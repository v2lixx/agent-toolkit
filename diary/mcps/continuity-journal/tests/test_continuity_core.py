from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from pathlib import Path

from scripts.continuity_core import (
    DIARY_HEADER,
    STATUS_CANCELLED,
    STATUS_COMPLETE,
    STATUS_PAUSED,
    STATUS_WORKING,
    ContinuityStore,
    _parse_numbered,
    _render_numbered,
)


class ContinuityStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "CONTINUITY_PROTOCOL.md").write_text("# Protocol\n", encoding="utf-8")
        (self.root / "DIARY.md").write_text(DIARY_HEADER, encoding="utf-8")
        self.store = ContinuityStore(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def start(self, prompt: str = "정확한 원문") -> dict:
        return self.store.diary_start(
            prompt=prompt,
            title="테스트",
            plan=["첫 작업", "둘째 작업"],
            task_thread="test-task",
        )

    def diary(self) -> str:
        with (self.root / "DIARY.md").open("r", encoding="utf-8", newline="") as stream:
            return stream.read()

    def test_start_writes_canonical_sections_and_preserves_exact_prompt(self) -> None:
        prompt = "  앞 공백\r\n본문\n뒤 공백  \n"
        started = self.start(prompt)
        diary = self.diary()
        self.assertEqual(started["block_number"], 1)
        self.assertEqual(started["status"], STATUS_WORKING)
        for heading in (
            "### 1. 날짜·시간 메타데이터",
            "### 2. 사용자 프롬프트 원문",
            "### 3. 작업 방향·계획",
            "### 4. 진행 상황·체크포인트",
            "### 5. 일기·교훈",
            "### 6. 작업 상태",
        ):
            self.assertIn(heading, diary)
        start_marker = f'<!-- CONTINUITY_PROMPT_START {started["entry_id"]} -->\n'
        end_marker = f'\n<!-- CONTINUITY_PROMPT_END {started["entry_id"]} -->'
        stored = diary.split(start_marker, 1)[1].split(end_marker, 1)[0]
        self.assertEqual(stored, prompt)
        self.assertIn(hashlib.sha256(prompt.encode()).hexdigest(), diary)

    def test_new_blocks_are_numbered_and_appended_at_bottom(self) -> None:
        first = self.start("첫째")
        first_end = self.diary().find(f'CONTINUITY_ENTRY_END {first["entry_id"]}')
        second = self.start("둘째")
        diary = self.diary()
        second_start = diary.find(f'CONTINUITY_ENTRY_START {second["entry_id"]}')
        self.assertEqual((first["block_number"], second["block_number"]), (1, 2))
        self.assertGreater(second_start, first_end)

    def test_terminal_tags_are_detected_from_exact_prompt_and_nod_wins(self) -> None:
        before = (self.root / "DIARY.md").read_bytes()
        nod = self.store.diary_start(
            prompt="인용 (fast)\n실제 작업 (nod)", title="x", plan=["x"]
        )
        fast = self.store.diary_start(prompt="급함 (fast)", title="x", plan=["x"])
        self.assertEqual(nod["reason"], "nod")
        self.assertEqual(fast["reason"], "fast-defers-diary-until-finish")
        self.assertEqual(before, (self.root / "DIARY.md").read_bytes())

    def test_progress_and_correction_update_same_block(self) -> None:
        started = self.start("기존 요청")
        self.store.diary_progress_append(
            entry_id=started["entry_id"], milestones=["기준선 완료"]
        )
        result = self.store.diary_correction(
            entry_id=started["entry_id"],
            prompt="수정 요청 원문",
            checkpoint_progress=["기준선 artifact는 보존됨"],
            revised_plan=["수정 우선", "기존 후속"],
        )
        diary = self.diary()
        self.assertEqual(diary.count("<!-- CONTINUITY_ENTRY_START"), 1)
        # Canonical section order places prompts before progress in the rendered
        # block; the single diary_correction transaction guarantees logical
        # checkpoint-first mutation without creating a partially updated file.
        self.assertIn("체크포인트 — 기준선 artifact", diary)
        self.assertIn("+ [", diary)
        self.assertIn("수정 요청 원문", diary)
        self.assertIn("1. 수정 우선", diary)
        self.assertEqual(
            result["prompt_sha256"],
            hashlib.sha256(
                diary.split(
                    f'<!-- CONTINUITY_PROMPT_START {started["entry_id"]} -->\n', 1
                )[1]
                .split(f'\n<!-- CONTINUITY_PROMPT_END {started["entry_id"]} -->', 1)[0]
                .encode()
            ).hexdigest(),
        )

    def test_multiline_progress_survives_later_append(self) -> None:
        started = self.start()
        milestone = (
            "실행 결과\nartifact: /tmp/synthetic-proof\n\n"
            "  1. 중첩 실행 순서\n    command --no-retry\n마지막 상세"
        )
        self.store.diary_progress_append(
            entry_id=started["entry_id"], milestones=[milestone]
        )
        self.store.diary_progress_append(
            entry_id=started["entry_id"], milestones=["후속 작업 완료"]
        )
        summary = self.store.diary_list_active()["entries"][0]
        self.assertEqual(len(summary["recent_progress"]), 2)
        self.assertTrue(summary["recent_progress"][0].endswith(milestone))
        self.assertTrue(summary["recent_progress"][1].endswith("후속 작업 완료"))

    def test_multiline_plan_and_progress_survive_resume_copy(self) -> None:
        plan = [
            "실행\n중요 옵션: --no-retry\n\n  1. 중첩 계획\n    code()",
            "검증\nartifact hash 확인",
        ]
        milestone = "완료 항목\n재사용할 증거: synthetic-proof\n\n    proof()"
        original = self.store.diary_start(
            prompt="다중행 보존", title="다중행", plan=plan
        )
        self.store.diary_progress_append(
            entry_id=original["entry_id"], milestones=[milestone]
        )
        self.store.diary_set_status(
            entry_id=original["entry_id"], status=STATUS_PAUSED
        )
        resumed = self.store.diary_resume(
            entry_id=original["entry_id"], resume_prompt="이어 진행"
        )
        summary = next(
            entry
            for entry in self.store.diary_list_active()["entries"]
            if entry["entry_id"] == resumed["entry_id"]
        )
        self.assertEqual(summary["plan"], plan)
        self.assertEqual(len(summary["recent_progress"]), 2)
        self.assertTrue(summary["recent_progress"][0].endswith(milestone))

    def test_numbered_fields_round_trip_nested_numbers_blank_lines_and_indent(self) -> None:
        items = [
            "첫 항목\n일반 continuation\n\n1. 내용 속 번호\n2. 다음 번호\n"
            "  1. 들여쓴 번호\n    code line\n        nested code\n마지막 줄",
            "둘째 항목\n\n다음 단락",
        ]
        rendered = _render_numbered(items, empty="없음")
        self.assertEqual(_parse_numbered(rendered), items)

    def test_numbered_parser_retains_legacy_unindented_continuations(self) -> None:
        legacy = "1. 이전 첫 항목\n이전 상세\n\n이전 다음 단락\n2. 이전 둘째 항목\n끝 상세"
        self.assertEqual(
            _parse_numbered(legacy),
            ["이전 첫 항목\n이전 상세\n\n이전 다음 단락", "이전 둘째 항목\n끝 상세"],
        )

    def test_pause_resume_and_completion_updates_both_blocks(self) -> None:
        original = self.start("원래 요청")
        paused = self.store.diary_set_status(
            entry_id=original["entry_id"],
            status=STATUS_PAUSED,
            progress=["다음에는 검증부터"],
            journal="중복 검사를 하지 말 것",
        )
        self.assertEqual(paused["status"], STATUS_PAUSED)
        resumed = self.store.diary_resume(
            block_number=original["block_number"],
            resume_prompt="다시 진행",
            revised_plan=["검증", "마감"],
        )
        self.assertEqual(resumed["block_number"], 2)
        diary = self.diary()
        copied = diary.split(
            f'<!-- CONTINUITY_ENTRY_START {resumed["entry_id"]} -->', 1
        )[1]
        self.assertLess(copied.index("다시 진행"), copied.index("원래 요청"))
        self.assertIn("1번 블록에 대한 작업 재개", copied)
        completed = self.store.diary_set_status(
            entry_id=resumed["entry_id"],
            status=STATUS_COMPLETE,
            progress=["검증 완료"],
            journal="재개 경로 정상",
        )
        self.assertEqual(completed["linked_source_completed"], original["entry_id"])
        self.assertEqual(self.diary().count(f"- status: {STATUS_COMPLETE}"), 2)

    def test_repeated_pause_resume_completion_updates_every_ancestor(self) -> None:
        original = self.start("최초 요청")
        self.store.diary_set_status(
            entry_id=original["entry_id"], status=STATUS_PAUSED
        )
        intermediate = self.store.diary_resume(
            entry_id=original["entry_id"], resume_prompt="첫 재개"
        )
        self.store.diary_set_status(
            entry_id=intermediate["entry_id"], status=STATUS_PAUSED
        )
        newest = self.store.diary_resume(
            entry_id=intermediate["entry_id"], resume_prompt="두 번째 재개"
        )
        self.store.diary_set_status(
            entry_id=newest["entry_id"], status=STATUS_COMPLETE
        )
        self.assertEqual(self.diary().count(f"- status: {STATUS_COMPLETE}"), 3)
        self.assertEqual(self.store.diary_list_active()["entries"], [])

    def test_cancelled_resume_leaves_every_ancestor_paused(self) -> None:
        original = self.start("최초 요청")
        self.store.diary_set_status(
            entry_id=original["entry_id"], status=STATUS_PAUSED
        )
        intermediate = self.store.diary_resume(
            entry_id=original["entry_id"], resume_prompt="첫 재개"
        )
        self.store.diary_set_status(
            entry_id=intermediate["entry_id"], status=STATUS_PAUSED
        )
        newest = self.store.diary_resume(
            entry_id=intermediate["entry_id"], resume_prompt="두 번째 재개"
        )
        self.store.diary_set_status(
            entry_id=newest["entry_id"], status=STATUS_CANCELLED
        )
        summary = self.store.diary_list_active()
        self.assertEqual((summary["active_count"], summary["paused_count"]), (0, 2))
        self.assertEqual(self.diary().count(f"- status: {STATUS_CANCELLED}"), 1)
        self.assertNotIn(f"- status: {STATUS_COMPLETE}", self.diary())

    def test_completion_rejects_ancestor_number_mismatch_without_writing(self) -> None:
        original = self.start()
        self.store.diary_set_status(
            entry_id=original["entry_id"], status=STATUS_PAUSED
        )
        resumed = self.store.diary_resume(
            entry_id=original["entry_id"], resume_prompt="재개"
        )
        corrupted = self.diary().replace(
            f'- resumes_block_number: {original["block_number"]}',
            "- resumes_block_number: 999",
            1,
        )
        self.store.diary_path.write_text(corrupted, encoding="utf-8")
        before = self.store.diary_path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.diary_set_status(
                entry_id=resumed["entry_id"], status=STATUS_COMPLETE
            )
        self.assertEqual(self.store.diary_path.read_bytes(), before)

    def test_completion_rejects_ancestor_cycle_without_writing(self) -> None:
        original = self.start()
        self.store.diary_set_status(
            entry_id=original["entry_id"], status=STATUS_PAUSED
        )
        resumed = self.store.diary_resume(
            entry_id=original["entry_id"], resume_prompt="재개"
        )
        marker = f'- entry_id: `{original["entry_id"]}`'
        corrupted = self.diary().replace(
            marker,
            f'{marker}\n- resumes_block_number: {resumed["block_number"]}\n'
            f'- resumes_entry_id: `{resumed["entry_id"]}`',
            1,
        )
        self.store.diary_path.write_text(corrupted, encoding="utf-8")
        before = self.store.diary_path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.diary_set_status(
                entry_id=resumed["entry_id"], status=STATUS_COMPLETE
            )
        self.assertEqual(self.store.diary_path.read_bytes(), before)

    def test_cancel_does_not_complete(self) -> None:
        started = self.start()
        result = self.store.diary_set_status(
            block_number=started["block_number"], status=STATUS_CANCELLED
        )
        self.assertEqual(result["status"], STATUS_CANCELLED)
        self.assertNotIn(f"- status: {STATUS_COMPLETE}", self.diary())

    def test_fast_one_call_record(self) -> None:
        result = self.store.diary_record_fast(
            prompt="급한 요청 (fast)",
            title="빠른 작업",
            status=STATUS_COMPLETE,
            plan=["즉시 조치"],
            progress=["조치 완료"],
            journal="과잉 검증을 생략했다.",
        )
        diary = self.diary()
        self.assertIn("- tags: fast", diary)
        self.assertIn(result["entry_id"], diary)
        self.assertIn(f"- status: {STATUS_COMPLETE}", diary)

    def test_fast_recorder_still_honours_terminal_nod_precedence(self) -> None:
        before = (self.root / "DIARY.md").read_bytes()
        result = self.store.diary_record_fast(
            prompt="쓰지 말 것 (fast) (nod)",
            title="skip",
            status=STATUS_COMPLETE,
        )
        self.assertTrue(result["skipped"])
        self.assertEqual(before, (self.root / "DIARY.md").read_bytes())

    def test_active_scan_returns_compact_working_and_paused_only(self) -> None:
        working = self.start("W" * 2_000)
        paused = self.start("paused")
        completed = self.start("complete")
        self.store.diary_set_status(entry_id=paused["entry_id"], status=STATUS_PAUSED)
        self.store.diary_set_status(entry_id=completed["entry_id"], status=STATUS_COMPLETE)
        result = self.store.diary_list_active()
        self.assertEqual((result["active_count"], result["paused_count"]), (1, 1))
        ids = {item["entry_id"] for item in result["entries"]}
        self.assertEqual(ids, {working["entry_id"], paused["entry_id"]})
        excerpt = next(item for item in result["entries"] if item["entry_id"] == working["entry_id"])
        self.assertLessEqual(len(excerpt["prompt_excerpt"]), 800)

    def test_concurrent_starts_allocate_unique_block_numbers(self) -> None:
        numbers: list[int] = []
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                numbers.append(self.start(f"parallel-{index}")["block_number"])
            except BaseException as exc:  # pragma: no cover - diagnostic path
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(numbers), list(range(1, 13)))

    def test_legacy_status_alias_finishes_legacy_block(self) -> None:
        entry_id = "a" * 32
        block = f"""<!-- CONTINUITY_ENTRY_START {entry_id} -->
## 2026-01-01 00:00:00 KST — legacy
- entry_id: `{entry_id}`
- status: IN_PROGRESS
- prompt_received_at: 2026-01-01T00:00:00+09:00
- task/thread: old
- tags: none

<!-- CONTINUITY_PROMPT_START {entry_id} -->
원문
<!-- CONTINUITY_PROMPT_END {entry_id} -->
<!-- CONTINUITY_RESULT_START {entry_id} -->
- 작업 중.
<!-- CONTINUITY_RESULT_END {entry_id} -->
<!-- CONTINUITY_CHANGES_START {entry_id} -->
- 예정
<!-- CONTINUITY_CHANGES_END {entry_id} -->
<!-- CONTINUITY_VERIFICATION_START {entry_id} -->
- 예정
<!-- CONTINUITY_VERIFICATION_END {entry_id} -->
<!-- CONTINUITY_LESSONS_START {entry_id} -->
- 예정
<!-- CONTINUITY_LESSONS_END {entry_id} -->
<!-- CONTINUITY_ENTRY_END {entry_id} -->"""
        (self.root / "DIARY.md").write_text(DIARY_HEADER + "\n" + block + "\n", encoding="utf-8")
        result = self.store.diary_finish(
            entry_id=entry_id,
            status="COMPLETE",
            result="완료",
            changes="변경",
            verification="PASS",
            lessons="교훈",
        )
        self.assertEqual(result["status"], STATUS_COMPLETE)
        self.assertIn(f"- status: {STATUS_COMPLETE}", self.diary())
        self.assertIn("완료", self.diary())

    def test_mixed_legacy_block_keeps_multiword_korean_status(self) -> None:
        """Regression: the old resume regex truncated `작업 중` to `작업`."""
        entry_id = "b" * 32
        block = f"""<!-- CONTINUITY_ENTRY_START {entry_id} -->
## 블록 10 - 2026-08-31 03:45:06 KST — mixed current block
- block_number: 10
- entry_id: `{entry_id}`
- status: 작업 중
- prompt_received_at: 2026-08-31T03:45:06+09:00
- task/thread: CM
- tags: none

### 사용자 프롬프트 원문
<!-- CONTINUITY_PROMPT_START {entry_id} -->
혼합 포맷 원문
<!-- CONTINUITY_PROMPT_END {entry_id} -->

### 작업 방향·계획
1. 먼저 할 일

### 진행 상황
1. 현재 진행

### 작업 결과
<!-- CONTINUITY_RESULT_START {entry_id} -->
- 작업 중.
<!-- CONTINUITY_RESULT_END {entry_id} -->
<!-- CONTINUITY_LESSONS_START {entry_id} -->
- 교훈 예정
<!-- CONTINUITY_LESSONS_END {entry_id} -->
<!-- CONTINUITY_ENTRY_END {entry_id} -->"""
        (self.root / "DIARY.md").write_text(DIARY_HEADER + "\n" + block + "\n", encoding="utf-8")
        scanned = self.store.diary_list_active()
        self.assertEqual(scanned["active_count"], 1)
        self.assertEqual(scanned["entries"][0]["status"], STATUS_WORKING)
        resumed = self.store.continuity_resume(reason="압축", next_action="계속")
        self.assertEqual(resumed["active_entries"][0]["status"], STATUS_WORKING)
        self.assertFalse(resumed["recovery_recorded_in_active_block"])

    def test_checkpoint_sha_guard(self) -> None:
        saved = self.store.checkpoint_save(
            objective="목표", last_prompt="중단", completed=["하나"], pending=["둘"]
        )
        read = self.store.checkpoint_read()
        self.assertEqual(saved["checkpoint_sha256"], read["checkpoint_sha256"])
        with self.assertRaisesRegex(ValueError, "SHA changed"):
            self.store.checkpoint_clear(expected_sha256="0" * 64, objective_complete=True)
        with self.assertRaisesRegex(ValueError, "objective_complete"):
            self.store.checkpoint_clear(
                expected_sha256=read["checkpoint_sha256"], objective_complete=False
            )
        self.store.checkpoint_clear(
            expected_sha256=read["checkpoint_sha256"], objective_complete=True
        )
        self.assertEqual((self.root / "checkpoints" / "ACTIVE_CHECKPOINT.md").read_bytes(), b"")

    def test_compaction_resume_returns_only_active_summaries(self) -> None:
        active = self.start("A" * 2_000)
        paused = self.start("PAUSED-BODY-" + "P" * 2_000)
        self.store.diary_set_status(entry_id=paused["entry_id"], status=STATUS_PAUSED)
        self.store.checkpoint_save(objective="목표", last_prompt="중단")
        result = self.store.continuity_resume(reason="요약 복구", next_action="검증부터 진행")
        self.assertFalse(result["checkpoint_empty"])
        self.assertGreater(result["checkpoint_bytes"], 0)
        self.assertTrue(result["checkpoint_requires_explicit_read"])
        self.assertNotIn("checkpoint", result)
        self.assertNotIn("diary", result)
        self.assertEqual(result["active_count"], 1)
        self.assertEqual(result["paused_count"], 1)
        self.assertEqual(result["active_entries"][0]["entry_id"], active["entry_id"])
        self.assertNotIn(paused["entry_id"], repr(result))
        self.assertNotIn("PAUSED-BODY", repr(result))
        self.assertTrue(result["recovery_recorded_in_active_block"])
        self.assertEqual(result["recovery_target_entry_id"], active["entry_id"])
        self.assertIn("컨텍스트 복구", self.diary())

    def test_compaction_resume_targets_explicit_working_entry(self) -> None:
        selected = self.start("선택 대상")
        newest = self.start("다른 병렬 작업")
        result = self.store.continuity_resume(
            reason="선택 복구",
            next_action="선택 작업 계속",
            entry_id=selected["entry_id"],
        )
        diary = self.diary()
        selected_block = diary.split(
            f'<!-- CONTINUITY_ENTRY_START {selected["entry_id"]} -->', 1
        )[1].split(f'<!-- CONTINUITY_ENTRY_END {selected["entry_id"]} -->', 1)[0]
        newest_block = diary.split(
            f'<!-- CONTINUITY_ENTRY_START {newest["entry_id"]} -->', 1
        )[1].split(f'<!-- CONTINUITY_ENTRY_END {newest["entry_id"]} -->', 1)[0]
        self.assertEqual(result["recovery_target_entry_id"], selected["entry_id"])
        self.assertIn("선택 복구", selected_block)
        self.assertNotIn("선택 복구", newest_block)

        with self.assertRaisesRegex(ValueError, "working v2"):
            self.store.continuity_resume(
                reason="잘못된 선택",
                next_action="실행하지 않음",
                entry_id="0" * 32,
            )


if __name__ == "__main__":
    unittest.main()
