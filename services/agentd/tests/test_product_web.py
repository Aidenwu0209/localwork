"""Default daily-product shell accessibility and asset contracts."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re

from fastapi.testclient import TestClient

from agentd.config import Settings
from agentd.server import create_app


class ShellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted(
        (luminance(foreground), luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


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
    status_group = next(
        attrs
        for _tag, attrs in parser.tags
        if attrs.get("class") == "status-strip"
    )
    assert status_group.get("role") == "group"
    assert status_group.get("aria-label") == "System summary"
    assert any(attrs.get("aria-live") == "polite" for _tag, attrs in parser.tags)
    assert any(attrs.get("role") == "alert" for _tag, attrs in parser.tags)
    assert any(
        attrs.get("role") == "dialog"
        and attrs.get("aria-modal") == "true"
        and attrs.get("aria-describedby") == "evidence-message"
        for _tag, attrs in parser.tags
    )
    profile_question = next(
        attrs
        for _tag, attrs in parser.tags
        if attrs.get("id") == "profile-question"
    )
    assert "disabled" in profile_question
    for status_id in (
        "overall-status",
        "capture-status",
        "compute-status",
        "profile-status",
    ):
        attrs = next(attrs for _tag, attrs in parser.tags if attrs.get("id") == status_id)
        assert attrs.get("role") == "status"
        assert attrs.get("aria-live") == "polite"
    assert 'href="http://127.0.0.1:8120/"' in response.text
    assert "Local-only data" in response.text
    assert "Local data" in response.text
    assert "RUN REAL SENTINEL" not in response.text


def test_product_assets_are_module_based_no_store_and_responsive(tmp_path: Path) -> None:
    app = client(tmp_path)
    css = app.get("/product.css")
    script = app.get("/product.js")
    focus_script = app.get("/product-focus.mjs")
    html = app.get("/")

    assert css.status_code == script.status_code == focus_script.status_code == 200
    assert css.headers["cache-control"] == "no-store"
    assert script.headers["cache-control"] == "no-store"
    assert 'type="module" src="/product.js"' in html.text
    assert "@media (max-width: 1100px)" in css.text
    assert "@media (max-width: 800px)" in css.text
    assert "@media (max-width: 680px)" in css.text
    assert "@media (prefers-reduced-motion: reduce)" in css.text
    assert ":focus-visible" in css.text
    assert "overflow-x: clip" in css.text
    assert ".view-nav { flex-wrap: wrap; overflow-x: visible; }" in css.text


def test_product_typography_heading_order_and_backgrounds_are_auditable(
    tmp_path: Path,
) -> None:
    app = client(tmp_path)
    css = app.get("/product.css").text
    script = app.get("/product.js").text

    timeline_renderer = script.split("function renderTimelineItem", 1)[1].split(
        "async function loadTimeline", 1
    )[0]
    assert 'document.createElement("h2")' in timeline_renderer
    assert 'document.createElement("h3")' not in timeline_renderer
    assert 'byId("close-evidence").focus();' in script
    assert "setDialogBackgroundInert(dialogBackground, true);" in script
    assert "setDialogBackgroundInert(dialogBackground, false);" in script
    assert "setProfileControls(profileControls, { available: false });" in script
    assert "if (!shouldAnnounceStatus" in script
    assert "image.alt = evidenceImageAlt" in script
    assert re.search(r"body\s*\{[^}]*background:\s*var\(--paper\);", css, re.DOTALL)
    assert re.search(r"\.panel\s*\{[^}]*background:\s*#fffef9;", css, re.DOTALL)

    information_selectors = (
        ".brand-block p",
        ".status-chip",
        ".last-check",
        ".view-nav a",
        ".eyebrow",
        ".quiet-note, .notice",
        ".local-badge",
        "label",
        ".timeline-item header",
        ".timeline-item p",
        ".form-footer",
        ".text-button",
    )
    for selector in information_selectors:
        rules = re.findall(
            rf"(?m)^\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", css
        )
        assert rules, selector
        sizes = [
            value
            for rule in rules
            for value in re.findall(r"font-size:\s*([^;]+)", rule)
        ]
        assert sizes and all(value == "14px" for value in sizes), selector

    icon_rule = re.search(r"\.icon-button\s*\{([^}]*)\}", css).group(1)
    citation_rule = re.search(r"\.citation-list button\s*\{([^}]*)\}", css).group(1)
    assert "width: 44px" in icon_rule and "height: 44px" in icon_rule
    assert "min-width: 44px" in citation_rule
    assert "min-height: 44px" in citation_rule
    evidence_rule = re.search(r"\.evidence-button\s*\{([^}]*)\}", css).group(1)
    assert "min-width: 44px" in evidence_rule
    assert "min-height: 44px" in evidence_rule

    control_rule = re.search(r"(?m)^input, textarea\s*\{([^}]*)\}", css).group(1)
    placeholder_rule = re.search(
        r"(?m)^input::placeholder, textarea::placeholder\s*\{([^}]*)\}", css
    ).group(1)
    control_border = re.search(r"border:\s*1px solid (#[0-9a-fA-F]{6})", control_rule).group(1)
    placeholder = re.search(r"color:\s*(#[0-9a-fA-F]{6})", placeholder_rule).group(1)
    assert contrast_ratio(control_border, "#fffef9") >= 3
    assert contrast_ratio(placeholder, "#fffef9") >= 4.5
