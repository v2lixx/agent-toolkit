import json
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))
from render_results import render_mcp, render_recall, render_writer


class RenderTests(unittest.TestCase):
    def test_missing_model_results_cannot_be_drawn_as_successes(self):
        with self.assertRaises(ValueError):
            render_recall({"status": "incomplete"}, {})
        with self.assertRaises(ValueError):
            render_writer({"complete": False, "samples": []})

    def test_published_data_render_is_valid_and_reproducible(self):
        data = json.loads((BENCHMARKS / "results" / "mcp-overhead-2026-09-05.json").read_text())
        svg = render_mcp(data)
        ET.fromstring(svg)
        self.assertEqual(svg, render_mcp(data))
        self.assertIn("not provider billing", svg)
        self.assertIn("not zero total task cost", svg)
        self.assertIn("90 blocks", svg)
        for group in data["groups"]:
            if group["mode"] == "warm":
                self.assertIn(f'{group["summary"]["cycle_elapsed_ms"]["mean"]:.2f}', svg)

    def test_chart_scales_for_slower_hosts(self):
        data = json.loads((BENCHMARKS / "results" / "mcp-overhead-2026-09-05.json").read_text())
        for group in data["groups"]:
            if group["mode"] == "warm":
                group["summary"]["cycle_elapsed_ms"]["mean"] = 63
                group["summary"]["cycle_elapsed_ms"]["p95_nearest_rank"] = 87
        svg = render_mcp(data)
        ET.fromstring(svg)
        self.assertIn(">90</text>", svg)
        self.assertIn(">63.00</text>", svg)


if __name__ == "__main__":
    unittest.main()
