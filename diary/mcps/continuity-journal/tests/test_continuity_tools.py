from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from scripts import continuity_cli, continuity_dispatch, continuity_mcp
from scripts.continuity_core import ContinuityStore


class ScopedToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve() / "Diaries"
        self.protocol = self.base.parent / "PROTOCOL.md"
        self.protocol.write_text("# Isolated tool-test protocol\n", encoding="utf-8")
        config = self.base.parent / "runtime.json"
        config.write_text(json.dumps({"schema_version": 1, "diary_base": str(self.base),
                                      "protocol_path": str(self.protocol)}), encoding="utf-8")
        self.config_environment = patch.dict(os.environ, {"CONTINUITY_CONFIG": str(config)})
        self.config_environment.start()
        self.addCleanup(self.config_environment.stop)
        factory = ContinuityStore.for_thread
        self.factory_patch = patch.object(
            ContinuityStore,
            "for_thread",
            side_effect=lambda thread_id, parent_thread_id=None: factory(
                thread_id, parent_thread_id, base_root=self.base
            ),
        )
        self.factory_mock = self.factory_patch.start()

    def tearDown(self) -> None:
        self.factory_patch.stop()
        self.temp.cleanup()

    def call(self, method: str, thread_id: str = "thread-A", parent: str | None = None, **payload) -> dict:
        return continuity_cli.invoke(
            method, thread_id=thread_id, parent_thread_id=parent, payload=payload
        )

    def start(self, thread_id: str = "thread-A", parent: str | None = None) -> dict:
        return self.call("diary_start", thread_id, parent, prompt=f"exact {thread_id}", title="task", plan=["one"])

    def cli(self, args: list[str], stdin: str = "") -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(stdin)), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = continuity_cli.main(args)
        return result, output.getvalue(), errors.getvalue()

    def test_every_tool_requires_explicit_identity_in_function_and_mcp_schema(self) -> None:
        schemas = {tool.name: tool.inputSchema for tool in asyncio.run(continuity_mcp.mcp.list_tools())}
        self.assertEqual(set(schemas), set(continuity_cli.METHODS))
        for name in continuity_cli.METHODS:
            with self.subTest(name=name):
                signature = inspect.signature(getattr(continuity_mcp, name))
                self.assertIs(signature.parameters["thread_id"].default, inspect.Parameter.empty)
                self.assertIsNone(signature.parameters["parent_thread_id"].default)
                self.assertIn("thread_id", schemas[name]["required"])
                self.assertNotIn("parent_thread_id", schemas[name]["required"])
        self.assertFalse(hasattr(continuity_mcp, "store"))

    def test_mcp_uses_internal_dispatch_not_administrative_cli(self) -> None:
        self.assertIs(continuity_mcp.invoke, continuity_dispatch.invoke)
        self.assertIs(continuity_cli.invoke, continuity_dispatch.invoke)
        self.assertEqual(continuity_mcp.invoke.__module__, "scripts.continuity_dispatch")
        self.assertFalse(hasattr(continuity_dispatch, "store"))
        self.assertFalse(hasattr(continuity_dispatch, "current_scope"))
        with self.assertRaises(TypeError):
            continuity_dispatch.METHODS["arbitrary"] = "_transaction"
        self.assertIn("not a workflow", continuity_mcp.mcp.instructions)
        self.assertIn("administrative", continuity_cli.__doc__.lower())

    def test_all_mcp_wrappers_forward_identity_payload_and_posthoc_without_state(self) -> None:
        values = {
            "thread_id": "thread-A", "prompt": "exact", "resume_prompt": "resume",
            "title": "task", "plan": ["step"], "milestones": ["done"],
            "checkpoint_progress": ["state"], "status": "작업 중", "entry_id": "entry-A",
            "reason": "recovery", "next_action": "next", "objective": "objective",
            "last_prompt": "last", "expected_sha256": "hash", "objective_complete": True,
        }
        for name in continuity_dispatch.METHODS:
            function = getattr(continuity_mcp, name)
            signature = inspect.signature(function)
            args = {key: values[key] for key, item in signature.parameters.items() if item.default is inspect.Parameter.empty}
            args["parent_thread_id"] = "parent-A"
            if "posthoc" in signature.parameters:
                args["posthoc"] = True
            with self.subTest(name=name), patch.object(continuity_mcp, "invoke", return_value={"mock": True}) as dispatch:
                self.assertEqual(function(**args), {"mock": True})
                actual = dispatch.call_args
                self.assertEqual(actual.args, (name,))
                self.assertEqual(actual.kwargs["thread_id"], "thread-A")
                self.assertEqual(actual.kwargs["parent_thread_id"], "parent-A")
                self.assertNotIn("thread_id", actual.kwargs["payload"])
                self.assertNotIn("parent_thread_id", actual.kwargs["payload"])
                if "posthoc" in signature.parameters:
                    self.assertTrue(actual.kwargs["payload"]["posthoc"])
        self.factory_mock.assert_not_called()

    def test_fast_posthoc_schema_and_non_idempotent_mutation_annotations(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(continuity_mcp.mcp.list_tools())}
        self.assertEqual(len(tools), 16)
        for name in ("diary_correction", "diary_resume"):
            with self.subTest(name=name):
                field = tools[name].inputSchema["properties"]["posthoc"]
                self.assertEqual(field["type"], "boolean")
                self.assertFalse(field["default"])
                self.assertNotIn("posthoc", tools[name].inputSchema["required"])
                self.assertIn("nod always skips", tools[name].description.lower())
        for name in ("diary_set_status", "diary_finish", "checkpoint_save"):
            with self.subTest(name=name):
                self.assertFalse(tools[name].annotations.idempotentHint)
                self.assertFalse(tools[name].annotations.readOnlyHint)
        self.assertIn("작업 중", tools["diary_record_fast"].inputSchema["properties"]["status"]["description"])
        self.assertTrue(tools["diary_read"].annotations.readOnlyHint)
        self.assertTrue(tools["diary_read"].annotations.idempotentHint)

    def test_fast_correction_defers_then_records_in_same_block_posthoc(self) -> None:
        entry = self.start()
        diary = self.base / "thread-A" / "Diary"
        original = diary.read_bytes()
        arguments = {"thread_id": "thread-A", "entry_id": entry["entry_id"], "prompt": "urgent correction (fast)", "checkpoint_progress": ["urgent work stopped"]}
        deferred = continuity_mcp.diary_correction(**arguments)
        self.assertTrue(deferred["skipped"])
        self.assertEqual(deferred["reason"], "fast-defers-diary-until-finish")
        self.assertEqual(diary.read_bytes(), original)
        nod = continuity_mcp.diary_correction(**{**arguments, "prompt": "skip (fast) (nod)", "posthoc": True})
        self.assertTrue(nod["skipped"])
        self.assertEqual(diary.read_bytes(), original)
        recorded = continuity_mcp.diary_correction(**arguments, posthoc=True)
        self.assertEqual(recorded["entry_id"], entry["entry_id"])
        self.assertEqual(diary.read_text().count("<!-- CONTINUITY_ENTRY_START"), 1)
        self.assertIn("urgent correction (fast)", diary.read_text())
        self.assertEqual(self.call("diary_list_active")["active_count"], 1)

    def test_fast_resume_defers_then_copies_linked_source_posthoc(self) -> None:
        entry = self.start()
        self.call("diary_set_status", entry_id=entry["entry_id"], status="작업 보류")
        diary = self.base / "thread-A" / "Diary"
        original = diary.read_bytes()
        arguments = {"thread_id": "thread-A", "entry_id": entry["entry_id"], "resume_prompt": "urgent resume (fast)"}
        deferred = continuity_mcp.diary_resume(**arguments)
        self.assertTrue(deferred["skipped"])
        self.assertEqual(deferred["reason"], "fast-defers-diary-until-finish")
        self.assertEqual(diary.read_bytes(), original)
        nod = continuity_mcp.diary_resume(**{**arguments, "resume_prompt": "skip (fast) (nod)", "posthoc": True})
        self.assertTrue(nod["skipped"])
        self.assertEqual(diary.read_bytes(), original)
        resumed = continuity_mcp.diary_resume(**arguments, posthoc=True)
        self.assertNotEqual(resumed["entry_id"], entry["entry_id"])
        self.assertEqual(resumed["block_number"], 2)
        self.assertEqual(resumed["status"], "작업 중")
        scan = self.call("diary_list_active")
        self.assertEqual((scan["active_count"], scan["paused_count"]), (1, 1))

    def test_fast_new_task_can_record_unfinished_work_as_working(self) -> None:
        entry = continuity_mcp.diary_record_fast(
            thread_id="thread-A", prompt="urgent (fast)", title="urgent", status="작업 중",
            progress=["urgent work interrupted; remaining step is documented"],
        )
        self.assertEqual(entry["status"], "작업 중")
        self.assertEqual(self.call("diary_list_active")["active_count"], 1)

    def test_status_reports_runtime_provenance_only_on_status(self) -> None:
        self.start()
        runtime = {"source_root": "source", "server_path": "server", "config_path": "config", "diary_base": "base", "protocol_path": "protocol", "version": "test"}
        with patch("scripts.continuity_runtime.runtime_info", return_value=runtime) as info:
            status = continuity_mcp.continuity_status("thread-A")
            self.assertEqual(status["runtime"], runtime)
            info.assert_called_once_with()
            self.assertNotIn("runtime", continuity_mcp.diary_list_active("thread-A"))
            info.assert_called_once_with()

    def test_selected_block_reader_returns_full_exact_fields_without_mutation(self) -> None:
        prompt = "  원문\r\n" + "긴 기록 🧭 " * 300 + "\r\n끝  "
        plan = ["first\n  nested detail", "second"]
        journal = "future-self guidance " * 100
        entry = continuity_mcp.diary_start("thread-A", prompt, "selected", plan)
        continuity_mcp.diary_set_status("thread-A", "작업 완료", entry_id=entry["entry_id"], progress=["selected milestone"], journal=journal)
        continuity_mcp.diary_start("thread-A", "unselected block secret", "other", ["other"])
        diary = self.base / "thread-A" / "Diary"
        before = diary.read_bytes()
        selected = continuity_mcp.diary_read("thread-A", entry_id=entry["entry_id"])
        self.assertEqual(selected["entry_id"], entry["entry_id"])
        self.assertEqual(selected["prompt"], prompt)
        self.assertEqual(selected["plan"], plan)
        self.assertEqual(selected["journal"], journal.strip())
        self.assertEqual(selected["status"], "작업 완료")
        self.assertTrue(any("selected milestone" in item for item in selected["progress"]))
        self.assertNotIn("unselected block secret", json.dumps(selected))
        self.assertEqual(selected, continuity_mcp.diary_read("thread-A", block_number=1))
        self.assertEqual(before, diary.read_bytes())

    def test_selected_reader_requires_selector_and_rejects_foreign_entry(self) -> None:
        own = self.start("thread-A")
        self.start("thread-B")
        before_a = (self.base / "thread-A" / "Diary").read_bytes()
        before_b = (self.base / "thread-B" / "Diary").read_bytes()
        with self.assertRaises(ValueError):
            continuity_mcp.diary_read("thread-A")
        with self.assertRaises(ValueError):
            continuity_mcp.diary_read("thread-B", entry_id=own["entry_id"])
        self.assertEqual(before_a, (self.base / "thread-A" / "Diary").read_bytes())
        self.assertEqual(before_b, (self.base / "thread-B" / "Diary").read_bytes())

    def test_interleaved_tools_cannot_mix_entries_or_numbering(self) -> None:
        first = self.start("thread-A")
        other = self.start("thread-B")
        self.assertEqual((first["block_number"], other["block_number"]), (1, 1))
        continuity_mcp.diary_progress_append("thread-A", ["only-A"], entry_id=first["entry_id"])
        before_a = (self.base / "thread-A" / "Diary").read_bytes()
        before_b = (self.base / "thread-B" / "Diary").read_bytes()
        with self.assertRaises(ValueError):
            continuity_mcp.diary_set_status("thread-B", "작업 완료", entry_id=first["entry_id"])
        self.assertEqual(before_a, (self.base / "thread-A" / "Diary").read_bytes())
        self.assertEqual(before_b, (self.base / "thread-B" / "Diary").read_bytes())
        self.call("diary_set_status", "thread-B", status="작업 완료", block_number=1)
        self.assertEqual(self.call("diary_list_active", "thread-A")["active_count"], 1)
        self.assertEqual(self.call("diary_list_active", "thread-B")["active_count"], 0)
        self.assertNotIn("only-A", before_b.decode())

    def test_nested_scope_uses_immediate_parent_and_never_parent_diary(self) -> None:
        self.start("thread-A")
        self.start("thread-B", "thread-A")
        child = self.start("thread-C", "thread-B")
        expected = self.base / "thread-A" / "Child" / "thread-B" / "Child" / "thread-C" / "Diary"
        self.assertEqual(Path(child["diary_path"]), expected)
        metadata = continuity_mcp.diary_resolve_scope("thread-C", "thread-B")
        self.assertEqual(metadata["lineage"], ["thread-A", "thread-B", "thread-C"])
        self.assertNotIn("exact thread-C", (self.base / "thread-A" / "Diary").read_text())
        self.assertNotIn("exact thread-C", (self.base / "thread-A" / "Child" / "thread-B" / "Diary").read_text())

    def test_read_only_resolve_does_not_initialize_scope(self) -> None:
        metadata = continuity_mcp.diary_resolve_scope("thread-A")
        self.assertEqual(metadata["thread_id"], "thread-A")
        self.assertFalse(self.base.exists())
        continuity_mcp.diary_register_scope("thread-A")
        diary = self.base / "thread-A" / "Diary"
        self.assertTrue(diary.is_file())
        original = diary.read_bytes()
        continuity_mcp.diary_register_scope("thread-A")
        self.assertEqual(diary.read_bytes(), original)

    def test_checkpoint_reads_and_clear_guards_are_scoped(self) -> None:
        self.call("checkpoint_save", "thread-A", objective="only-A", last_prompt="A")
        self.call("checkpoint_save", "thread-B", objective="only-B", last_prompt="B")
        a = self.call("checkpoint_read", "thread-A")
        b = self.call("checkpoint_read", "thread-B")
        self.assertIn("only-A", json.dumps(a))
        self.assertNotIn("only-B", json.dumps(a))
        self.assertNotEqual(a["checkpoint_sha256"], b["checkpoint_sha256"])
        with self.assertRaises(ValueError):
            self.call("checkpoint_clear", "thread-B", expected_sha256=a["checkpoint_sha256"], objective_complete=True)
        self.assertEqual(self.call("checkpoint_read", "thread-B"), b)

    def test_create_resume_and_fast_always_record_real_thread_id(self) -> None:
        entry = self.start()
        self.call("diary_set_status", entry_id=entry["entry_id"], status="작업 보류")
        resumed = self.call("diary_resume", entry_id=entry["entry_id"], resume_prompt="resume")
        fast = self.call("diary_record_fast", prompt="fast (fast)", title="fast", status="작업 완료")
        text = (self.base / "thread-A" / "Diary").read_text()
        self.assertEqual(text.count("- task/thread: thread-A"), 3)
        self.assertNotIn("current Codex task", text)
        self.assertEqual((resumed["block_number"], fast["block_number"]), (2, 3))

    def test_task_thread_labels_cannot_spoof_identity(self) -> None:
        for method in continuity_cli.IDENTITY_METHODS:
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "task_thread must equal"):
                self.call(method, task_thread="another-task")
        self.assertFalse(self.base.exists())

    def test_nod_and_fast_deferral_make_no_scope_files(self) -> None:
        result = self.call("diary_start", prompt="skip (nod)", title="x", plan=["one"])
        self.assertTrue(result["skipped"])
        result = self.call("diary_start", prompt="later (fast)", title="x", plan=["one"])
        self.assertTrue(result["skipped"])
        self.assertFalse(self.base.exists())

    def test_cli_requires_identity_even_when_inherited_environment_has_one(self) -> None:
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "inherited-parent"}), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                continuity_cli.main(["diary_list_active"])
        self.assertEqual(raised.exception.code, 2)
        self.factory_mock.assert_not_called()

    def test_cli_rejects_private_methods_and_payload_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.call("_transaction")
        for payload in ({"thread_id": "other"}, {"parent_thread_id": "other"}, []):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                continuity_cli.invoke("diary_list_active", thread_id="thread-A", payload=payload)
        self.factory_mock.assert_not_called()

    def test_cli_json_file_and_stdin_payloads(self) -> None:
        payload = {"prompt": "original\n한국어", "title": "cli", "plan": ["one"]}
        payload_path = Path(self.temp.name) / "payload.json"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        result, output, errors = self.cli(["diary_start", "--thread-id", "thread-A", "--json-file", str(payload_path)])
        self.assertEqual((result, errors), (0, ""))
        entry = json.loads(output)
        result, output, errors = self.cli(
            ["diary_progress_append", "--thread-id", "thread-A"],
            json.dumps({"entry_id": entry["entry_id"], "milestones": ["stdin milestone"]}),
        )
        self.assertEqual((result, errors), (0, ""))
        result, output, errors = self.cli(["diary_list_active", "--thread-id", "thread-A", "--json", "{}"])
        self.assertEqual((result, errors), (0, ""))
        self.assertEqual(json.loads(output)["active_count"], 1)
        result, output, errors = self.cli(["diary_list_active", "--thread-id", "thread-A", "--json", "[]"])
        self.assertEqual((result, output), (1, ""))
        self.assertIn("JSON object", errors)

    def test_concurrent_mcp_calls_keep_independent_scope_state(self) -> None:
        # Pre-registration makes simultaneous discovery independent of mkdir timing.
        self.call("diary_register_scope", "thread-A")
        self.call("diary_register_scope", "thread-B")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: self.start("thread-A" if i % 2 else "thread-B"), range(12)))
        for thread_id in ("thread-A", "thread-B"):
            blocks = [r["block_number"] for r in results if Path(r["diary_path"]).parent.name == thread_id]
            self.assertEqual(sorted(blocks), list(range(1, 7)))
            self.assertEqual(self.call("diary_list_active", thread_id)["active_count"], 6)


if __name__ == "__main__":
    unittest.main()
