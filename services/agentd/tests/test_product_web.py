"""Default daily-product shell accessibility and asset contracts."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

from agentd.config import Settings
from agentd.server import create_app


class ShellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        gateway_url="http://synthetic/v1",
        timeline_db_url="postgresql://synthetic/dejaview",
        honcho_url="http://synthetic-honcho",
        data_root=tmp_path,
    )
    return TestClient(create_app(settings=settings))


def test_default_route_is_accessible_daily_product_not_demo_stage(tmp_path: Path) -> None:
    response = client(tmp_path).get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    parser = ShellParser()
    parser.feed(response.text)

    assert any(
        tag == "a" and attrs.get("class") == "skip-link" and attrs.get("href") == "#main"
        for tag, attrs in parser.tags
    )
    assert any(tag == "main" and attrs.get("id") == "main" for tag, attrs in parser.tags)
    assert any(tag == "nav" and attrs.get("aria-label") for tag, attrs in parser.tags)
    assert any(attrs.get("aria-live") == "polite" for _tag, attrs in parser.tags)
    assert any(attrs.get("role") == "alert" for _tag, attrs in parser.tags)
    assert any(
        attrs.get("role") == "dialog" and attrs.get("aria-modal") == "true"
        for _tag, attrs in parser.tags
    )
    assert 'href="http://127.0.0.1:8120/"' in response.text
    assert "Local-only data" in response.text
    assert "Local data" in response.text
    assert "RUN REAL SENTINEL" not in response.text


def test_product_assets_are_module_based_no_store_and_responsive(tmp_path: Path) -> None:
    app = client(tmp_path)
    css = app.get("/product.css")
    script = app.get("/product.js")
    html = app.get("/")

    assert css.status_code == script.status_code == 200
    assert css.headers["cache-control"] == "no-store"
    assert script.headers["cache-control"] == "no-store"
    assert 'type="module" src="/product.js"' in html.text
    assert "@media (max-width: 1100px)" in css.text
    assert "@media (max-width: 680px)" in css.text
    assert "@media (prefers-reduced-motion: reduce)" in css.text
    assert ":focus-visible" in css.text
    assert "overflow-x: clip" in css.text
    assert ".view-nav { flex-wrap: wrap; overflow-x: visible; }" in css.text
