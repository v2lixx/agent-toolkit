from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.continuity_scope import ensure_scope, resolve_scope


class ContinuityScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve() / "Diaries"
        self.protocol = Path(self.temp.name).resolve() / "CONTINUITY_PROTOCOL.md"
        self.protocol.write_text("# Isolated protocol\n", encoding="utf-8")
        config = Path(self.temp.name).resolve() / "runtime.json"
        config.write_text(json.dumps({"schema_version": 1, "diary_base": str(self.base),
                                      "protocol_path": str(self.protocol)}), encoding="utf-8")
        self.config_environment = patch.dict(os.environ, {"CONTINUITY_CONFIG": str(config)})
        self.config_environment.start()
        self.addCleanup(self.config_environment.stop)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def resolve(self, thread: str, parent: str | None = None):
        return resolve_scope(thread, parent, base_root=self.base)

    def register(self, thread: str, parent: str | None = None):
        scope = self.resolve(thread, parent)
        ensure_scope(scope)
        return scope

    def test_root_resolution_is_read_only(self) -> None:
        scope = self.resolve("root-01")
        self.assertEqual(scope.root, self.base / "root-01")
        self.assertEqual(scope.diary_path, scope.root / "Diary")
        self.assertEqual(scope.checkpoint_path, scope.root / "checkpoints" / "ACTIVE_CHECKPOINT.md")
        self.assertEqual(scope.protocol_path, self.protocol)
        self.assertIsNone(scope.parent_thread_id)
        self.assertEqual(scope.ancestor_thread_ids, ())
        self.assertFalse(self.base.exists())

    def test_default_base_uses_operator_config_not_environment_thread_fallback(self) -> None:
        with patch("scripts.continuity_scope.Path.home", return_value=Path(self.temp.name)), patch.dict(
            os.environ, {"CODEX_THREAD_ID": "not-the-current-id", "PARENT_THREAD_ID": "wrong"}
        ):
            scope = resolve_scope("explicit-id")
        self.assertEqual(scope.root, self.base / "explicit-id")
        self.assertIsNone(scope.parent_thread_id)

    def test_parent_and_current_id_are_found_in_one_directory_scan(self) -> None:
        from scripts import continuity_scope
        self.register("A")
        self.register("B", "A")
        original = continuity_scope._scope_directories
        with patch.object(continuity_scope, "_scope_directories", wraps=original) as scan:
            scope = self.resolve("C", "B")
        self.assertEqual(scope.ancestor_thread_ids, ("A", "B"))
        self.assertEqual(scan.call_count, 1)

    def test_siblings_and_grandchild_discover_only_immediate_parent(self) -> None:
        root = self.register("A")
        b = self.register("B", "A")
        sibling = self.register("D", "A")
        c = self.resolve("C", "B")
        self.assertEqual(b.root, root.root / "Child" / "B")
        self.assertEqual(sibling.root, root.root / "Child" / "D")
        self.assertEqual(c.root, b.root / "Child" / "C")
        self.assertEqual(c.ancestor_thread_ids, ("A", "B"))
        self.assertEqual(c.metadata()["lineage"], ["A", "B", "C"])
        self.assertEqual(c.metadata()["root_thread_id"], "A")
        self.assertFalse(c.root.exists())

    def test_registration_writes_only_atomic_identity_and_is_idempotent(self) -> None:
        scope = self.register("A")
        path = scope.root / "scope.json"
        first = path.read_bytes()
        self.assertEqual(json.loads(first), scope.metadata())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        ensure_scope(scope)
        self.assertEqual(path.read_bytes(), first)
        self.assertFalse(scope.diary_path.exists())
        self.assertFalse(scope.checkpoint_path.exists())
        self.assertEqual(list(scope.root.iterdir()), [path])

    def test_existing_diary_without_metadata_can_register_and_parent(self) -> None:
        directory = self.base / "A"
        directory.mkdir(parents=True)
        (directory / "Diary").write_bytes(b"private diary body")
        with patch.object(Path, "read_text", side_effect=AssertionError("must not read Diary body")):
            child = self.resolve("B", "A")
            ensure_scope(self.resolve("A"))
        self.assertEqual(child.ancestor_thread_ids, ("A",))
        self.assertEqual((directory / "Diary").read_bytes(), b"private diary body")

    def test_missing_or_unregistered_parent_fails_without_creating(self) -> None:
        for make_directory in (False, True):
            if make_directory:
                (self.base / "A").mkdir(parents=True)
            with self.subTest(make_directory=make_directory), self.assertRaisesRegex(ValueError, "missing|registered"):
                self.resolve("B", "A")
        self.assertFalse((self.base / "A" / "Child").exists())

    def test_duplicate_parent_is_ambiguous(self) -> None:
        self.register("A")
        self.register("B", "A")
        duplicate = self.base / "B"
        duplicate.mkdir()
        (duplicate / "Diary").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.resolve("C", "B")

    def test_existing_id_cannot_be_reparented_or_recreated_as_root(self) -> None:
        self.register("A")
        self.register("B", "A")
        self.register("X")
        for parent in (None, "X"):
            with self.subTest(parent=parent), self.assertRaisesRegex(ValueError, "another Diary location"):
                self.resolve("B", parent)

    def test_invalid_and_missing_ids_fail_before_io(self) -> None:
        invalid = (None, "", " ", "/root", "A/B", "../A", ".", "..", "-abc", "x\\y", "한글", "x\n", "x" * 129, 123)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.resolve(value)
        for value in invalid[1:]:
            with self.subTest(parent=value), self.assertRaises(ValueError):
                self.resolve("A", value)
        self.assertFalse(self.base.exists())

    def test_safe_uuid_and_stable_ids_are_allowed(self) -> None:
        for thread in ("7bbba208-c1a6-4b4b-960f-dbc1cb274f70", "Task_2", "x" * 128):
            with self.subTest(thread=thread):
                self.assertEqual(self.resolve(thread).thread_id, thread)

    def test_self_parent_and_ancestor_cycle_fail(self) -> None:
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.resolve("A", "A")
        self.register("A")
        self.register("B", "A")
        with self.assertRaisesRegex(ValueError, "cycle|repeated"):
            self.resolve("A", "B")

    def test_repeated_ids_in_discovered_parent_path_fail(self) -> None:
        directory = self.base / "A" / "Child" / "A" / "Child" / "B"
        directory.mkdir(parents=True)
        (directory / "Diary").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cycle|repeated"):
            self.resolve("C", "B")

    def test_scope_identity_metadata_mismatch_or_malformed_fails(self) -> None:
        scope = self.register("A")
        metadata_path = scope.root / "scope.json"
        for contents in (
            "{not-json",
            "[]",
            json.dumps({**scope.metadata(), "thread_id": "other"}),
            json.dumps({**scope.metadata(), "canonical_directory": "/different"}),
            json.dumps({**scope.metadata(), "schema_version": 99}),
        ):
            metadata_path.write_text(contents, encoding="utf-8")
            with self.subTest(contents=contents), self.assertRaisesRegex(ValueError, "metadata"):
                self.resolve("A")
            with self.assertRaises(ValueError):
                ensure_scope(scope)
            self.assertEqual(metadata_path.read_text(encoding="utf-8"), contents)

    def test_parent_metadata_mismatch_fails(self) -> None:
        scope = self.register("A")
        (scope.root / "scope.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "metadata mismatch"):
            self.resolve("B", "A")

    def test_scope_fields_cannot_be_forged(self) -> None:
        scope = self.resolve("A")
        for forged in (
            replace(scope, thread_id="B"),
            replace(scope, diary_path=Path(self.temp.name) / "outside"),
            replace(scope, protocol_path=Path("/wrong")),
        ):
            with self.subTest(forged=forged), self.assertRaisesRegex(ValueError, "canonical identity"):
                ensure_scope(forged)
        self.assertFalse(self.base.exists())

    def test_legacy_diary_md_requires_migration(self) -> None:
        root = self.base / "A"
        root.mkdir(parents=True)
        (root / "Diary.md").write_text("old content", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Migration required"):
            self.resolve("A")
        self.assertFalse((root / "Diary").exists())
        self.assertFalse((root / "scope.json").exists())

    def test_both_diary_filenames_require_reconciliation_without_overwrite(self) -> None:
        root = self.base / "A"
        root.mkdir(parents=True)
        for name in ("Diary", "Diary.md"):
            (root / name).write_text(name, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Reconciliation required"):
            self.resolve("A")
        self.assertFalse((root / "scope.json").exists())
        for name in ("Diary", "Diary.md"):
            self.assertEqual((root / name).read_text(encoding="utf-8"), name)

    def test_symlink_base_and_scope_directory_are_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        self.base.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.resolve("A")
        self.base.unlink()
        self.base.mkdir()
        (self.base / "A").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.resolve("A")
        with self.assertRaises(ValueError):
            self.resolve("B", "A")
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_files_and_checkpoint_directory_are_rejected(self) -> None:
        root = self.base / "A"
        root.mkdir(parents=True)
        outside = Path(self.temp.name) / "outside"
        outside.write_text("unchanged", encoding="utf-8")
        for name in ("Diary", "scope.json", ".continuity-journal.lock", "checkpoints"):
            target = root / name
            target.symlink_to(outside)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "symlink"):
                self.resolve("A")
            target.unlink()
        self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged")

    def test_child_container_symlink_is_not_followed(self) -> None:
        root = self.register("A")
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (root.root / "Child").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.resolve("B", "A")
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_non_file_diary_and_non_directory_scope_rejected(self) -> None:
        self.base.mkdir()
        (self.base / "A").write_text("file", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not a directory"):
            self.resolve("A")
        root = self.base / "B"
        (root / "Diary").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            self.resolve("B")

    def test_mutation_rechecks_paths_after_resolve(self) -> None:
        scope = self.resolve("A")
        self.base.mkdir()
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        scope.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            ensure_scope(scope)
        self.assertEqual(list(outside.iterdir()), [])

    def test_traversal_base_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "traversal"):
            resolve_scope("A", base_root=self.base / ".." / "elsewhere")

    def test_cross_parent_simultaneous_same_id_registers_only_one_scope(self) -> None:
        self.register("A")
        self.register("B")
        first = self.resolve("same-id", "A")
        second = self.resolve("same-id", "B")
        ready = threading.Barrier(2)
        outcomes = []
        first_committing = threading.Event()
        release_first = threading.Event()
        original_replace = os.replace

        def delayed_replace(source, destination):
            if Path(destination) == first.root / "scope.json":
                first_committing.set()
                if not release_first.wait(timeout=5):
                    raise AssertionError("test registration release timed out")
            return original_replace(source, destination)

        def worker(scope):
            try:
                ready.wait(timeout=5)
                ensure_scope(scope)
                outcomes.append((scope.parent_thread_id, "ok"))
            except Exception as exc:
                outcomes.append((scope.parent_thread_id, exc))

        with patch("scripts.continuity_scope.os.replace", side_effect=delayed_replace):
            # Release the first worker into registration before launching the
            # contender; the paused atomic commit creates the formerly unsafe
            # check/create window deterministically.
            one = threading.Thread(target=worker, args=(first,))
            one.start()
            ready.wait(timeout=5)
            self.assertTrue(first_committing.wait(timeout=5))
            two = threading.Thread(target=worker, args=(second,))
            two.start()
            ready.wait(timeout=5)
            try:
                two.join(timeout=0.05)
                self.assertTrue(two.is_alive(), "contender must wait for registry commit")
            finally:
                release_first.set()
                one.join(timeout=5)
                two.join(timeout=5)
        self.assertFalse(one.is_alive())
        self.assertFalse(two.is_alive())
        self.assertIn(("A", "ok"), outcomes)
        errors = [result for _, result in outcomes if isinstance(result, Exception)]
        self.assertEqual(len(errors), 1, outcomes)
        self.assertIsInstance(errors[0], ValueError)
        self.assertIn("another Diary location", str(errors[0]))
        self.assertTrue((first.root / "scope.json").exists())
        self.assertFalse((second.root / "scope.json").exists())

    def test_registry_lock_symlink_and_directory_are_rejected(self) -> None:
        scope = self.resolve("A")
        self.base.mkdir()
        outside = Path(self.temp.name) / "outside"
        outside.write_text("unchanged", encoding="utf-8")
        lock = self.base / ".scope-registry.lock"
        lock.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            ensure_scope(scope)
        lock.unlink()
        lock.mkdir()
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            ensure_scope(scope)
        self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged")
        self.assertFalse(scope.root.exists())

    def test_existing_registration_does_not_acquire_registry_lock(self) -> None:
        scope = self.register("A")
        with patch("scripts.continuity_scope._registry_locked", side_effect=AssertionError("normal write must not lock registry")):
            self.assertEqual(ensure_scope(scope), scope)


if __name__ == "__main__":
    unittest.main()
