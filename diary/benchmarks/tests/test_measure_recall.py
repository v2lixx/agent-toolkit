import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("measure_recall", Path(__file__).resolve().parents[1] / "measure_recall.py")
recall = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recall)


class RecallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = recall.build_corpus(17)

    def test_deterministic_corpus_and_sizes(self):
        self.assertEqual(self.corpus, recall.build_corpus(17))
        self.assertEqual([recall.token_count(x["history"]) for x in self.corpus], [4000, 8000, 16000, 32000])

    def test_all_target_events_are_in_history(self):
        for index, checkpoint in enumerate(self.corpus):
            for preceding in self.corpus[:index + 1]:
                self.assertIn(preceding["target_events"], checkpoint["history"])
            self.assertEqual(len(checkpoint["oracle"]["requirements"]), 32 + 16 * index)

    def test_oracle_matches_latest_raw_events(self):
        for checkpoint in self.corpus:
            for key, value in checkpoint["oracle"]["requirements"].items():
                self.assertIn(value, checkpoint["target_history"])
                self.assertNotIn(value, checkpoint["oracle"]["stale"].get(key, []))

    def test_perfect_exact_score(self):
        oracle = self.corpus[-1]["oracle"]
        answer = {"requirements": oracle["requirements"], "completed_actions": oracle["completed_actions"], "next_actions": ["P001"]}
        result = recall.score_response(json.dumps(answer), oracle)
        for key in ("requirement_failures", "latest_correction_failures", "stale_value_count", "repeated_completed_actions", "completed_action_recall_failures"):
            self.assertEqual(result[key], 0)

    def test_stale_repeat_and_abstention(self):
        oracle = self.corpus[0]["oracle"]
        key = next(iter(oracle["stale"]))
        answer = {"requirements": {key: oracle["stale"][key][0], "R999": "invented"}, "next_actions": ["A001", "A001"], "completed_actions": []}
        result = recall.score_response(json.dumps(answer), oracle)
        self.assertEqual(result["stale_value_count"], 1)
        self.assertEqual(result["repeated_completed_actions"], 1)
        self.assertEqual(result["known_requirement_abstentions"], 31)
        self.assertEqual(result["unknown_requirement_fabrications"], 1)

    def test_invalid_json_fails_all_requirements(self):
        result = recall.score_response("not json", self.corpus[0]["oracle"])
        self.assertTrue(result["invalid_response"])
        self.assertEqual(result["requirement_failure_rate"], 1)

    def test_probe_contains_ids_not_expected_answers(self):
        oracle = self.corpus[0]["oracle"]
        prompt = recall.continuation_prompt("summary-placeholder", oracle, 17)
        for value in oracle["requirements"].values():
            self.assertNotIn(value, prompt)
        self.assertTrue(prompt.endswith("(nod)"))

    def test_event_parser_strips_private_ids_and_reasoning(self):
        raw = "\n".join(json.dumps(x) for x in [
            {"type": "thread.started", "thread_id": "private-thread"},
            {"type": "item.completed", "item": {"type": "reasoning", "text": "private-reasoning"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
        ])
        result = recall.parse_codex_events(raw)
        self.assertEqual(result["response"], "answer")
        self.assertNotIn("private-", json.dumps(result))

    def test_event_parser_rejects_tool_execution(self):
        with self.assertRaises(RuntimeError):
            recall.parse_codex_events(json.dumps({"type": "item.started", "item": {"type": "command_execution"}}))

    def test_nonfatal_cli_warning_with_completed_turn_is_allowed(self):
        events = [{"type": "item.completed", "item": {"type": "error", "message": "Nonfatal CLI warning"}},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}},
                  {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 1}}]
        result = recall.parse_codex_events("\n".join(json.dumps(x) for x in events))
        self.assertEqual(result["response"], "ok")
        self.assertEqual(result["cli_diagnostics"], [{"kind": "cli_warning", "cli_resolved_model": None}])

    def test_empty_or_invalid_next_actions_are_not_success(self):
        oracle = self.corpus[0]["oracle"]
        result = recall.score_response(json.dumps({"requirements": oracle["requirements"], "next_actions": ["P099"]}), oracle)
        self.assertEqual(result["missing_pending_actions"], 2)
        self.assertEqual(result["invalid_next_actions"], 1)

    def test_minimal_complete_answer_fits_summary_target(self):
        for checkpoint in self.corpus:
            oracle = checkpoint["oracle"]
            minimal = {"requirements": oracle["requirements"] | {key: None for key in oracle["unknown_requirements"]},
                       "completed_actions": oracle["completed_actions"], "next_actions": oracle["pending_actions"]}
            self.assertLessEqual(recall.token_count(recall.compact_json(minimal)), recall.SUMMARY_TARGET)


if __name__ == "__main__":
    unittest.main()
