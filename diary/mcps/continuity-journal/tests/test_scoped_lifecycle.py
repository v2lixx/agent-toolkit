from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.continuity_core import (
    STATUS_COMPLETE,
    STATUS_PAUSED,
    STATUS_WORKING,
    ContinuityStore,
)
from scripts import continuity_scope


class ScopedLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve() / "Diaries"
        self.protocol = self.base.parent / "PROTOCOL"
        self.protocol.write_text("# Isolated test protocol\n", encoding="utf-8")
        config = self.base.parent / "runtime.json"
        config.write_text(json.dumps({"schema_version": 1, "diary_base": str(self.base),
                                      "protocol_path": str(self.protocol)}), encoding="utf-8")
        self.config_environment = patch.dict(os.environ, {"CONTINUITY_CONFIG": str(config)})
        self.config_environment.start()
        self.addCleanup(self.config_environment.stop)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def store(self, thread: str, parent: str | None = None) -> ContinuityStore:
        # Both storage and protocol come from this test's operator configuration.
        return ContinuityStore.for_thread(thread, parent, base_root=self.base)

    def start(self, store: ContinuityStore, prompt: str = "정확한 요청") -> dict:
        return store.diary_start(prompt=prompt, title="Fixture", plan=["검증"])

    def test_root_child_grandchild_have_independent_blocks_and_checkpoints(self) -> None:
        a = self.store("A")
        a_entry = self.start(a)
        b = self.store("B", "A")
        b_entry = self.start(b)
        c = self.store("C", "B")
        c_entry = self.start(c)
        x = self.store("X")
        x_entry = self.start(x)
        self.assertEqual(b.root, a.root / "Child" / "B")
        self.assertEqual(c.root, b.root / "Child" / "C")
        self.assertEqual({item["block_number"] for item in (a_entry, b_entry, c_entry, x_entry)}, {1})
        for store in (a, b, c, x):
            store.checkpoint_save(objective=store.scope.thread_id, last_prompt="원문")
        parent_before = a.diary_path.read_bytes()
        sibling_before = x.diary_path.read_bytes()
        c.diary_set_status(entry_id=c_entry["entry_id"], status=STATUS_COMPLETE)
        self.assertEqual(a.diary_path.read_bytes(), parent_before)
        self.assertEqual(x.diary_path.read_bytes(), sibling_before)
        self.assertEqual(b.diary_list_active()["entries"][0]["status"], STATUS_WORKING)
        for store in (a, b, c, x):
            self.assertIn("## 목표\n\n" + store.scope.thread_id + "\n", store.checkpoint_read()["content"])
        self.assertFalse((self.base / "DIARY.md").exists())
        self.assertEqual(sorted(path.name for path in self.base.iterdir() if path.is_dir()), ["A", "X"])

    def test_foreign_entry_id_cannot_write_or_recover_other_diaries(self) -> None:
        a = self.store("A")
        a_entry = self.start(a, "parent-private")
        b = self.store("B", "A")
        self.start(b, "child-private")
        before = {store.root: store.diary_path.read_bytes() for store in (a, b)}
        attempts = (
            lambda: b.diary_progress_append(entry_id=a_entry["entry_id"], milestones=["wrong"]),
            lambda: b.diary_set_status(entry_id=a_entry["entry_id"], status=STATUS_COMPLETE),
            lambda: b.continuity_resume(entry_id=a_entry["entry_id"], reason="restore", next_action="continue"),
        )
        for attempt in attempts:
            with self.subTest(attempt=attempt), self.assertRaises(ValueError):
                attempt()
            for store in (a, b):
                self.assertEqual(store.diary_path.read_bytes(), before[store.root])

    def test_recovery_returns_only_current_scope_and_requires_explicit_ambiguity_resolution(self) -> None:
        a = self.store("A")
        a_entry = self.start(a, "parent-secret-canary")
        b = self.store("B", "A")
        b_entry = self.start(b, "child-current")
        paused = self.start(b, "child-paused-secret-canary")
        b.diary_set_status(entry_id=paused["entry_id"], status=STATUS_PAUSED)
        parent_before = a.diary_path.read_bytes()
        result = b.continuity_resume(reason="compaction", next_action="verify")
        self.assertEqual(result["active_count"], 1)
        self.assertEqual(result["paused_count"], 1)
        self.assertEqual(result["recovery_target_entry_id"], b_entry["entry_id"])
        self.assertNotIn("parent-secret-canary", str(result))
        self.assertNotIn("child-paused-secret-canary", str(result))
        self.assertNotIn(a_entry["entry_id"], str(result))
        self.assertEqual(a.diary_path.read_bytes(), parent_before)
        self.start(b, "second current")
        before = b.diary_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Multiple working"):
            b.continuity_resume(reason="compaction", next_action="verify")
        self.assertEqual(b.diary_path.read_bytes(), before)
        explicit = b.continuity_resume(entry_id=b_entry["entry_id"], reason="compaction", next_action="verify")
        self.assertEqual(explicit["recovery_target_entry_id"], b_entry["entry_id"])

    def test_exact_prompt_correction_resume_chain_stays_inside_child(self) -> None:
        a = self.store("A")
        a_entry = self.start(a)
        b = self.store("B", "A")
        prompt = "  first\r\nsecond\rthird\nend  \n"
        entry = self.start(b, prompt)
        correction = "  수정\r\n그대로  \n"
        b.diary_correction(entry_id=entry["entry_id"], prompt=correction, checkpoint_progress=["fixture checkpoint"])
        self.assertIn(prompt.encode(), b.diary_path.read_bytes())
        self.assertIn(correction.encode(), b.diary_path.read_bytes())
        b.diary_set_status(entry_id=entry["entry_id"], status=STATUS_PAUSED)
        resumed = b.diary_resume(entry_id=entry["entry_id"], resume_prompt="재개 원문\r\n")
        b.diary_set_status(entry_id=resumed["entry_id"], status=STATUS_PAUSED)
        second_resume = b.diary_resume(entry_id=resumed["entry_id"], resume_prompt="두 번째 재개")
        parent_before = a.diary_path.read_bytes()
        completed = b.diary_set_status(entry_id=second_resume["entry_id"], status=STATUS_COMPLETE)
        self.assertEqual(completed["linked_sources_completed"], [resumed["entry_id"], entry["entry_id"]])
        self.assertEqual(b.diary_list_active()["active_count"], 0)
        self.assertEqual(b.diary_list_active()["paused_count"], 0)
        self.assertEqual(a.diary_path.read_bytes(), parent_before)
        self.assertEqual(a.diary_list_active()["entries"][0]["entry_id"], a_entry["entry_id"])

    def test_concurrent_same_scope_starts_preserve_every_block(self) -> None:
        self.store("A").register_scope()
        barrier = threading.Barrier(10)

        def start(index: int) -> dict:
            store = self.store("A")
            barrier.wait(timeout=10)
            return self.start(store, f"concurrent-{index}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(start, range(10)))
        self.assertEqual(sorted(item["block_number"] for item in results), list(range(1, 11)))
        active = self.store("A").diary_list_active()
        self.assertEqual(active["active_count"], 10)
        self.assertEqual({item["entry_id"] for item in active["entries"]}, {item["entry_id"] for item in results})

    def test_duplicate_id_concurrent_registration_rejects_one_parent(self) -> None:
        self.store("A").register_scope()
        self.store("X").register_scope()
        first = self.store("duplicate-child", "A")
        second = self.store("duplicate-child", "X")
        barrier = threading.Barrier(2)
        real_mkstemp = continuity_scope.tempfile.mkstemp

        def delayed_metadata_temp(*args, **kwargs):
            if kwargs.get("prefix") == ".scope.json.":
                # Widen the otherwise nondeterministic check/create race. A
                # correct registry lock makes one waiter time out, then the
                # other contender sees the committed identity and rejects.
                try:
                    barrier.wait(timeout=0.25)
                except threading.BrokenBarrierError:
                    pass
            return real_mkstemp(*args, **kwargs)

        def register(store: ContinuityStore) -> str:
            try:
                store.register_scope()
                return "registered"
            except ValueError:
                return "rejected"

        with patch.object(continuity_scope.tempfile, "mkstemp", delayed_metadata_temp):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(register, (first, second)))
        self.assertEqual(sorted(outcomes), ["registered", "rejected"])
        self.assertEqual(sum((store.root / "scope.json").exists() for store in (first, second)), 1)
        self.assertEqual(sum(store.diary_path.exists() for store in (first, second)), 1)

    def test_nod_and_deferred_fast_do_not_create_scope_or_diary(self) -> None:
        for suffix in ("(nod)", "(fast)"):
            result = self.start(self.store("A"), "skip " + suffix)
            self.assertTrue(result["skipped"])
            self.assertFalse(self.base.exists())


if __name__ == "__main__":
    unittest.main()
