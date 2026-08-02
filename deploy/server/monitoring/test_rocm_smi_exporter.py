from __future__ import annotations

import unittest

from rocm_smi_exporter import parse_rocm_smi_json, render_prometheus


class RocmSmiExporterTest(unittest.TestCase):
    def test_parses_rocm_smi_card_metrics(self) -> None:
        raw = """
        {
          "card0": {
            "GPU use (%)": "73",
            "VRAM Total Memory (B)": "48000000000",
            "VRAM Total Used Memory (B)": "12000000000"
          }
        }
        """
        cards = parse_rocm_smi_json(raw)
        self.assertEqual(cards["card0"]["utilization_percent"], 73)
        self.assertEqual(cards["card0"]["vram_free_bytes"], 36000000000)
        self.assertEqual(cards["card0"]["vram_used_percent"], 25)

        rendered = render_prometheus(cards)
        self.assertIn(
            'dejaview_rocm_gpu_utilization_percent{gpu="card0"} 73.000000',
            rendered,
        )
        self.assertIn(
            'dejaview_rocm_vram_used_bytes{gpu="card0"} 12000000000.000000',
            rendered,
        )

    def test_rejects_non_gpu_json(self) -> None:
        with self.assertRaises(ValueError):
            parse_rocm_smi_json('{"system": {"driver": "6.4"}}')


if __name__ == "__main__":
    unittest.main()
