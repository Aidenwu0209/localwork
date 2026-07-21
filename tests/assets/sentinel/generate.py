#!/usr/bin/env python3
"""Generate the synthetic sentinel test set (M6.3).

40 fully-synthetic screenshots for validating the privacy sentinel's recall
(block rate on sensitive frames) and precision (no false blocks on normal
frames). Each PNG is paired with a JSON giving the expected category + decision.

Sensitive (decision=block), 4 categories × 5 = 20:
  banking_finance  — fake bank login / account pages (masked PAN, $ balances)
  password_prompt  — password fields (dot-masked), password-manager UI
  private_chat     — DM-style chat with private content
  id_document      — ID-card / passport form fields (fake names + numbers)

Normal (decision=allow), 20:
  code / docs / terminal / public-webpage scenes with NO sensitive content.

All names, accounts, numbers, hosts are fictional. No real PII.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
W, H = 1920, 1080

FONT_MONO = "/System/Library/Fonts/Menlo.ttc"
FONT_SANS = "/System/Library/Fonts/Helvetica.ttc"
FONT_CJK = "/System/Library/Fonts/STHeiti Medium.ttc"

_FONT_CACHE: dict[tuple, ImageFont.FreeTypeFont] = {}


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = ImageFont.truetype(path, size)
        except OSError:
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def tw(d: ImageDraw.ImageDraw, txt: str, fnt: ImageFont.FreeTypeFont) -> int:
    return int(d.textlength(txt, font=fnt))


def mono(size: int) -> ImageFont.FreeTypeFont:
    return font(FONT_MONO, size)


def sans(size: int) -> ImageFont.FreeTypeFont:
    return font(FONT_SANS, size)


def cjk(size: int) -> ImageFont.FreeTypeFont:
    return font(FONT_CJK, size)


def text(d, xy, txt, fnt, fill):
    d.text(xy, txt, font=fnt, fill=fill)


def rect(d, box, color, outline=None, width=1):
    d.rectangle(box, fill=color, outline=outline, width=width)


# Palette — neutral web UI greys so the test is about content, not chrome.
GREY_BG = (243, 244, 246)
GREY_CARD = (255, 255, 255)
GREY_LINE = (209, 213, 219)
GREY_TEXT = (55, 65, 81)
GREY_MUTED = (107, 114, 128)
BLUE = (37, 99, 235)
BLUE_DARK = (29, 78, 216)
RED = (220, 38, 38)
GREEN = (5, 150, 105)
BLACK = (17, 24, 39)


def base_page(bg=GREY_BG) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), bg)
    return img, ImageDraw.Draw(img)


def browser_chrome(d, url: str):
    """Draw a minimal browser top bar + address bar."""
    rect(d, (0, 0, W, 64), (52, 52, 52))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse((20 + i * 24, 22, 36 + i * 24, 38), fill=c)
    rect(d, (120, 16, W - 120, 48), (38, 38, 38), outline=(80, 80, 80))
    text(d, (140, 24), url, sans(16), (220, 220, 220))


# --------------------------------------------------------------------------- #
# SENSITIVE: banking_finance
# --------------------------------------------------------------------------- #
def render_banking(idx: int):
    img, d = base_page()
    browser_chrome(d, "https://acme-bank.demo/login")
    # Logo + header
    rect(d, (0, 64, W, 130), BLUE_DARK)
    text(d, (120, 84), "Acme Bank", sans(32), (255, 255, 255))
    text(d, (320, 96), "Demo Credit Union", sans(18), (200, 220, 255))
    # Login card
    rect(d, (610, 200, 1310, 560), GREY_CARD, outline=GREY_LINE, width=1)
    text(d, (660, 230), "Sign in to your account", sans(26), BLACK)
    text(d, (660, 280), "User ID", sans(15), GREY_MUTED)
    rect(d, (660, 305, 1260, 345), GREY_BG, outline=GREY_LINE)
    text(d, (672, 315), "jordan.lee.demo", sans(18), GREY_TEXT)
    text(d, (660, 370), "Password", sans(15), GREY_MUTED)
    rect(d, (660, 395, 1260, 435), GREY_BG, outline=GREY_LINE)
    text(d, (672, 405), "•" * 12, mono(20), GREY_TEXT)
    rect(d, (660, 460, 880, 500), BLUE, outline=BLUE)
    text(d, (700, 470), "Sign in", sans(18), (255, 255, 255))
    # Account preview (masked balances)
    rect(d, (610, 600, 1310, 760), GREY_CARD, outline=GREY_LINE)
    text(d, (660, 620), "Checking ****1234", sans(18), GREY_TEXT)
    text(d, (660, 650), "$12,345.67", mono(24), GREEN)
    text(d, (960, 620), "Savings ****5678", sans(18), GREY_TEXT)
    text(d, (960, 650), "$48,901.22", mono(24), GREEN)
    text(d, (660, 710), "Loan ****9012  balance $84,200.00", sans(16), GREY_MUTED)
    img.save(OUT / f"banking_{idx:02d}.png")


def render_password(idx: int):
    img, d = base_page()
    browser_chrome(d, "https://vault.demo-acme.io/unlock")
    rect(d, (610, 200, 1310, 560), GREY_CARD, outline=GREY_LINE)
    text(d, (660, 230), "Enter Master Password", sans(26), BLACK)
    text(d, (660, 280), "Vault Demo — your passwords are encrypted locally.",
         sans(15), GREY_MUTED)
    rect(d, (660, 320, 1260, 380), GREY_BG, outline=BLUE, width=2)
    text(d, (672, 335), "•" * 18, mono(28), GREY_TEXT)
    text(d, (672, 365), "password hidden — type to reveal", sans(13), GREY_MUTED)
    rect(d, (660, 410, 880, 450), BLUE)
    text(d, (700, 420), "Unlock", sans(18), (255, 255, 255))
    # Password-manager entry list (masked)
    rect(d, (610, 600, 1310, 900), GREY_CARD, outline=GREY_LINE)
    text(d, (660, 620), "Demo Vault entries (masked)", sans(18), GREY_TEXT)
    for i, row in enumerate(
        ["github.com            ••••••••••••    jordan_demo",
         "docs.demo-acme.io     ••••••••••••    jordan@demo",
         "grafana.northwind     ••••••••••••    admin"]
    ):
        text(d, (660, 660 + i * 36), row, mono(16), GREY_TEXT)
    img.save(OUT / f"password_{idx:02d}.png")


def render_private_chat(idx: int):
    img, d = base_page(bg=(30, 30, 30))
    browser_chrome(d, "https://app.demoslack.io/dm/alex_w")
    # Sidebar
    rect(d, (0, 64, 380, H), (37, 37, 38))
    text(d, (20, 84), "Direct Messages", sans(18), (200, 200, 200))
    for i, name in enumerate(["alex_w", "sam_qa", "morgan_dev", "priya_eng"]):
        col = (255, 255, 255) if i == 0 else (150, 150, 150)
        text(d, (20, 130 + i * 36), f"#  {name}", sans(16), col)
    # DM thread (private content)
    text(d, (420, 90), "# alex_w", sans(20), (255, 255, 255))
    msgs = [
        ("alex_w", "9:41", "hey, sending you the contract draft now — keep it private"),
        ("alex_w", "9:42", "the salary band is 140-160k, please don't share externally"),
        ("jordan", "9:43", "got it, won't forward. what's the offer deadline?"),
        ("alex_w", "9:44", "friday. also the client's real name is masked in the doc"),
    ]
    palette = {"alex_w": (235, 200, 120), "jordan": (130, 200, 255)}
    for i, (who, ts, body) in enumerate(msgs):
        y = 140 + i * 90
        text(d, (420, y), who, sans(16), palette[who])
        text(d, (560, y), ts, sans(14), (130, 130, 130))
        rect(d, (420, y + 24, 1400, y + 70), (52, 52, 54))
        text(d, (436, y + 32), body, sans(16), (230, 230, 230))
    img.save(OUT / f"private_chat_{idx:02d}.png")


def render_id_document(idx: int):
    img, d = base_page()
    browser_chrome(d, "https://kyc.demo-verify.io/upload")
    rect(d, (510, 150, 1410, 820), GREY_CARD, outline=GREY_LINE)
    text(d, (560, 180), "Identity Verification (Demo KYC)", sans(26), BLACK)
    text(d, (560, 220), "All data is fictional — for testing only.", sans(14), GREY_MUTED)
    # ID-card mock
    rect(d, (560, 260, 1360, 520), (250, 240, 210), outline=GREY_LINE)
    text(d, (580, 280), "DEMO REPUBLIC  —  NATIONAL ID (SPECIMEN)", sans(16), (120, 80, 40))
    rect(d, (600, 320, 780, 480), (220, 210, 180))  # photo placeholder
    text(d, (640, 390), "[photo]", sans(16), (140, 110, 70))
    rows = [
        ("Name",        "JORDAN LEE"),
        ("ID Number",   "S-1234567-D"),
        ("DOB",         "1996-04-12"),
        ("Issued",      "2021-08-01  ·  Expires 2031-07-31"),
    ]
    for i, (k, v) in enumerate(rows):
        y = 330 + i * 42
        text(d, (820, y), k, sans(15), GREY_MUTED)
        text(d, (980, y), v, mono(18), BLACK)
    # Form fields below
    text(d, (560, 570), "Confirm details", sans(18), BLACK)
    for i, (lbl, val) in enumerate(
        [("Full legal name", "Jordan Lee"), ("Document number", "S1234567D"),
         ("Address", "1 Demo Street, Singapore 048583")]
    ):
        y = 610 + i * 56
        text(d, (560, y), lbl, sans(14), GREY_MUTED)
        rect(d, (560, y + 20, 1360, y + 52), GREY_BG, outline=GREY_LINE)
        text(d, (572, y + 26), val, mono(16), GREY_TEXT)
    img.save(OUT / f"id_document_{idx:02d}.png")


# --------------------------------------------------------------------------- #
# NORMAL: 20 frames with no sensitive content (decision=allow)
# --------------------------------------------------------------------------- #
def render_normal_code(idx: int):
    img, d = base_page(bg=(30, 30, 30))
    rect(d, (0, 0, W, 36), (50, 50, 50))
    text(d, (20, 8), "main.py — dejaview-demo", mono(14), (210, 210, 210))
    lines = [
        'from fastapi import FastAPI',
        'app = FastAPI()',
        '',
        '@app.get("/health")',
        'def health():',
        '    return {"status": "ok"}',
        '',
        '# TODO: wire pipeline stages here',
    ]
    for i, ln in enumerate(lines):
        text(d, (60, 60 + i * 24), ln, mono(16), (212, 212, 212))
    img.save(OUT / f"normal_code_{idx:02d}.png")


def render_normal_doc(idx: int):
    img, d = base_page()
    browser_chrome(d, "https://docs.demo-acme.io/architecture")
    text(d, (120, 110), "DejaView Architecture Overview", sans(28), BLACK)
    paras = [
        "The system is split into three planes: sensor (Mac/Win capture),",
        "data-sovereignty (Mac, stateful), and compute (AMD server, stateless).",
        "",
        "All inference runs on a single Radeon PRO W7900D via ROCm. Logical",
        "model names route through a LiteLLM gateway; app code never sees the",
        "physical backend.",
    ]
    for i, ln in enumerate(paras):
        text(d, (120, 160 + i * 30), ln, sans(18), GREY_TEXT)
    img.save(OUT / f"normal_doc_{idx:02d}.png")


def render_normal_terminal(idx: int):
    img, d = base_page(bg=(20, 20, 20))
    text(d, (20, 20), "jordan@demo:~/dejaview-demo$", mono(16), (150, 200, 150))
    lines = [
        "$ make data-up",
        "[+] Running 2/2  ✔ database healthy  ✔ redis healthy",
        "$ uv run python -m memoryd",
        "INFO:     Uvicorn running on http://127.0.0.1:8090",
        "INFO:     127.0.0.1 - GET /health 200 OK",
    ]
    for i, ln in enumerate(lines):
        text(d, (20, 60 + i * 26), ln, mono(15), (210, 210, 210))
    img.save(OUT / f"normal_terminal_{idx:02d}.png")


def render_normal_web(idx: int):
    img, d = base_page()
    browser_chrome(d, "https://blog.demo-acme.io/local-first")
    text(d, (120, 110), "Why local-first matters", sans(28), BLACK)
    text(d, (120, 150), "Posted 2026-07-15 · 8 min read · #142 shares", sans(14), GREY_MUTED)
    body = (
        "Local-first software keeps user data on the user's device. The cloud\n"
        "becomes an optional sync target, not the source of truth.\n\n"
        "本地的优先 (local-first) 把数据主权还给用户。云端只是可选的同步目标。"
    )
    for i, ln in enumerate(body.split("\n")):
        text(d, (120, 200 + i * 30), ln, sans(18), GREY_TEXT)
    img.save(OUT / f"normal_web_{idx:02d}.png")


def main():
    # Sensitive — 4 categories × 5 = 20. Map the verdict category (handbook
    # sentinel taxonomy) to the file prefix used by the render functions.
    sensitive = [
        ("banking_finance", "banking", render_banking),
        ("password_prompt", "password", render_password),
        ("private_chat", "private_chat", render_private_chat),
        ("id_document", "id_document", render_id_document),
    ]
    for cat, prefix, fn in sensitive:
        for i in range(1, 6):
            fn(i)
            fname = f"{prefix}_{i:02d}"
            (OUT / f"{fname}.json").write_text(json.dumps({
                "category": cat,
                "expected_decision": "block",
                "confidence_min": 0.7,
                "notes": f"fictional {cat} — no real PII",
                "image": f"{fname}.png",
            }, indent=2) + "\n")

    # Normal — 20 across 4 scenes (5 each). Each scene writes its own scene-
    # prefixed files, which we keep as-is (clearer than renumbering). JSON
    # references the same scene-prefixed name.
    normal_scenes = [
        ("normal_code", render_normal_code),
        ("normal_doc", render_normal_doc),
        ("normal_terminal", render_normal_terminal),
        ("normal_web", render_normal_web),
    ]
    for prefix, fn in normal_scenes:
        for i in range(1, 6):
            fn(i)
            (OUT / f"{prefix}_{i:02d}.json").write_text(json.dumps({
                "category": "normal",
                "expected_decision": "allow",
                "confidence_min": 0.5,
                "notes": "ordinary working content, no sensitive data",
                "image": f"{prefix}_{i:02d}.png",
            }, indent=2) + "\n")

    print(f"Generated sentinel test set in {OUT}")
    pngs = sorted(OUT.glob("*.png"))
    jsons = sorted(OUT.glob("*.json"))
    print(f"  PNGs: {len(pngs)}  (expect 40)")
    print(f"  JSONs: {len(jsons)}  (expect 40)")


if __name__ == "__main__":
    main()
