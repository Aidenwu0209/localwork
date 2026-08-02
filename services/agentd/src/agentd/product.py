"""Read-only daily product API with explicit local safety boundaries."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import stat
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from agentd.config import Settings

_TIMELINE_FILTERS = frozenset(
    {"limit", "cursor", "date_from", "date_to", "app", "query"}
)
_EVIDENCE_INSUFFICIENT = (
    "I don't have sufficient verified evidence to answer that safely."
)
_CAPTURE_FRESH_SECONDS = 120.0
_CAPABILITY_TTL_SECONDS = 300
_CURSOR_TTL_SECONDS = 900
_PROFILE_FIELDS = (
    "enabled",
    "paused",
    "pending",
    "failed",
    "last_success",
    "covered_session_start",
    "covered_session_end",
)
_WEB_ROOT = Path(__file__).with_name("web")


class ProductStoreProtocol(Protocol):
    def database_ready(self) -> bool: ...

    def list_timeline(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_evidence(self, event_id: int) -> dict[str, Any] | None: ...

    def privacy_summary(self) -> dict[str, Any]: ...


class ProductStore:
    """Small query layer whose public rows are filtered again at the API edge."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def database_ready(self) -> bool:
        try:
            with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() == (1,)
        except psycopg.Error:
            return False

    def list_timeline(
        self,
        *,
        limit: int,
        position: tuple[str, int] | None,
        date_from: date | None,
        date_to: date | None,
        app: str | None,
        query: str | None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[object] = []
        if position is not None:
            clauses.append("(ts, id) < (%s::timestamptz, %s)")
            params.extend(position)
        if date_from is not None:
            clauses.append("ts >= %s::date")
            params.append(date_from.isoformat())
        if date_to is not None:
            clauses.append("ts < (%s::date + interval '1 day')")
            params.append(date_to.isoformat())
        if app is not None:
            clauses.append("app = %s")
            params.append(app)
        if query is not None:
            clauses.append("activity ILIKE %s")
            params.append(f"%{query}%")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"""
            SELECT id, ts, end_ts, kind, app, activity, topics
            FROM timeline_events
            {where}
            ORDER BY ts DESC, id DESC
            LIMIT %s
        """
        params.append(limit + 1)
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            {
                "id": int(row[0]),
                "ts": row[1].isoformat(),
                "end_ts": row[2].isoformat() if row[2] is not None else None,
                "kind": row[3],
                "app": row[4],
                "activity": row[5],
                "topics": list(row[6] or []),
            }
            for row in rows
        ]
        next_position = None
        if has_more and rows:
            next_position = (rows[-1][1].isoformat(), int(rows[-1][0]))
        return {"items": items, "next_position": next_position}

    def get_evidence(self, event_id: int) -> dict[str, Any] | None:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, ts, app, activity, topics, ocr_blocks,
                          screenshot_path
                   FROM timeline_events WHERE id = %s""",
                (event_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": int(row[0]),
            "ts": row[1].isoformat(),
            "app": row[2],
            "activity": row[3],
            "topics": list(row[4] or []),
            "ocr_blocks": row[5] or [],
            "screenshot_path": row[6],
        }

    def privacy_summary(self) -> dict[str, Any]:
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT decision, category, reason, COUNT(*)
                   FROM sentinel_audit
                   GROUP BY decision, category, reason"""
            )
            rows = cur.fetchall()
        total = allowed = blocked = 0
        categories: dict[str, int] = {}
        reasons: dict[str, int] = {}
        for decision, category, reason, raw_count in rows:
            count = int(raw_count)
            total += count
            allowed += count if decision == "allow" else 0
            blocked += count if decision == "block" else 0
            categories[str(category)] = categories.get(str(category), 0) + count
            reasons[str(reason)] = reasons.get(str(reason), 0) + count
        return {
            "total": total,
            "allowed": allowed,
            "blocked": blocked,
            "categories": categories,
            "reasons": reasons,
        }


class ProductAPI:
    def __init__(
        self,
        settings: Settings,
        *,
        store: ProductStoreProtocol,
        client_factory: Callable[..., Any],
        clock: Callable[[], datetime],
        ask_chat: Callable[[str], Awaitable[JSONResponse]],
    ) -> None:
        self.settings = settings
        self.store = store
        self.client_factory = client_factory
        self.clock = clock
        self.ask_chat = ask_chat
        self.last_compute: dict[str, Any] | None = None
        self.tokens = _TokenSigner(clock=clock)
        self.csrf_token = secrets.token_urlsafe(32)

    def status(self) -> dict[str, Any]:
        checked_at = self.clock().astimezone(timezone.utc)
        database_state = "ready" if self.store.database_ready() else "offline"
        memoryd_state = "offline"
        capture: dict[str, Any] = {
            "state": "unknown",
            "last_heartbeat": None,
            "age_seconds": None,
        }
        try:
            with self.client_factory(timeout=httpx.Timeout(3.0, connect=1.0)) as client:
                health = client.get(f"{self.settings.memoryd_url.rstrip('/')}/health")
                health.raise_for_status()
                health_body = health.json()
                memoryd_state = (
                    "ready"
                    if isinstance(health_body, dict)
                    and health_body.get("status") == "ok"
                    and health_body.get("accepting_frames") is True
                    else "degraded"
                )
                metrics = client.get(f"{self.settings.memoryd_url.rstrip('/')}/metrics")
                metrics.raise_for_status()
                capture = _capture_status(metrics.text, checked_at)
        except (httpx.HTTPError, TypeError, ValueError):
            memoryd_state = "offline"

        compute = self.last_compute or {
            "state": "unknown",
            "backend": None,
            "physical_model": None,
            "logical_model": "brain",
            "degraded": None,
            "reason": "no_verified_inference",
            "last_success": None,
        }
        if database_state == "offline" or memoryd_state == "offline":
            overall = "offline"
        elif capture["state"] == "stale" or compute["state"] == "degraded":
            overall = "degraded"
        elif capture["state"] != "ready" or compute["state"] != "ready":
            overall = "unknown"
        else:
            overall = "ready"
        return {
            "overall": overall,
            "data_sovereignty": "local_only",
            "compute": compute,
            "capture": capture,
            "components": {
                "database": {"state": database_state},
                "memoryd": {"state": memoryd_state},
            },
            "last_checked_at": checked_at.isoformat(),
        }

    async def ask(self, question: str) -> dict[str, Any]:
        response = await self.ask_chat(question)
        try:
            product = json.loads(bytes(response.body))
            answer = product["choices"][0]["message"]["content"]
            metadata = product["dejaview"]
            raw_citations = metadata["citations"]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail={"code": "answer_unavailable"}
            ) from exc
        if not isinstance(answer, str) or not isinstance(raw_citations, list):
            raise HTTPException(503, detail={"code": "answer_unavailable"})
        compute = _safe_compute(metadata)
        self.last_compute = {
            **compute,
            "state": "degraded" if compute["degraded"] else "ready",
            "last_success": self.clock().astimezone(timezone.utc).isoformat(),
        }
        citations = []
        for citation in raw_citations:
            if not isinstance(citation, dict):
                continue
            event_id = citation.get("event_id")
            label = citation.get("label")
            if isinstance(event_id, int) and not isinstance(event_id, bool) and isinstance(label, str):
                citations.append(
                    {
                        "event_id": event_id,
                        "label": label,
                        "evidence_url": (
                            f"/api/evidence/{event_id}?cap="
                            f"{self.tokens.evidence(event_id)}"
                        ),
                    }
                )
        return {
            "answer": answer,
            "citations": citations,
            "compute": compute,
            "evidence_insufficient": answer == _EVIDENCE_INSUFFICIENT,
        }


def install_product_routes(
    app: FastAPI,
    settings: Settings,
    *,
    store: ProductStoreProtocol | None,
    client_factory: Callable[..., Any],
    clock: Callable[[], datetime],
    ask_chat: Callable[[str], Awaitable[JSONResponse]],
) -> None:
    product = ProductAPI(
        settings,
        store=store or ProductStore(settings.timeline_db_url),
        client_factory=client_factory,
        clock=clock,
        ask_chat=ask_chat,
    )

    @app.get("/", include_in_schema=False)
    async def product_home() -> FileResponse:
        return _web_asset("index.html", "text/html; charset=utf-8")

    @app.get("/product.css", include_in_schema=False)
    async def product_css() -> FileResponse:
        return _web_asset("product.css", "text/css; charset=utf-8")

    @app.get("/product.js", include_in_schema=False)
    async def product_javascript() -> FileResponse:
        return _web_asset("product.js", "text/javascript; charset=utf-8")

    @app.get("/product-focus.mjs", include_in_schema=False)
    async def product_focus_javascript() -> FileResponse:
        return _web_asset("product-focus.mjs", "text/javascript; charset=utf-8")

    @app.get("/api/status")
    async def product_status() -> dict[str, Any]:
        return product.status()

    @app.get("/api/session")
    async def product_session() -> JSONResponse:
        response = JSONResponse({"csrf_token": product.csrf_token})
        response.set_cookie(
            "dejaview_csrf",
            product.csrf_token,
            secure=False,
            httponly=False,
            samesite="strict",
            path="/",
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/api/timeline")
    async def product_timeline(request: Request) -> dict[str, Any]:
        try:
            values = _timeline_query(request)
            cursor = values.pop("cursor")
            binding = _timeline_filter_binding(values)
            values["position"] = (
                product.tokens.cursor_position(cursor, binding=binding)
                if cursor is not None
                else None
            )
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail={"code": "invalid_timeline_query"}
            ) from exc
        try:
            page = product.store.list_timeline(**values)
            items = []
            for item in page["items"]:
                safe_item = _safe_timeline_item(item)
                event_id = safe_item["id"]
                if isinstance(event_id, bool) or not isinstance(event_id, int):
                    raise ValueError("invalid event id")
                capability = product.tokens.evidence(event_id)
                safe_item["evidence"] = {
                    "available": True,
                    "url": f"/api/evidence/{event_id}?cap={capability}",
                }
                items.append(safe_item)
            next_position = page.get("next_position")
            next_cursor = (
                product.tokens.cursor(next_position, binding=binding)
                if next_position
                else None
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail={"code": "timeline_unavailable"}
            ) from exc
        return {"items": items, "next_cursor": next_cursor}

    @app.post("/api/ask")
    async def product_ask(request: Request) -> dict[str, Any]:
        _require_same_origin_json(request, product)
        question = await _validated_body_text(
            request, field="question", error_code="invalid_question", max_length=2000
        )
        return await product.ask(question)

    @app.get("/api/evidence/{event_id}")
    async def product_evidence(event_id: int, request: Request) -> JSONResponse:
        _require_evidence_capability(request, product, event_id)
        event = _event_or_404(product.store, event_id)
        image_path = _contained_image_path(settings.data_root, event.get("screenshot_path"))
        capability = request.query_params["cap"]
        response = JSONResponse({
            "event_id": event_id,
            "ts": event.get("ts"),
            "app": event.get("app"),
            "activity": event.get("activity"),
            "topics": list(event.get("topics") or []),
            "highlights": _safe_highlights(event.get("ocr_blocks")),
            "image": {
                "available": image_path is not None,
                "url": (
                    f"/api/evidence/{event_id}/image?cap={capability}"
                    if image_path
                    else None
                ),
            },
        })
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/api/evidence/{event_id}/image")
    async def product_evidence_image(event_id: int, request: Request) -> StreamingResponse:
        _require_evidence_capability(request, product, event_id)
        event = _event_or_404(product.store, event_id)
        image_path = _contained_image_path(settings.data_root, event.get("screenshot_path"))
        if image_path is None:
            raise HTTPException(
                status_code=404, detail={"code": "evidence_image_unavailable"}
            )
        file_descriptor = _open_regular_nofollow(image_path)
        return StreamingResponse(
            _file_chunks(file_descriptor),
            media_type=_image_media_type(image_path),
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/privacy/summary")
    async def product_privacy_summary() -> dict[str, Any]:
        try:
            summary = product.store.privacy_summary()
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail={"code": "privacy_summary_unavailable"}
            ) from exc
        return {
            "total": int(summary.get("total") or 0),
            "allowed": int(summary.get("allowed") or 0),
            "blocked": int(summary.get("blocked") or 0),
            "categories": dict(summary.get("categories") or {}),
            "reasons": dict(summary.get("reasons") or {}),
            "blocked_pixels_exposed": 0,
        }

    @app.get("/api/profile/status")
    async def product_profile_status() -> dict[str, Any]:
        return _profile_status(product)

    @app.post("/api/profile/query")
    async def product_profile_query(request: Request) -> dict[str, Any]:
        _require_same_origin_json(request, product)
        question = await _validated_body_text(
            request,
            field="question",
            error_code="invalid_profile_query",
            max_length=2000,
        )
        try:
            with product.client_factory(
                timeout=httpx.Timeout(120.0, connect=3.0)
            ) as client:
                response = client.post(
                    f"{settings.honcho_url.rstrip('/')}/v3/workspaces/dejaview/peers/owner/chat",
                    json={"query": question},
                )
                response.raise_for_status()
                body = response.json()
            answer = body.get("content") if isinstance(body, dict) else None
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("missing answer")
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail={"code": "profile_unavailable"}
            ) from exc
        return {
            "answer": answer[:2000],
            "provenance": {
                "source": "honcho_local_projection",
                "workspace": "dejaview",
                "peer": "owner",
            },
        }

    for action in ("pause", "resume"):
        _install_profile_control(app, product, action)


def _web_asset(name: str, media_type: str) -> FileResponse:
    return FileResponse(
        _WEB_ROOT / name,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _install_profile_control(app: FastAPI, product: ProductAPI, action: str) -> None:
    async def control(request: Request) -> dict[str, bool]:
        _require_same_origin_json(request, product)
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail={"code": "confirmation_required"}
            ) from exc
        if body != {"confirm": True}:
            raise HTTPException(422, detail={"code": "confirmation_required"})
        try:
            with product.client_factory(
                timeout=httpx.Timeout(5.0, connect=1.0)
            ) as client:
                response = client.post(
                    f"{product.settings.memoryd_url.rstrip('/')}/v1/profile/{action}"
                )
                response.raise_for_status()
                result = response.json()
            enabled = result.get("enabled") if isinstance(result, dict) else None
            paused = result.get("paused") if isinstance(result, dict) else None
            if (
                not isinstance(result, dict)
                or set(result) != {"enabled", "paused"}
                or not isinstance(enabled, bool)
                or not isinstance(paused, bool)
            ):
                raise ValueError("invalid profile state")
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail={"code": "profile_unavailable"}
            ) from exc
        return {"enabled": enabled, "paused": paused}

    app.add_api_route(f"/api/profile/{action}", control, methods=["POST"])


def _profile_status(product: ProductAPI) -> dict[str, Any]:
    try:
        with product.client_factory(
            timeout=httpx.Timeout(5.0, connect=1.0)
        ) as client:
            response = client.get(
                f"{product.settings.memoryd_url.rstrip('/')}/v1/profile/status"
            )
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, dict):
            raise ValueError("invalid profile state")
        if set(body) != set(_PROFILE_FIELDS):
            raise ValueError("invalid profile state")
        return {field: body.get(field) for field in _PROFILE_FIELDS}
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail={"code": "profile_unavailable"}
        ) from exc


async def _validated_body_text(
    request: Request, *, field: str, error_code: str, max_length: int
) -> str:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail={"code": error_code}) from exc
    if not isinstance(body, dict) or set(body) != {field}:
        raise HTTPException(422, detail={"code": error_code})
    value = body.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise HTTPException(422, detail={"code": error_code})
    return value.strip()


def _timeline_query(request: Request) -> dict[str, Any]:
    params = request.query_params
    if any(key not in _TIMELINE_FILTERS for key in params):
        raise HTTPException(422, detail={"code": "invalid_timeline_query"})
    if any(len(params.getlist(key)) != 1 for key in params):
        raise HTTPException(422, detail={"code": "invalid_timeline_query"})
    try:
        limit = int(params.get("limit", "20"))
        if not 1 <= limit <= 50:
            raise ValueError("limit")
        date_from = date.fromisoformat(params["date_from"]) if "date_from" in params else None
        date_to = date.fromisoformat(params["date_to"]) if "date_to" in params else None
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ValueError("date range")
        app = _optional_filter(params.get("app"), 128)
        query = _optional_filter(params.get("query"), 200)
        cursor = params["cursor"] if "cursor" in params else None
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_timeline_query"}
        ) from exc
    return {
        "limit": limit,
        "cursor": cursor,
        "date_from": date_from,
        "date_to": date_to,
        "app": app,
        "query": query,
    }


def _optional_filter(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    if not value.strip() or len(value) > max_length or any(ord(char) < 32 for char in value):
        raise ValueError("invalid filter")
    return value.strip()


def _timeline_filter_binding(values: dict[str, Any]) -> str:
    canonical = {
        "date_from": values["date_from"].isoformat() if values["date_from"] else None,
        "date_to": values["date_to"].isoformat() if values["date_to"] else None,
        "app": values["app"],
        "query": values["query"],
        "sort": "ts_desc_id_desc",
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _safe_timeline_item(item: object) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("invalid timeline row")
    return {
        "id": item.get("id"),
        "ts": item.get("ts"),
        "end_ts": item.get("end_ts"),
        "kind": item.get("kind"),
        "app": item.get("app"),
        "activity": item.get("activity"),
        "topics": list(item.get("topics") or []),
    }


def _safe_compute(metadata: object) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise HTTPException(503, detail={"code": "answer_unavailable"})
    return {
        "backend": metadata.get("backend"),
        "physical_model": metadata.get("physical_model"),
        "logical_model": metadata.get("logical_model"),
        "degraded": metadata.get("degraded") is True,
        "reason": metadata.get("reason"),
        "latency_ms": metadata.get("latency_ms"),
    }


def _event_or_404(store: ProductStoreProtocol, event_id: int) -> dict[str, Any]:
    if event_id <= 0:
        raise HTTPException(404, detail={"code": "evidence_not_found"})
    try:
        event = store.get_evidence(event_id)
    except Exception as exc:
        raise HTTPException(503, detail={"code": "evidence_unavailable"}) from exc
    if not isinstance(event, dict):
        raise HTTPException(404, detail={"code": "evidence_not_found"})
    return event


def _safe_highlights(raw_blocks: object) -> list[dict[str, list[int | float]]]:
    if not isinstance(raw_blocks, list):
        return []
    highlights = []
    for block in raw_blocks[:100]:
        bbox = block.get("bbox") if isinstance(block, dict) else None
        if (
            isinstance(bbox, list)
            and len(bbox) == 4
            and all(
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
                for value in bbox
            )
        ):
            highlights.append({"bbox": bbox})
    return highlights


def _contained_image_path(data_root: Path, stored_path: object) -> Path | None:
    if not isinstance(stored_path, str) or not stored_path:
        return None
    raw = Path(stored_path)
    root = data_root / "screenshots"
    if not raw.is_absolute() or ".." in raw.parts or not root.exists() or root.is_symlink():
        return None
    try:
        relative = raw.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return None
        resolved_root = root.resolve(strict=True)
        resolved = raw.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        return None
    if resolved.suffix.lower() not in {".webp", ".png", ".jpg", ".jpeg"}:
        return None
    return resolved


def _open_regular_nofollow(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("not regular")
        current = os.stat(path, follow_symlinks=False)
        if (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino):
            raise OSError("file changed")
        return descriptor
    except OSError as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise HTTPException(
            status_code=404, detail={"code": "evidence_image_unavailable"}
        ) from exc


def _file_chunks(descriptor: int):
    with os.fdopen(descriptor, "rb") as image:
        while chunk := image.read(64 * 1024):
            yield chunk


def _image_media_type(path: Path) -> str:
    return {
        ".webp": "image/webp",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }[path.suffix.lower()]


def _capture_status(metrics: str, now: datetime) -> dict[str, Any]:
    prefix = "dejaview_capture_last_heartbeat_unixtime "
    raw = next((line[len(prefix) :] for line in metrics.splitlines() if line.startswith(prefix)), None)
    try:
        timestamp = float(raw) if raw is not None else 0.0
    except ValueError:
        timestamp = 0.0
    if not math.isfinite(timestamp) or timestamp <= 0:
        return {"state": "unknown", "last_heartbeat": None, "age_seconds": None}
    heartbeat = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    age = (now - heartbeat).total_seconds()
    if age < -60:
        return {"state": "unknown", "last_heartbeat": heartbeat.isoformat(), "age_seconds": None}
    age = max(0.0, age)
    return {
        "state": "ready" if age <= _CAPTURE_FRESH_SECONDS else "stale",
        "last_heartbeat": heartbeat.isoformat(),
        "age_seconds": round(age, 1),
    }


class _TokenSigner:
    def __init__(self, *, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._key = secrets.token_bytes(32)

    def evidence(self, event_id: int) -> str:
        return self._mint(
            {
                "kind": "evidence",
                "event_id": event_id,
                "exp": int(self._clock().timestamp()) + _CAPABILITY_TTL_SECONDS,
            }
        )

    def authorize_evidence(self, token: str, event_id: int) -> bool:
        payload = self._verify(token)
        return (
            payload is not None
            and payload.get("kind") == "evidence"
            and payload.get("event_id") == event_id
        )

    def cursor(self, position: tuple[str, int], *, binding: str) -> str:
        return self._mint(
            {
                "kind": "cursor",
                "position": [position[0], position[1]],
                "binding": binding,
                "exp": int(self._clock().timestamp()) + _CURSOR_TTL_SECONDS,
            }
        )

    def cursor_position(self, token: str, *, binding: str) -> tuple[str, int]:
        payload = self._verify(token)
        position = payload.get("position") if payload else None
        if (
            payload is None
            or payload.get("kind") != "cursor"
            or payload.get("binding") != binding
            or not isinstance(position, list)
            or len(position) != 2
            or not isinstance(position[0], str)
            or isinstance(position[1], bool)
            or not isinstance(position[1], int)
            or position[1] <= 0
        ):
            raise ValueError("invalid cursor")
        datetime.fromisoformat(position[0].replace("Z", "+00:00"))
        return position[0], position[1]

    def _mint(self, payload: dict[str, Any]) -> str:
        encoded = _b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        signature = _b64encode(hmac.new(self._key, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def _verify(self, token: str) -> dict[str, Any] | None:
        if not isinstance(token, str) or len(token) > 1024 or token.count(".") != 1:
            return None
        encoded, supplied_signature = token.split(".", 1)
        expected = _b64encode(hmac.new(self._key, encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, supplied_signature):
            return None
        try:
            payload = json.loads(_b64decode(encoded))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        expiry = payload.get("exp")
        if isinstance(expiry, bool) or not isinstance(expiry, int):
            return None
        if expiry < int(self._clock().timestamp()):
            return None
        return payload


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _require_evidence_capability(
    request: Request, product: ProductAPI, event_id: int
) -> None:
    capability = request.query_params.get("cap", "")
    if not product.tokens.authorize_evidence(capability, event_id):
        raise HTTPException(404, detail={"code": "evidence_not_found"})


def _require_same_origin_json(request: Request, product: ProductAPI) -> None:
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    expected_origin = f"{request.url.scheme}://{host}" if host else ""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    header_token = request.headers.get("x-dejaview-csrf", "")
    cookie_token = request.cookies.get("dejaview_csrf", "")
    if (
        not origin
        or origin.rstrip("/") != expected_origin.rstrip("/")
        or content_type != "application/json"
        or not header_token
        or not cookie_token
        or not hmac.compare_digest(header_token, product.csrf_token)
        or not hmac.compare_digest(cookie_token, product.csrf_token)
    ):
        raise HTTPException(403, detail={"code": "same_origin_required"})
