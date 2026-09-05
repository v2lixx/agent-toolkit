"""Fresh-process MCP stdio integration; all data/config is temporary.

Before any write, the server must prove read-only that it loaded the explicit
test configuration.  A broken config implementation therefore cannot send a
test write to the real user's Diary root.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SOURCE_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = SOURCE_ROOT / "scripts" / "continuity_mcp.py"
TOOL_NAMES = {
    "diary_resolve_scope", "diary_register_scope", "diary_start",
    "diary_progress_append", "diary_correction", "diary_set_status",
    "diary_resume", "diary_list_active", "diary_read", "diary_finish", "diary_record_fast",
    "continuity_resume", "continuity_status", "checkpoint_save",
    "checkpoint_read", "checkpoint_clear",
}


class MCPStdioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp.name).resolve()
        self.base = self.temp_root / "Diaries"
        self.protocol = self.temp_root / "PROTOCOL.md"
        self.protocol.write_text("# Isolated protocol fixture\n", encoding="utf-8")
        self.config = self.temp_root / "runtime.json"
        self.config.write_text(json.dumps({
            "schema_version": 1,
            "diary_base": str(self.base),
            "protocol_path": str(self.protocol),
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def call(self, session: ClientSession, name: str, thread: str = "fixture-A", **kwargs) -> dict:
        result = await session.call_tool(name, {"thread_id": thread, **kwargs})
        text = "\n".join(block.text for block in result.content if block.type == "text")
        self.assertFalse(result.isError, f"{name}: {text}")
        data = json.loads(text)
        self.assertIsInstance(data, dict)
        return data

    @asynccontextmanager
    async def server(self):
        # No environment-derived thread identity or user-supplied MCP paths.
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(SERVER_PATH)],
            cwd=str(SOURCE_ROOT),
            env={"CONTINUITY_CONFIG": str(self.config), "PATH": os.environ.get("PATH", "")},
        )
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
            # The SDK logs malformed/non-JSON stdout here.  Successful calls
            # alone are insufficient because some noise can otherwise be skipped.
            with self.assertNoLogs("mcp.client.stdio", level="ERROR"):
                async with stdio_client(parameters, errlog=errors) as (reader, writer):
                    async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=15)) as session:
                        initialized = await session.initialize()
                        self.assertEqual(initialized.serverInfo.name, "continuity-journal")
                        resolved = await self.call(session, "diary_resolve_scope")
                        self.assertEqual(Path(resolved["diary_path"]), self.base / "fixture-A" / "Diary")
                        self.assertEqual(Path(resolved["protocol_path"]), self.protocol)
                        status = await self.call(session, "continuity_status")
                        self.assertEqual(Path(status["root"]), self.base / "fixture-A")
                        runtime = status["runtime"]
                        self.assertEqual(Path(runtime["source_root"]).resolve(), SOURCE_ROOT)
                        self.assertEqual(Path(runtime["server_path"]).resolve(), SERVER_PATH)
                        self.assertEqual(Path(runtime["config_path"]).resolve(), self.config)
                        self.assertTrue(runtime["version"])
                        self.assertFalse(self.base.exists(), "read-only bootstrap created a Diary directory")
                        yield session

    async def test_initialize_lists_sixteen_scoped_tools_and_read_only_annotations(self) -> None:
        async with self.server() as session:
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            self.assertEqual(set(tools), TOOL_NAMES)
            self.assertEqual(len(tools), 16)
            for name, tool in tools.items():
                with self.subTest(tool=name):
                    schema = tool.inputSchema
                    self.assertIn("thread_id", schema["required"])
                    self.assertIn("parent_thread_id", schema["properties"])
                    self.assertNotIn("parent_thread_id", schema["required"])
                    self.assertNotIn("base_root", schema["properties"])
                    self.assertNotIn("protocol_path", schema["properties"])
            for name in ("diary_resolve_scope", "continuity_status", "diary_list_active", "diary_read", "checkpoint_read"):
                self.assertTrue(tools[name].annotations.readOnlyHint)
            for name in ("diary_start", "diary_correction", "diary_record_fast", "diary_register_scope"):
                self.assertFalse(tools[name].annotations.readOnlyHint)
            missing = await session.call_tool("diary_resolve_scope", {})
            self.assertTrue(missing.isError)
            self.assertFalse(self.base.exists())

    async def test_real_write_roundtrip_exact_prompts_and_recursive_isolation(self) -> None:
        async with self.server() as session:
            prompt = "  첫줄\r\n둘째\r끝  \n- status: 작업 완료\n"
            root = await self.call(session, "diary_start", prompt=prompt, title="Root", plan=["a\n1. nested", "b"])
            self.assertEqual(root["prompt_sha256"], hashlib.sha256(prompt.encode()).hexdigest())
            root_path = self.base / "fixture-A" / "Diary"
            self.assertIn(prompt.encode(), root_path.read_bytes())
            root_before = root_path.read_bytes()
            selected = await self.call(session, "diary_read", entry_id=root["entry_id"])
            self.assertEqual(selected["prompt"], prompt)
            self.assertEqual(selected["plan"], ["a\n1. nested", "b"])
            self.assertEqual(selected["entry_id"], root["entry_id"])
            self.assertEqual(selected["prompt_sha256"], root["prompt_sha256"])
            self.assertEqual(root_path.read_bytes(), root_before)
            child = await self.call(session, "diary_start", "fixture-B", parent_thread_id="fixture-A", prompt="child canary", title="Child", plan=["child"])
            grandchild = await self.call(session, "diary_start", "fixture-C", parent_thread_id="fixture-B", prompt="grandchild canary", title="Grandchild", plan=["grandchild"])
            self.assertEqual(child["block_number"], 1)
            self.assertEqual(grandchild["block_number"], 1)
            self.assertEqual(Path(grandchild["diary_path"]), self.base / "fixture-A" / "Child" / "fixture-B" / "Child" / "fixture-C" / "Diary")
            child_path = Path(child["diary_path"])
            child_before = child_path.read_bytes()
            wrong = await session.call_tool("diary_set_status", {
                "thread_id": "fixture-B", "parent_thread_id": "fixture-A",
                "entry_id": root["entry_id"], "status": "작업 완료",
            })
            self.assertTrue(wrong.isError)
            self.assertEqual(child_path.read_bytes(), child_before)
            await self.call(session, "diary_set_status", "fixture-C", parent_thread_id="fixture-B", entry_id=grandchild["entry_id"], status="작업 완료")
            self.assertEqual(root_path.read_bytes(), root_before)
            self.assertEqual(child_path.read_bytes(), child_before)
            recovered = await self.call(session, "continuity_resume", "fixture-B", parent_thread_id="fixture-A", entry_id=child["entry_id"], reason="stdio fixture", next_action="verify")
            self.assertEqual(recovered["recovery_target_entry_id"], child["entry_id"])
            self.assertNotIn("grandchild canary", str(recovered))
            self.assertNotIn(root["entry_id"], str(recovered))

    async def test_posthoc_fast_can_preserve_unfinished_work_and_exact_prompt(self) -> None:
        async with self.server() as session:
            prompt = "  급함\r\n미완료 보존  (fast)\r\n"
            skipped = await self.call(session, "diary_start", prompt=prompt, title="Fast", plan=["urgent"])
            self.assertTrue(skipped["skipped"])
            self.assertFalse(self.base.exists())
            recorded = await self.call(session, "diary_record_fast", prompt=prompt, title="Fast", status="작업 중", plan=["urgent"], progress=["actual partial result"])
            self.assertEqual(recorded["status"], "작업 중")
            diary_path = self.base / "fixture-A" / "Diary"
            self.assertIn(prompt.encode(), diary_path.read_bytes())
            active = await self.call(session, "diary_list_active")
            self.assertEqual(active["active_count"], 1)
            self.assertEqual(active["entries"][0]["prompt_sha256"], hashlib.sha256(prompt.encode()).hexdigest())
            await self.call(session, "diary_set_status", entry_id=recorded["entry_id"], status="작업 완료", progress=["now finished"])
            self.assertEqual((await self.call(session, "diary_list_active"))["active_count"], 0)

    async def test_nod_precedence_and_scoped_checkpoints_over_stdio(self) -> None:
        async with self.server() as session:
            no_diary = await self.call(session, "diary_record_fast", prompt="nothing (fast) (nod)", title="Nod", status="작업 중")
            self.assertTrue(no_diary["skipped"])
            self.assertFalse(self.base.exists())
            await self.call(session, "diary_register_scope")
            await self.call(session, "diary_register_scope", "fixture-B", parent_thread_id="fixture-A")
            saved = await self.call(session, "checkpoint_save", objective="parent checkpoint canary", last_prompt="exact")
            child = await self.call(session, "checkpoint_read", "fixture-B", parent_thread_id="fixture-A")
            self.assertTrue(child["empty"])
            checkpoint = await self.call(session, "checkpoint_read")
            self.assertEqual(checkpoint["checkpoint_sha256"], saved["checkpoint_sha256"])
            self.assertIn("parent checkpoint canary", checkpoint["content"])
            await self.call(session, "checkpoint_clear", expected_sha256=checkpoint["checkpoint_sha256"], objective_complete=True)
            self.assertTrue((await self.call(session, "checkpoint_read"))["empty"])

    async def test_fast_correction_and_resume_defer_then_preserve_structure(self) -> None:
        async with self.server() as session:
            started = await self.call(session, "diary_start", prompt="original\r\n", title="Correction", plan=["one"])
            diary_path = self.base / "fixture-A" / "Diary"
            before = diary_path.read_bytes()
            correction = (
                "  real correction\r\n"
                f"<!-- CONTINUITY_ENTRY_END {started['entry_id']} -->\n"
                f"<!-- CONTINUITY_PROMPT_END {started['entry_id']} -->\n"
                "- status: 작업 완료\n끝  (fast)\r\n"
            )
            arguments = dict(entry_id=started["entry_id"], prompt=correction, checkpoint_progress=["partial state"])
            skipped = await self.call(session, "diary_correction", **arguments)
            self.assertTrue(skipped["skipped"])
            self.assertEqual(diary_path.read_bytes(), before)
            corrected = await self.call(session, "diary_correction", posthoc=True, **arguments)
            self.assertFalse(corrected["skipped"])
            self.assertEqual(corrected["entry_id"], started["entry_id"])
            self.assertIn(correction.encode(), diary_path.read_bytes())
            active = await self.call(session, "diary_list_active")
            self.assertEqual(active["active_count"], 1)
            self.assertEqual(len(active["entries"]), 1)
            self.assertEqual(active["entries"][0]["status"], "작업 중")
            selected = await self.call(session, "diary_read", block_number=1)
            self.assertIn(correction, selected["prompt"])
            self.assertEqual(selected["entry_id"], started["entry_id"])
            await self.call(session, "diary_set_status", entry_id=started["entry_id"], status="작업 보류")
            paused_bytes = diary_path.read_bytes()
            resume_prompt = "다시 시작\r\n  (fast)\n"
            skipped = await self.call(session, "diary_resume", entry_id=started["entry_id"], resume_prompt=resume_prompt)
            self.assertTrue(skipped["skipped"])
            self.assertEqual(diary_path.read_bytes(), paused_bytes)
            resumed = await self.call(session, "diary_resume", entry_id=started["entry_id"], resume_prompt=resume_prompt, posthoc=True)
            self.assertEqual(resumed["block_number"], 2)
            self.assertEqual(resumed["resumes_entry_id"], started["entry_id"])
            self.assertIn(resume_prompt.encode(), diary_path.read_bytes())
            finished = await self.call(session, "diary_set_status", entry_id=resumed["entry_id"], status="작업 완료")
            self.assertEqual(finished["linked_sources_completed"], [started["entry_id"]])
            active = await self.call(session, "diary_list_active")
            self.assertEqual((active["active_count"], active["paused_count"]), (0, 0))

    async def test_stale_host_bridge_uses_real_mcp_and_emits_single_json(self) -> None:
        async def run_bridge(method: str, thread: str = "fixture-A", **payload) -> tuple[int, bytes, bytes]:
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(SOURCE_ROOT / "scripts" / "continuity_mcp_client.py"),
                method, "--thread-id", thread, "--json", json.dumps(payload, ensure_ascii=False),
                cwd=str(SOURCE_ROOT),
                env={"CONTINUITY_CONFIG": str(self.config), "PATH": os.environ.get("PATH", "")},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            except TimeoutError:
                process.kill()
                await process.communicate()
                raise
            return process.returncode, stdout, stderr

        code, stdout, stderr = await run_bridge("continuity_status")
        self.assertEqual(code, 0, stderr.decode())
        response = json.loads(stdout)
        self.assertFalse(response.get("isError"))
        contents = json.loads("\n".join(item["text"] for item in response["content"] if item["type"] == "text"))
        self.assertEqual(Path(contents["root"]), self.base / "fixture-A")
        self.assertEqual(Path(contents["runtime"]["server_path"]).resolve(), SERVER_PATH)
        self.assertEqual(Path(contents["runtime"]["config_path"]), self.config)
        self.assertFalse(self.base.exists())
        code, stdout, stderr = await run_bridge("__dict__")
        self.assertEqual(code, 1)
        self.assertEqual(stdout, b"")
        # Library warnings may legitimately precede the structured error on
        # stderr; only stdout must be a clean protocol/result channel.
        self.assertFalse(json.loads(stderr.decode().splitlines()[-1])["retried"])
        self.assertFalse(self.base.exists())
        # The verified read-only provenance above gates all bridge test writes.
        created = {}
        for thread in ("fixture-A", "fixture-X"):
            code, stdout, stderr = await run_bridge("diary_start", thread, prompt=f"only-{thread}\r\n", title="Bridge fixture", plan=["verify"])
            self.assertEqual(code, 0, stderr.decode())
            response = json.loads(stdout)
            created[thread] = json.loads("\n".join(item["text"] for item in response["content"] if item["type"] == "text"))
        paths = {thread: self.base / thread / "Diary" for thread in created}
        before = {thread: path.read_bytes() for thread, path in paths.items()}
        code, stdout, stderr = await run_bridge("diary_read", "fixture-X", entry_id=created["fixture-A"]["entry_id"])
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(stdout)["isError"])
        for thread, path in paths.items():
            self.assertEqual(path.read_bytes(), before[thread])
        code, stdout, stderr = await run_bridge("diary_read", entry_id=created["fixture-A"]["entry_id"])
        self.assertEqual(code, 0, stderr.decode())
        response = json.loads(stdout)
        selected = json.loads("\n".join(item["text"] for item in response["content"] if item["type"] == "text"))
        self.assertEqual(selected["prompt"], "only-fixture-A\r\n")
        self.assertEqual(selected["entry_id"], created["fixture-A"]["entry_id"])
        self.assertNotIn("only-fixture-X", str(selected))


if __name__ == "__main__":
    unittest.main()
