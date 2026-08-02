"""Fail-closed sentinel parser contract tests."""

from __future__ import annotations

import unittest

from memoryd.stages import _parse_sentinel_json


class SentinelFailClosedTest(unittest.TestCase):
    def test_malformed_output_blocks(self):
        verdict = _parse_sentinel_json("not-json")
        self.assertEqual(
            (verdict.decision, verdict.category, verdict.reason),
            ("block", "normal", "malformed_output"),
        )

    def test_missing_or_unknown_category_blocks(self):
        for raw in ('{"decision":"allow"}', '{"category":"medical_record","confidence":0.9}'):
            with self.subTest(raw=raw):
                verdict = _parse_sentinel_json(raw)
                self.assertEqual(verdict.decision, "block")
                self.assertEqual(verdict.reason, "unknown_category")

    def test_low_confidence_normal_blocks(self):
        verdict = _parse_sentinel_json('{"category":"normal","confidence":0.69}')
        self.assertEqual(verdict.reason, "low_confidence")
        self.assertEqual(verdict.decision, "block")

    def test_non_numeric_confidence_blocks_as_low_confidence(self):
        for raw in (
            '{"category":"normal","confidence":true}',
            '{"category":"normal","confidence":false}',
            '{"category":"normal","confidence":null}',
            '{"category":"normal","confidence":"high"}',
        ):
            with self.subTest(raw=raw):
                verdict = _parse_sentinel_json(raw)
                self.assertEqual(
                    (verdict.decision, verdict.confidence, verdict.reason),
                    ("block", 0.0, "low_confidence"),
                )

    def test_high_confidence_normal_allows(self):
        verdict = _parse_sentinel_json('{"category":"normal","confidence":0.70}')
        self.assertEqual(verdict.reason, "classified_normal")
        self.assertEqual(verdict.decision, "allow")

    def test_sensitive_category_blocks_even_below_threshold(self):
        verdict = _parse_sentinel_json('{"category":"banking_finance","confidence":0.2}')
        self.assertEqual(verdict.reason, "sensitive_category")
        self.assertEqual(verdict.decision, "block")
