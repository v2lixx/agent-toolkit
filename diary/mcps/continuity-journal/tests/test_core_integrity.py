from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.continuity_core import (
    DIARY_HEADER,
    STATUS_COMPLETE,
    STATUS_PAUSED,
    STATUS_WORKING,
    ContinuityStore,
    _entry_content,
    _entry_metadata,
    _extract_entry,
    _extract_field,
    _iter_entries,
    _now,
    _render_v2_entry,
)


class CoreIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ContinuityStore(self.root)
        self.store.protocol_path.write_text("# test protocol\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def start(self, prompt: str = "original", **kwargs):
        return self.store.diary_start(prompt=prompt, title="test", plan=["one"], **kwargs)

    def text(self) -> str:
        with self.store.diary_path.open("r", encoding="utf-8", newline="") as stream:
            return stream.read()

    def write(self, text: str) -> None:
        with self.store.diary_path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(text)

    def render(self, entry_id: str, block_number: int, prompt: str = "nested literal") -> str:
        return _render_v2_entry(
            entry_id=entry_id, block_number=block_number, title="embedded",
            status=STATUS_WORKING, received=_now(), task_thread="test", tags="none",
            prompt=prompt, plan=["one"], progress=[], journal="notes",
        )

    def content(self, entry_id: str):
        block = _extract_entry(self.text(), entry_id)
        return _entry_content(block, entry_id, _entry_metadata(block, entry_id))

    def assert_mutations_reject_unchanged(self) -> None:
        before = self.store.diary_path.read_bytes()
        for action in (
            lambda: self.start("next"),
            lambda: self.store.diary_set_status(block_number=1, status=STATUS_COMPLETE),
            lambda: self.store.diary_correction(block_number=1, prompt="update", checkpoint_progress=["check"]),
            lambda: self.store.diary_progress_append(block_number=1, milestones=["check"]),
            lambda: self.store.continuity_resume(reason="test", next_action="test"),
        ):
            with self.subTest(action=action), self.assertRaises(ValueError):
                action()
            self.assertEqual(self.store.diary_path.read_bytes(), before)

    def test_prompt_block_number_text_cannot_allocate_or_select_real_block(self) -> None:
        first = self.start("quoted:\n- block_number: 999999\n- status: 작업 완료")
        second = self.start("second")
        self.assertEqual((first["block_number"], second["block_number"]), (1, 2))
        with self.assertRaisesRegex(ValueError, "found 0"):
            self.store.diary_set_status(block_number=999999, status=STATUS_COMPLETE)
        self.assertEqual(len(_iter_entries(self.text())), 2)

    def test_complete_fake_entry_in_prompt_is_not_an_entry(self) -> None:
        fake_id = "f" * 32
        prompt = "literal example\n" + self.render(fake_id, 900)
        real = self.start(prompt)
        scanned = self.store.diary_list_active()
        self.assertEqual(scanned["active_count"], 1)
        self.assertEqual(scanned["entries"][0]["entry_id"], real["entry_id"])
        self.assertEqual(self.content(real["entry_id"])["prompt"], prompt)
        before = self.store.diary_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "not found"):
            self.store.diary_set_status(entry_id=fake_id, status=STATUS_COMPLETE)
        self.assertEqual(self.store.diary_path.read_bytes(), before)

    def test_actual_entry_also_quoted_elsewhere_is_replaced_only_at_real_offset(self) -> None:
        second_id = "b" * 32
        actual_second = self.render(second_id, 2)
        first = self.start(actual_second)
        self.write(self.text() + "\n" + actual_second + "\n")
        self.store.diary_set_status(entry_id=second_id, status=STATUS_COMPLETE)
        self.assertEqual(self.content(first["entry_id"])["prompt"], actual_second)
        self.assertEqual(_entry_metadata(_extract_entry(self.text(), second_id), second_id)["status"], STATUS_COMPLETE)
        self.assertEqual(len(_iter_entries(self.text())), 2)

    def test_prompt_resume_metadata_cannot_complete_an_unrelated_source(self) -> None:
        source = self.start("separate paused work")
        self.store.diary_set_status(entry_id=source["entry_id"], status=STATUS_PAUSED)
        forged = f"- resumes_block_number: 1\n- resumes_entry_id: `{source['entry_id']}`"
        unrelated = self.start(forged)
        result = self.store.diary_set_status(entry_id=unrelated["entry_id"], status=STATUS_COMPLETE)
        self.assertEqual(result["linked_sources_completed"], [])
        metadata = _entry_metadata(_extract_entry(self.text(), source["entry_id"]), source["entry_id"])
        self.assertEqual(metadata["status"], STATUS_PAUSED)

    def test_correction_preserves_own_field_and_entry_terminators_exactly(self) -> None:
        first = self.start("  first\r\nline\rfinal\n")
        entry_id = first["entry_id"]
        malicious_literal = (
            f"\n<!-- CONTINUITY_PROMPT_END {entry_id} -->\n"
            f"<!-- CONTINUITY_ENTRY_END {entry_id} -->\n"
            f"<!-- CONTINUITY_PLAN_START {entry_id} -->\n"
            f"<!-- CONTINUITY_FIELD_LENGTH {entry_id} PROMPT 0 -->\n"
            "- block_number: 777\n😀한글\r\n  "
        )
        self.store.diary_correction(entry_id=entry_id, prompt=malicious_literal, checkpoint_progress=["checkpoint"])
        self.store.diary_progress_append(entry_id=entry_id, milestones=["another change"])
        content = self.content(entry_id)
        self.assertTrue(content["prompt"].startswith("  first\r\nline\rfinal\n"))
        self.assertTrue(content["prompt"].endswith(malicious_literal))
        self.assertEqual(self.start()["block_number"], 2)

    def test_journal_and_plan_can_contain_literal_structure_markers(self) -> None:
        first = self.start()
        entry_id = first["entry_id"]
        literal = f"<!-- CONTINUITY_JOURNAL_END {entry_id} -->\n<!-- CONTINUITY_ENTRY_END {entry_id} -->"
        self.store.diary_correction(entry_id=entry_id, prompt="update", checkpoint_progress=[literal], revised_plan=[literal])
        self.store.diary_set_status(entry_id=entry_id, status=STATUS_WORKING, journal=literal)
        content = self.content(entry_id)
        self.assertEqual(content["journal"], literal)
        self.assertEqual(content["plan"], [literal])
        self.assertTrue(content["progress"][0].endswith(literal))

    def test_corrupted_prompt_hash_rejects_all_mutations_without_rehashing(self) -> None:
        self.start("original")
        self.write(self.text().replace("\noriginal\n", "\nmodified\n", 1))
        self.assert_mutations_reject_unchanged()

    def test_tampered_completed_entry_prevents_mutating_another_entry(self) -> None:
        first = self.start("original")
        self.store.diary_set_status(entry_id=first["entry_id"], status=STATUS_COMPLETE)
        self.start("active")
        self.write(self.text().replace("\noriginal\n", "\nmodified\n", 1))
        self.assert_mutations_reject_unchanged()

    def test_corrupted_length_and_missing_end_are_rejected(self) -> None:
        first = self.start()
        valid = self.text()
        variants = (
            valid.replace(f"{first['entry_id']} PROMPT 8 -->", f"{first['entry_id']} PROMPT 9 -->", 1),
            valid.replace(f"<!-- CONTINUITY_ENTRY_END {first['entry_id']} -->", "", 1),
            valid.replace(f"<!-- CONTINUITY_PROMPT_END {first['entry_id']} -->", "", 1),
        )
        for broken in variants:
            with self.subTest(broken=broken):
                self.write(broken)
                self.assert_mutations_reject_unchanged()

    def test_duplicate_entry_ids_and_block_numbers_are_rejected(self) -> None:
        self.start()
        valid = self.text()
        original = _iter_entries(valid)[0][1]
        for extra in (original, self.render("a" * 32, 1)):
            with self.subTest(extra=extra):
                self.write(valid + "\n" + extra)
                self.assert_mutations_reject_unchanged()

    def test_orphan_invalid_or_nested_entry_markers_reject_partial_parse(self) -> None:
        first = self.start()
        valid = self.text()
        for broken in (
            valid + "\n<!-- CONTINUITY_ENTRY_START not-an-id -->\n",
            valid + "\n<!-- CONTINUITY_ENTRY_END " + "b" * 32 + " -->\n",
            valid.replace("### 3. 작업 방향·계획", "<!-- CONTINUITY_ENTRY_START " + "b" * 32 + " -->", 1),
        ):
            with self.subTest(broken=broken):
                self.write(broken)
                self.assert_mutations_reject_unchanged()

    def test_duplicate_metadata_and_identity_mismatch_rejected(self) -> None:
        first = self.start()
        valid = self.text()
        for broken in (
            valid.replace("- block_number: 1", "- block_number: 1\n- block_number: 999", 1),
            valid.replace(f"- entry_id: `{first['entry_id']}`", "- entry_id: `" + "f" * 32 + "`", 1),
            valid.replace("- status: 작업 중", "- status: 작업 완료", 1),
            valid.replace("- block_number: 1", "- block_number: True", 1),
        ):
            with self.subTest(broken=broken):
                self.write(broken)
                self.assert_mutations_reject_unchanged()

    def test_missing_plan_cannot_downgrade_malformed_v2_to_legacy(self) -> None:
        first = self.start()
        entry_id = first["entry_id"]
        text = self.text()
        pattern = r"<!-- CONTINUITY_FIELD_LENGTH " + entry_id + r" PLAN \d+ -->\n<!-- CONTINUITY_PLAN_START " + entry_id + r" -->.*?<!-- CONTINUITY_PLAN_END " + entry_id + r" -->"
        self.write(re.sub(pattern, "", text, count=1, flags=re.DOTALL))
        self.assert_mutations_reject_unchanged()

    def test_old_unframed_v2_remains_readable_and_only_changed_fields_reframe(self) -> None:
        first = self.start("  old\r\n😀\n  ")
        second = self.start("second stays byte-identical")
        unframed = re.sub(r"(?m)^<!-- CONTINUITY_FIELD_LENGTH [^\n]+ -->\n", "", self.text())
        self.write(unframed)
        second_before = _extract_entry(unframed, second["entry_id"])
        self.assertEqual(self.content(first["entry_id"])["prompt"], "  old\r\n😀\n  ")
        self.store.diary_progress_append(entry_id=first["entry_id"], milestones=["changed"])
        self.assertEqual(_extract_entry(self.text(), second["entry_id"]), second_before)
        self.assertNotIn(f"CONTINUITY_FIELD_LENGTH {first['entry_id']} PROMPT", self.text())
        self.assertIn(f"CONTINUITY_FIELD_LENGTH {first['entry_id']} PROGRESS", self.text())

    def test_old_v2_with_fake_entry_inside_prompt_skips_embedded_structure(self) -> None:
        fake_id = "c" * 32
        prompt = f"<!-- CONTINUITY_ENTRY_START {fake_id} -->\n- block_number: 999\n<!-- CONTINUITY_ENTRY_END {fake_id} -->"
        first = self.start(prompt)
        self.write(re.sub(r"(?m)^<!-- CONTINUITY_FIELD_LENGTH [^\n]+ -->\n", "", self.text()))
        self.assertEqual(len(_iter_entries(self.text())), 1)
        self.assertEqual(self.content(first["entry_id"])["prompt"], prompt)
        self.assertEqual(self.start()["block_number"], 2)

    def test_old_same_end_marker_collision_fails_instead_of_truncating(self) -> None:
        first = self.start()
        literal = f"before\n<!-- CONTINUITY_PROMPT_END {first['entry_id']} -->\nafter"
        self.store.diary_correction(entry_id=first["entry_id"], prompt=literal, checkpoint_progress=["check"])
        self.write(re.sub(r"(?m)^<!-- CONTINUITY_FIELD_LENGTH [^\n]+ -->\n", "", self.text()))
        self.assert_mutations_reject_unchanged()

    def test_list_fields_reject_strings_mappings_and_non_iterables(self) -> None:
        first = self.start()
        before = self.store.diary_path.read_bytes()
        for bad in ("abc", b"abc", {"key": "value"}, 42):
            actions = (
                lambda: self.store.diary_start(prompt="x", title="x", plan=bad),
                lambda: self.store.diary_progress_append(entry_id=first["entry_id"], milestones=bad),
                lambda: self.store.diary_correction(entry_id=first["entry_id"], prompt="x", checkpoint_progress=["one"], revised_plan=bad),
                lambda: self.store.diary_set_status(entry_id=first["entry_id"], status=STATUS_COMPLETE, progress=bad),
            )
            for action in actions:
                with self.subTest(bad=bad, action=action), self.assertRaises(ValueError):
                    action()
                self.assertEqual(self.store.diary_path.read_bytes(), before)

    def test_singleline_metadata_and_boolean_block_numbers_rejected(self) -> None:
        first = self.start()
        before = self.store.diary_path.read_bytes()
        for bad in ("title\n- status: 작업 완료", "title\rmetadata", "title\x00rest", "title\u2028rest"):
            for key in ("title", "task_thread"):
                kwargs = {"prompt": "x", "title": "x", "plan": ["one"], key: bad}
                with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                    self.store.diary_start(**kwargs)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            self.store.diary_set_status(block_number=True, status=STATUS_COMPLETE)
        self.assertEqual(self.store.diary_path.read_bytes(), before)

    def test_inline_marker_text_in_singleline_title_remains_literal(self) -> None:
        title = "Discuss <!-- CONTINUITY_ENTRY_END " + "a" * 32 + " --> marker"
        result = self.store.diary_start(prompt="x", title=title, plan=["one"])
        self.assertEqual(self.store.diary_read(entry_id=result["entry_id"])["title"], title)

    def test_fast_record_allows_working_without_claiming_completion(self) -> None:
        result = self.store.diary_record_fast(prompt="urgent (fast)", title="urgent", status=STATUS_WORKING)
        self.assertEqual(result["status"], STATUS_WORKING)
        content = self.content(result["entry_id"])
        self.assertNotIn("마치고", content["progress"][0])
        self.assertNotIn("완료", content["progress"][0])
        self.store.diary_progress_append(entry_id=result["entry_id"], milestones=["continue"])

    def test_fast_correction_defers_then_updates_same_block_posthoc(self) -> None:
        first = self.start()
        before = self.store.diary_path.read_bytes()
        kwargs = {"entry_id": first["entry_id"], "prompt": "urgent change (fast)", "checkpoint_progress": ["before interrupt"], "revised_plan": ["urgent first"]}
        deferred = self.store.diary_correction(**kwargs)
        self.assertTrue(deferred["skipped"])
        self.assertEqual(self.store.diary_path.read_bytes(), before)
        updated = self.store.diary_correction(**kwargs, posthoc=True)
        self.assertEqual(updated["entry_id"], first["entry_id"])
        self.assertEqual(len(_iter_entries(self.text())), 1)
        self.assertTrue(self.content(first["entry_id"])["prompt"].endswith(kwargs["prompt"]))
        self.assertEqual(self.store.diary_list_active()["active_count"], 1)

    def test_fast_resume_defers_then_copies_once_posthoc_and_preserves_links(self) -> None:
        first = self.start()
        self.store.diary_set_status(entry_id=first["entry_id"], status=STATUS_PAUSED)
        before = self.store.diary_path.read_bytes()
        kwargs = {"entry_id": first["entry_id"], "resume_prompt": "resume urgently (fast)"}
        self.assertTrue(self.store.diary_resume(**kwargs)["skipped"])
        self.assertEqual(self.store.diary_path.read_bytes(), before)
        resumed = self.store.diary_resume(**kwargs, posthoc=True)
        self.assertEqual(resumed["resumes_entry_id"], first["entry_id"])
        self.assertEqual(resumed["status"], STATUS_WORKING)
        self.assertEqual(len(_iter_entries(self.text())), 2)
        self.store.diary_set_status(entry_id=resumed["entry_id"], status=STATUS_COMPLETE)
        self.assertEqual(_entry_metadata(_extract_entry(self.text(), first["entry_id"]), first["entry_id"])["status"], STATUS_COMPLETE)

    def test_fast_deferred_unknown_entry_does_not_touch_files(self) -> None:
        empty = ContinuityStore(self.root / "nonexistent")
        self.assertTrue(empty.diary_correction(entry_id="a" * 32, prompt="urgent (fast)", checkpoint_progress=["x"])["skipped"])
        self.assertTrue(empty.diary_resume(entry_id="a" * 32, resume_prompt="urgent (fast)")["skipped"])
        self.assertFalse(empty.root.exists())

    def test_nod_always_wins_over_posthoc_and_fast(self) -> None:
        first = self.start()
        before = self.store.diary_path.read_bytes()
        self.assertEqual(self.store.diary_correction(entry_id=first["entry_id"], prompt="change (fast) (nod)", checkpoint_progress=["x"], posthoc=True)["reason"], "nod")
        self.assertEqual(self.store.diary_resume(entry_id=first["entry_id"], resume_prompt="resume (nod) (fast)", posthoc=True)["reason"], "nod")
        self.assertTrue(self.store.diary_record_fast(prompt="skip (nod)", title="x", status=STATUS_WORKING)["skipped"])
        self.assertEqual(self.store.diary_path.read_bytes(), before)

    def test_posthoc_is_a_real_boolean(self) -> None:
        for value in ("false", 1, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "boolean"):
                self.store.diary_correction(entry_id="a" * 32, prompt="urgent (fast)", checkpoint_progress=["x"], posthoc=value)
            with self.assertRaisesRegex(ValueError, "boolean"):
                self.store.diary_resume(entry_id="a" * 32, resume_prompt="urgent (fast)", posthoc=value)

    def test_selected_read_returns_full_exact_content_only_and_is_read_only(self) -> None:
        original = "  exact\r\n" + "very long " * 120 + "😀\n  "
        first = self.start(original)
        second = self.start("other secret not returned")
        self.store.diary_set_status(entry_id=first["entry_id"], status=STATUS_PAUSED)
        before = self.store.diary_path.read_bytes()
        read = self.store.diary_read(entry_id=first["entry_id"])
        self.assertEqual(read["prompt"], original)
        self.assertEqual(read["status"], STATUS_PAUSED)
        self.assertEqual(read["entry_id"], first["entry_id"])
        self.assertEqual(read["diary_path"], str(self.store.diary_path))
        self.assertEqual(read["plan"], ["one"])
        self.assertNotIn("other secret", str(read))
        self.assertEqual(self.store.diary_read(block_number=first["block_number"]), read)
        self.assertEqual(self.store.diary_path.read_bytes(), before)
        for selectors in ({}, {"entry_id": first["entry_id"], "block_number": 1}, {"entry_id": "f" * 32}, {"block_number": True}):
            with self.subTest(selectors=selectors), self.assertRaises(ValueError):
                self.store.diary_read(**selectors)
        self.assertEqual(self.store.diary_path.read_bytes(), before)

    def test_selected_read_missing_diary_does_not_create_directories(self) -> None:
        empty = ContinuityStore(self.root / "nonexistent")
        with self.assertRaises(ValueError):
            empty.diary_read(block_number=1)
        self.assertFalse(empty.root.exists())

    def test_read_of_existing_legacy_directory_does_not_create_lock(self) -> None:
        self.write(DIARY_HEADER + "\n" + self.render("a" * 32, 1) + "\n")
        self.assertFalse((self.root / ".continuity-journal.lock").exists())
        before = set(self.root.iterdir())
        self.assertEqual(self.store.diary_read(block_number=1)["prompt"], "nested literal")
        self.assertEqual(set(self.root.iterdir()), before)

    def test_recovery_counts_paused_without_expanding_paused_content(self) -> None:
        paused = self.start("private paused body")
        self.store.diary_set_status(entry_id=paused["entry_id"], status=STATUS_PAUSED)
        working = self.start("working")
        original = _entry_content
        expanded = []

        def guarded_content(block, entry_id, meta):
            expanded.append(entry_id)
            if entry_id == paused["entry_id"]:
                raise AssertionError("Recovery must not expand paused body")
            return original(block, entry_id, meta)

        with patch("scripts.continuity_core._entry_content", side_effect=guarded_content):
            result = self.store.continuity_resume(entry_id=working["entry_id"], reason="test", next_action="continue")
        self.assertEqual(result["paused_count"], 1)
        self.assertNotIn(paused["entry_id"], expanded)
        self.assertNotIn("private paused body", str(result))
        self.assertEqual(self.store.diary_list_active()["paused_count"], 1)


if __name__ == "__main__":
    unittest.main()
