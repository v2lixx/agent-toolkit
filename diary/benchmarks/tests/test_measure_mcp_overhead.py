from __future__ import annotations

import importlib.util
import json
import unittest
from types import SimpleNamespace
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "measure_mcp_overhead.py"
SPEC = importlib.util.spec_from_file_location("measure_mcp_overhead", SCRIPT)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class OverheadMeasurementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.counter = benchmark.TokenCounter("o200k_base")

    def test_statistics_use_documented_nearest_rank_percentile(self) -> None:
        data = benchmark.summarize(list(range(1, 21)))
        self.assertEqual(data["n"], 20)
        self.assertEqual(data["mean"], 10.5)
        self.assertEqual(data["median"], 10.5)
        self.assertEqual(data["p95_nearest_rank"], 19)
        self.assertEqual(benchmark.summarize([]), {"n": 0})
        self.assertEqual(benchmark.summarize([7])["p95_nearest_rank"], 7)

    def test_synthetic_prompt_has_exact_requested_proxy_length(self) -> None:
        for length in benchmark.SCENARIOS.values():
            with self.subTest(length=length):
                prompt = benchmark.synthetic_prompt(length, self.counter)
                self.assertEqual(self.counter.text(prompt), length)

    def test_json_serialization_is_deterministic_and_preserves_unicode(self) -> None:
        self.assertEqual(benchmark.compact_json({"b": 2, "a": "작업 완료"}), '{"a":"작업 완료","b":2}')

    def test_operation_payloads_form_one_complete_lifecycle(self) -> None:
        start = benchmark.operation_payload("diary_start", prompt="exact prompt")
        self.assertEqual(start["prompt"], "exact prompt")
        self.assertEqual(len(start["plan"]), 3)
        for name in benchmark.OPERATIONS[1:]:
            payload = benchmark.operation_payload(name, prompt="ignored", entry_id="fixture-entry")
            self.assertEqual(payload["entry_id"], "fixture-entry")
        finish = benchmark.operation_payload("diary_set_status", prompt="ignored", entry_id="fixture-entry")
        self.assertEqual(finish["status"], "작업 완료")
        self.assertIn("progress", finish)
        self.assertNotIn("final_progress", finish)
        with self.assertRaises(ValueError):
            benchmark.operation_payload("unexpected", prompt="ignored")

    def test_sample_emits_counts_not_private_payload_or_paths(self) -> None:
        arguments = {"thread_id": "private-identity", "prompt": "private prompt"}
        result = {"content": [{"type": "text", "text": json.dumps({"path": "/private/local/path"})}], "isError": False}
        sample = benchmark.sample_call("diary_start", arguments, result, 12.5, self.counter)
        encoded = json.dumps(sample)
        for private in ("private-identity", "private prompt", "/private/local/path"):
            self.assertNotIn(private, encoded)
        self.assertEqual(sample["serialized_input_plus_result_tokens"],
                         sample["serialized_call_input_tokens"] + sample["serialized_call_result_tokens"])
        self.assertEqual(sample["elapsed_ms"], 12.5)

    def test_error_results_are_not_included_as_successful_measurements(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "aborted"):
            benchmark.result_data({"isError": True, "content": []})

    def test_schema_validation_rejects_silently_ignored_extra_arguments(self) -> None:
        schemas = SimpleNamespace(tools=[SimpleNamespace(name="diary_set_status", inputSchema={
            "properties": {"thread_id": {}, "status": {}, "progress": {}},
            "required": ["thread_id", "status"],
        })])
        benchmark.validate_arguments(schemas, "diary_set_status", {"thread_id": "fixture", "status": "작업 완료", "progress": ["done"]})
        with self.assertRaisesRegex(ValueError, "Unknown"):
            benchmark.validate_arguments(schemas, "diary_set_status", {"thread_id": "fixture", "status": "작업 완료", "final_progress": ["done"]})


class ActualStdioBenchmarkSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_three_scenarios_use_real_isolated_mcp_and_emit_no_paths(self) -> None:
        result = await benchmark.run_benchmark(1, 0, benchmark.TokenCounter())
        self.assertEqual(len(result["groups"]), 3)
        self.assertEqual(result["startup"]["tool_count"], 16)
        self.assertEqual(result["no_diary_bookkeeping_baseline"]["calls_per_block"], 0)
        for group in result["groups"]:
            self.assertEqual(group["summary"]["cycle_elapsed_ms"]["n"], 1)
            self.assertEqual(len(group["samples"][0]["operations"]), 3)
            self.assertTrue(group["samples"][0]["stored_block_verified"])
            self.assertGreater(group["samples"][0]["cycle_elapsed_ms"], 0)
        encoded = json.dumps(result)
        self.assertNotIn(str(benchmark.PACKAGE), encoded)
        self.assertNotIn("diary-mcp-benchmark-", encoded)
        self.assertNotIn("benchmark-guard", encoded)


if __name__ == "__main__":
    unittest.main()
