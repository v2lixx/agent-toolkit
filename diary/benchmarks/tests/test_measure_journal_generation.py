from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


BENCHMARKS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS))
SPEC = importlib.util.spec_from_file_location("measure_journal_generation", BENCHMARKS / "measure_journal_generation.py")
writer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(writer)

RECORD = {
    "plan": ["Read the synthetic rows.", "Verify the total.", "Report the result."],
    "milestones": ["Six rows were checked without renaming identifiers."],
    "final_progress": ["The verified quantity total is 134."],
    "journal": "Keep identifiers stable; do not sort by display label when the owner forbids it.",
}


class JournalWriterTests(unittest.TestCase):
    def test_writer_prompt_is_synthetic_quoted_and_disables_diary(self) -> None:
        prompt = writer.writer_request("Original fixture request")
        self.assertTrue(prompt.rstrip().endswith("(nod)"))
        self.assertIn('"synthetic_original_request":"Original fixture request"', prompt)
        self.assertIn("Do not perform the original task", prompt)

    def test_valid_record_round_trips_without_regenerating_original_prompt(self) -> None:
        self.assertEqual(writer.parse_writer_record(json.dumps(RECORD)), RECORD)

    def test_extra_fields_invalid_arrays_and_markdown_are_rejected(self) -> None:
        for value in ({**RECORD, "original_prompt": "not allowed"},
                      {**RECORD, "plan": "not a list"}, {**RECORD, "milestones": []},
                      {**RECORD, "journal": None}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                writer.parse_writer_record(json.dumps(value))
        with self.assertRaises(ValueError):
            writer.parse_writer_record("```json\n{}\n```")

    def test_missing_provider_counters_are_not_reported_as_zero(self) -> None:
        sample = {"model_wall_ms": 100, "mcp_cycle_ms": 2, "controlled_stage_wall_ms": 105,
                  "generated_record_proxy_tokens": 60, "mcp_serialized_input_plus_result_tokens": 900,
                  "provider_usage": {"input_tokens": 1000, "reasoning_tokens": None}}
        summary = writer.summarize_samples([sample])
        self.assertEqual(summary["provider_usage"]["reasoning_tokens"], {"n": 0, "unavailable_samples": 1})
        self.assertEqual(summary["provider_usage"]["input_tokens"]["mean"], 1000)

    def test_private_path_or_session_shaped_output_is_not_published(self) -> None:
        writer.assert_public_synthetic_text(json.dumps(RECORD))
        for text in ("/Users/private-person/file", "/tmp/session/file", "01234567-89ab-cdef-0123-456789abcdef"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                writer.assert_public_synthetic_text(text)


class ActualJournalApplyTest(unittest.IsolatedAsyncioTestCase):
    async def test_generated_fields_are_really_stored_with_correct_mcp_progress_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            base, protocol, config = root / "Diaries", root / "PROTOCOL.md", root / "runtime.json"
            protocol.write_text("# Synthetic test protocol\n", encoding="utf-8")
            config.write_text(json.dumps({"schema_version": 1, "diary_base": str(base), "protocol_path": str(protocol)}), encoding="utf-8")
            async with writer.connected_server(config) as (session, _, schemas, _):
                await writer.guard_temporary_scope(session, config, base)
                applied = await writer.apply_record(session, "  exact request\r\n", RECORD, "synthetic-writer", writer.TokenCounter(), schemas)
                self.assertTrue(applied["completion_verified"])
                self.assertEqual(len(applied["mcp_operations"]), 3)
                self.assertGreater(applied["mcp_cycle_ms"], 0)


if __name__ == "__main__":
    unittest.main()
