#!/usr/bin/env python3
"""Offline unit checks for P3.6/P3.7 parse hardening (no GPU required)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memoryd.stages import (  # noqa: E402
    _parse_perceive_json,
    _parse_sentinel_json,
)


def check_sentinel() -> None:
    # M4.4 failure mode: category=normal but decision=block → must ALLOW.
    v = _parse_sentinel_json(
        '{"decision":"block","category":"normal","confidence":0.9}'
    )
    assert v.decision == "allow" and v.category == "normal", v

    # AMD live miss: category correct but decision=allow → must BLOCK.
    v = _parse_sentinel_json(
        '{"decision":"allow","category":"banking_finance","confidence":0.9}'
    )
    assert v.decision == "block" and v.category == "banking_finance", v

    # Partial JSON (only decision) → fail-open allow/normal unless category token.
    v = _parse_sentinel_json('{"decision":"allow"}')
    assert v.decision == "allow" and v.category == "normal", v

    # Category-only JSON (new prompt shape).
    v = _parse_sentinel_json('{"category":"password_prompt","confidence":0.87}')
    assert v.decision == "block" and v.category == "password_prompt"
    assert abs(v.confidence - 0.87) < 1e-6
    v = _parse_sentinel_json('{"category":"banking_finance"}')
    assert v.decision == "block" and abs(v.confidence - 0.75) < 1e-6

    # Fenced + trailing comma.
    v = _parse_sentinel_json(
        '```json\n{"category":"private_chat","confidence":0.8,}\n```'
    )
    assert v.decision == "block" and v.category == "private_chat"

    # Alias normalisation.
    v = _parse_sentinel_json('{"category":"banking","confidence":0.7}')
    assert v.decision == "block" and v.category == "banking_finance"

    print("sentinel parse OK")


def check_perceive() -> None:
    ocr = (
        "def parse_timeline(self, events):\n"
        "from acme_parser import Tokenizer\n"
        "error: ROCM-4042: device lost\n"
        "https://docs.demo-acme.io/errors/ROCM-4042\n"
    )
    # Generic activity replaced; hallucinated verbatim dropped.
    ev = _parse_perceive_json(
        '{"activity":"working in VS Code","app_context":"ide",'
        '"topics":["parser"],'
        '"verbatim":{"errors":["ROCM-4042","FAKE-999"],'
        '"urls":["https://docs.demo-acme.io/errors/ROCM-4042","https://evil.example"],'
        '"identifiers":["parse_timeline","not_in_ocr"],'
        '"numbers":[],"quotes":[]}}',
        app="Code",
        window_title="timeline.py",
        ocr_full_text=ocr,
    )
    assert "working in" not in ev.activity.lower() or "«" in ev.activity, ev.activity
    assert ev.activity != "working in VS Code", ev.activity
    assert "ROCM-4042" in ev.verbatim.errors
    assert "FAKE-999" not in ev.verbatim.errors
    assert "https://docs.demo-acme.io/errors/ROCM-4042" in ev.verbatim.urls
    assert "https://evil.example" not in ev.verbatim.urls
    assert "parse_timeline" in ev.verbatim.identifiers
    assert "not_in_ocr" not in ev.verbatim.identifiers

    # Specific activity preserved.
    ev2 = _parse_perceive_json(
        '{"activity":"debugging ROCM-4042 hip_alloc failure in terminal",'
        '"app_context":"terminal","topics":["rocm"],'
        '"verbatim":{"errors":["ROCM-4042"],"urls":[],"identifiers":[],'
        '"numbers":[],"quotes":[]}}',
        app="Terminal",
        window_title="zsh",
        ocr_full_text=ocr,
    )
    assert ev2.activity.startswith("debugging ROCM-4042")
    assert ev2.verbatim.errors == ["ROCM-4042"]

    # Empty / unparsable → OCR-grounded fallback (not "working in X").
    ev3 = _parse_perceive_json("not json", app="Chrome", window_title="Blog", ocr_full_text=ocr)
    assert "working in" not in ev3.activity.lower()
    assert "parse_timeline" in ev3.activity or "inspecting" in ev3.activity

    print("perceive parse OK")


if __name__ == "__main__":
    check_sentinel()
    check_perceive()
    print("ALL PASS")
