"""Atomic numbered diary and checkpoint operations for Codex continuity."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
DIARY_HEADER = (
    "# Codex 작업 일기\n\n"
    "이 파일은 MCP runtime.json이 지정한 연속성 규칙 정본에 따라 누적한다. "
    "신규 번호 블록은 파일 아래에 추가하며, 공개 저장소나 배포 산출물에 포함하지 않는다.\n"
)

STATUS_WORKING = "작업 중"
STATUS_COMPLETE = "작업 완료"
STATUS_CANCELLED = "작업 취소"
STATUS_PAUSED = "작업 보류"
ALLOWED_STATUSES = {
    STATUS_WORKING,
    STATUS_COMPLETE,
    STATUS_CANCELLED,
    STATUS_PAUSED,
}
STATUS_ALIASES = {
    "IN_PROGRESS": STATUS_WORKING,
    "WORKING": STATUS_WORKING,
    "COMPLETE": STATUS_COMPLETE,
    "COMPLETED": STATUS_COMPLETE,
    "CANCELLED": STATUS_CANCELLED,
    "CANCELED": STATUS_CANCELLED,
    "PAUSED": STATUS_PAUSED,
    "PARTIAL": STATUS_PAUSED,
    "BLOCKED": STATUS_PAUSED,
    STATUS_WORKING: STATUS_WORKING,
    STATUS_COMPLETE: STATUS_COMPLETE,
    STATUS_CANCELLED: STATUS_CANCELLED,
    STATUS_PAUSED: STATUS_PAUSED,
}
ALLOWED_CHECKPOINT_STATUSES = {"PAUSED", "IN_PROGRESS", "BLOCKED"}
MAX_FIELD_CHARS = 300_000
SUMMARY_PROMPT_CHARS = 800
SUMMARY_JOURNAL_CHARS = 800
SUMMARY_PROGRESS_ITEMS = 8


def _clean(value: str, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if len(value) > MAX_FIELD_CHARS:
        raise ValueError(f"{field} exceeds {MAX_FIELD_CHARS} characters")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


def _exact_prompt(value: str, field: str = "prompt") -> str:
    """Validate without normalising or trimming user-authored prompt bytes."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if len(value) > MAX_FIELD_CHARS:
        raise ValueError(f"{field} exceeds {MAX_FIELD_CHARS} characters")
    return value


def _singleline(value: str, field: str, *, allow_empty: bool = False) -> str:
    value = _clean(value, field, allow_empty=allow_empty)
    if any(character in value for character in ("\n", "\x00", "\u2028", "\u2029")):
        raise ValueError(f"{field} must be a single line")
    return value.strip()


def _list(values: Iterable[str] | None, field: str, *, allow_empty: bool = True) -> list[str]:
    if values is None:
        if allow_empty:
            return []
        raise ValueError(f"{field} must contain at least one item")
    if isinstance(values, (str, bytes, bytearray, dict)) or not isinstance(values, Iterable):
        raise ValueError(f"{field} must be a list/iterable of strings, not a scalar or mapping")
    result = [_clean(value, f"{field}[{index}]").strip() for index, value in enumerate(values)]
    if not result and not allow_empty:
        raise ValueError(f"{field} must contain at least one item")
    return result


def _now() -> datetime:
    return datetime.now(KST)


def _display_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S KST")


def _iso_time(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    # newline="" disables universal-newline conversion so exact prompt CR/LF
    # bytes remain stable across later read-modify-write operations.
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _locked(root: Path):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = root / ".continuity-journal.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _append_newest(diary: str, block: str) -> str:
    """Append at the physical bottom without reordering any legacy entry."""
    if len(_scan_entries(block)) != 1:
        raise ValueError("A new Diary append must contain exactly one valid entry")
    if not diary.strip():
        diary = DIARY_HEADER
    return f"{diary.rstrip()}\n\n{block.rstrip()}\n"


def _field_markers(entry_id: str, field: str) -> tuple[str, str]:
    return (
        f"<!-- CONTINUITY_{field}_START {entry_id} -->",
        f"<!-- CONTINUITY_{field}_END {entry_id} -->",
    )


def _render_field(entry_id: str, field: str, content: str, *, exact: bool = False) -> str:
    start, end = _field_markers(entry_id, field)
    value = content if exact else content.rstrip()
    # Python Unicode-codepoint length, NOT bytes or UTF-16 code units.  The file
    # is always decoded without newline conversion.  The old start/end strings
    # stay readable while lengths make even their literal occurrence safe.
    frame = f"<!-- CONTINUITY_FIELD_LENGTH {entry_id} {field} {len(value)} -->"
    return f"{frame}\n{start}\n{value}\n{end}"


def _entry_markers(entry_id: str) -> tuple[str, str]:
    return (
        f"<!-- CONTINUITY_ENTRY_START {entry_id} -->",
        f"<!-- CONTINUITY_ENTRY_END {entry_id} -->",
    )


def _render_numbered(items: Iterable[str], *, empty: str) -> str:
    values = list(items)
    if not values:
        return f"- {empty}"
    rendered = []
    for index, value in enumerate(values, 1):
        prefix = f"{index}. "
        lines = value.split("\n")
        rendered.append(prefix + lines[0])
        # Indent continuation lines, including nested numbered lists, so they
        # cannot be confused with another top-level item on the next update.
        rendered.extend(" " * len(prefix) + line for line in lines[1:])
    return "\n".join(rendered)


def _normalise_status(status: str) -> str:
    value = _singleline(status, "status")
    normalised = STATUS_ALIASES.get(value, STATUS_ALIASES.get(value.upper()))
    if normalised is None:
        raise ValueError(f"status must be one of {sorted(ALLOWED_STATUSES)}")
    return normalised


def _render_v2_entry(
    *,
    entry_id: str,
    block_number: int,
    title: str,
    status: str,
    received: datetime,
    task_thread: str,
    tags: str,
    prompt: str,
    plan: list[str],
    progress: list[str],
    journal: str,
    resumes_block_number: int | None = None,
    resumes_entry_id: str | None = None,
) -> str:
    title = _singleline(title, "title")
    task_thread = _singleline(task_thread, "task_thread")
    tags = _singleline(tags, "tags")
    start, end = _entry_markers(entry_id)
    resume_text = (
        f" : {resumes_block_number}번 블록에 대한 작업 재개"
        if resumes_block_number is not None
        else ""
    )
    lines = [
        start,
        f"## 블록 {block_number} - {_display_time(received)}{resume_text} — {title}",
        "### 1. 날짜·시간 메타데이터",
        f"- block_number: {block_number}",
        f"- entry_id: `{entry_id}`",
        f"- status: {status}",
        f"- prompt_received_at: {_iso_time(received)}",
        f"- task/thread: {task_thread}",
        f"- tags: {tags}",
        f"- prompt_sha256: `{_sha(prompt.encode('utf-8'))}`",
    ]
    if resumes_block_number is not None:
        lines.append(f"- resumes_block_number: {resumes_block_number}")
    if resumes_entry_id is not None:
        lines.append(f"- resumes_entry_id: `{resumes_entry_id}`")
    lines.extend(
        [
            "",
            "### 2. 사용자 프롬프트 원문",
            "",
            _render_field(entry_id, "PROMPT", prompt, exact=True),
            "",
            "### 3. 작업 방향·계획",
            "",
            _render_field(entry_id, "PLAN", _render_numbered(plan, empty="계획 없음.")),
            "",
            "### 4. 진행 상황·체크포인트",
            "",
            _render_field(
                entry_id,
                "PROGRESS",
                _render_numbered(progress, empty="아직 기록된 주요 진행 상황 없음."),
            ),
            "",
            "### 5. 일기·교훈",
            "",
            _render_field(entry_id, "JOURNAL", journal or "- 작업 완료 후 기록."),
            "",
            "### 6. 작업 상태",
            "",
            _render_field(entry_id, "STATUS", status),
            end,
        ]
    )
    return "\n".join(lines)


def _parse_received(received_at: str | None) -> datetime:
    if received_at is None:
        return _now()
    parsed = datetime.fromisoformat(_singleline(received_at, "received_at"))
    if parsed.tzinfo is None:
        raise ValueError("received_at must include a timezone offset")
    return parsed.astimezone(KST)


_ENTRY_START_RE = re.compile(r"<!-- CONTINUITY_ENTRY_START ([0-9a-f]{32}) -->\Z")
_FIELD_START_RE = re.compile(r"<!-- CONTINUITY_([A-Z]+)_START ([0-9a-f]{32}) -->\Z")
_FIELD_LENGTH_RE = re.compile(r"<!-- CONTINUITY_FIELD_LENGTH ([0-9a-f]{32}) ([A-Z]+) (\d+) -->\Z")
_FIELDS = {"PROMPT", "PLAN", "PROGRESS", "JOURNAL", "STATUS", "RESULT", "CHANGES", "VERIFICATION", "LESSONS"}


def _line_at(text: str, position: int) -> tuple[str, int]:
    end = text.find("\n", position)
    return (text[position:], len(text)) if end < 0 else (text[position:end], end + 1)


def _parse_entry_at(text: str, left: int) -> dict[str, Any]:
    """Parse structure sequentially; payloads are opaque, never regex input."""
    line, position = _line_at(text, left)
    match = _ENTRY_START_RE.fullmatch(line)
    if not match:
        raise ValueError("Malformed diary entry start marker")
    entry_id = match.group(1)
    fields: dict[str, tuple[int, int, int, int]] = {}
    header_end: int | None = None
    while position < len(text):
        field_left = position
        line, following = _line_at(text, position)
        if line == _entry_markers(entry_id)[1]:
            if "PROMPT" not in fields:
                raise ValueError("Malformed diary entry: missing PROMPT field")
            return {
                "entry_id": entry_id, "left": left, "right": position + len(line),
                "header": text[left:header_end if header_end is not None else position],
                "fields": fields,
            }
        length_match = _FIELD_LENGTH_RE.fullmatch(line)
        content_length: int | None = None
        if length_match:
            marker_id, field, length = length_match.groups()
            if marker_id != entry_id or field not in _FIELDS:
                raise ValueError("Mismatched Diary field length identity")
            content_length = int(length)
            position = following
            line, following = _line_at(text, position)
            if line != _field_markers(entry_id, field)[0]:
                raise ValueError("Field length frame is not followed by its start marker")
        field_match = _FIELD_START_RE.fullmatch(line)
        if field_match:
            field, marker_id = field_match.groups()
            if marker_id != entry_id or field not in _FIELDS or field in fields:
                raise ValueError("Mismatched or duplicate Diary field")
            if header_end is None:
                header_end = field_left
            content_left = following
            end_marker = _field_markers(entry_id, field)[1]
            if content_length is not None:
                content_right = content_left + content_length
                ending = "\n" + end_marker
                if not text.startswith(ending, content_right):
                    raise ValueError(f"Diary field length/end mismatch: {field}")
                field_right = content_right + len(ending)
            else:
                # Legacy fields had exactly one framing LF at each edge.  A
                # same-field terminator inside old content is ambiguous; later
                # structural/hash validation rejects it instead of guessing.
                end_match = re.search(r"(?m)^" + re.escape(end_marker) + r"(?=\n|\Z)", text[content_left:])
                if end_match is None:
                    raise ValueError(f"Missing Diary field end: {field}")
                marker_left = content_left + end_match.start()
                content_right = marker_left - 1 if marker_left > content_left and text[marker_left - 1] == "\n" else marker_left
                field_right = marker_left + len(end_marker)
            if field_right < len(text) and text[field_right] != "\n":
                raise ValueError("Diary field end is not on its own line")
            fields[field] = (field_left, field_right, content_left, content_right)
            position = field_right + (field_right < len(text))
            continue
        if line.lstrip().startswith("<!-- CONTINUITY_"):
            raise ValueError("Malformed/nested Diary structure outside a field")
        if header_end is None and line.startswith("### ") and line != "### 1. 날짜·시간 메타데이터":
            header_end = position
        position = following
    raise ValueError(f"Missing diary entry end: {entry_id}")


def _scan_entries(diary: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_numbers: set[int] = set()
    position = 0
    while position < len(diary):
        line, following = _line_at(diary, position)
        if _ENTRY_START_RE.fullmatch(line):
            parsed = _parse_entry_at(diary, position)
            entry_id = parsed["entry_id"]
            block = diary[parsed["left"]:parsed["right"]]
            meta = _metadata_from_parsed(parsed)
            if entry_id in seen_ids:
                raise ValueError(f"Duplicate Diary entry ID: {entry_id}")
            seen_ids.add(entry_id)
            if meta["block_number"] is not None:
                if meta["block_number"] in seen_numbers:
                    raise ValueError(f"Duplicate Diary block number: {meta['block_number']}")
                seen_numbers.add(meta["block_number"])
            prompt_span = parsed["fields"]["PROMPT"]
            prompt = diary[prompt_span[2]:prompt_span[3]]
            if meta["prompt_sha256"] is not None and _sha(prompt.encode("utf-8")) != meta["prompt_sha256"]:
                raise ValueError(f"Diary prompt SHA-256 integrity mismatch: {entry_id}")
            if meta["v2"]:
                required = {"PROMPT", "PLAN", "PROGRESS", "JOURNAL", "STATUS"}
                if not required <= parsed["fields"].keys():
                    raise ValueError("Malformed v2 Diary: missing required field")
                span = parsed["fields"]["STATUS"]
                if _normalise_status(diary[span[2]:span[3]]) != meta["status"]:
                    raise ValueError("Diary status field/metadata mismatch")
            parsed["block"] = block
            entries.append(parsed)
            position = parsed["right"]
        else:
            if line.lstrip().startswith("<!-- CONTINUITY_"):
                raise ValueError("Malformed/unmatched Diary marker outside an entry")
            position = following
    return entries


def _extract_entry(diary: str, entry_id: str) -> str:
    for parsed in _scan_entries(diary):
        if parsed["entry_id"] == entry_id:
            return parsed["block"]
    raise ValueError(f"diary entry not found: {entry_id}")


def _replace_entry(diary: str, entry_id: str, replacement: str) -> str:
    for parsed in _scan_entries(diary):
        if parsed["entry_id"] == entry_id:
            replacement_entries = _scan_entries(replacement)
            if len(replacement_entries) != 1 or replacement_entries[0]["entry_id"] != entry_id:
                raise ValueError("Replacement must contain exactly its original Diary identity")
            return diary[:parsed["left"]] + replacement + diary[parsed["right"]:]
    raise ValueError(f"diary entry not found: {entry_id}")


def _extract_field(block: str, entry_id: str, field: str, *, exact: bool = False) -> str:
    parsed = _parse_entry_at(block, 0)
    if parsed["entry_id"] != entry_id or field not in parsed["fields"]:
        raise ValueError(f"entry field missing: {field}")
    _, _, left, right = parsed["fields"][field]
    value = block[left:right]
    return value if exact else value.strip("\n")


def _replace_field(
    block: str,
    entry_id: str,
    field: str,
    content: str,
    *,
    exact: bool = False,
) -> str:
    parsed = _parse_entry_at(block, 0)
    if parsed["entry_id"] != entry_id or field not in parsed["fields"]:
        raise ValueError(f"entry field missing: {field}")
    left, right, _, _ = parsed["fields"][field]
    return block[:left] + _render_field(entry_id, field, content, exact=exact) + block[right:]


def _iter_entries(diary: str) -> list[tuple[str, str]]:
    return [(parsed["entry_id"], parsed["block"]) for parsed in _scan_entries(diary)]


def _metadata_from_parsed(parsed: dict[str, Any]) -> dict[str, Any]:
    header = parsed["header"]
    entry_id = parsed["entry_id"]
    raw = {}
    for key, value in re.findall(r"(?m)^- ([a-z_0-9/]+): (.*)$", header):
        if key in raw:
            raise ValueError(f"Duplicate Diary metadata key: {key}")
        raw[key] = value
    if raw.get("entry_id") != f"`{entry_id}`":
        raise ValueError("Diary entry marker/metadata identity mismatch")

    def match(pattern: str) -> str | None:
        found = re.search(pattern, header, re.MULTILINE)
        return found.group(1) if found else None

    status = _normalise_status(raw.get("status", ""))
    block_number_text = match(r"^- block_number: (\d+)$")
    resume_number_text = match(r"^- resumes_block_number: (\d+)$")
    for key, value in (("block_number", block_number_text), ("resumes_block_number", resume_number_text)):
        if key in raw and (value is None or int(value) < 1):
            raise ValueError(f"Invalid Diary metadata: {key}")
    resume_id = match(r"^- resumes_entry_id: `([0-9a-f]{32})`$")
    if (resume_number_text is None) != (resume_id is None) or ("resumes_entry_id" in raw and resume_id is None):
        raise ValueError("Diary resume metadata requires a valid ID/number pair")
    v2 = "PLAN" in parsed["fields"] or "### 1. 날짜·시간 메타데이터" in header.splitlines()
    prompt_hash = match(r"^- prompt_sha256: `([0-9a-f]{64})`$")
    if (v2 or "prompt_sha256" in raw) and prompt_hash is None:
        raise ValueError("Diary prompt SHA-256 metadata missing or malformed")
    if v2 and block_number_text is None:
        raise ValueError("v2 Diary block number is missing")
    title_match = re.search(r"^## .*? — (.+)$", header, re.MULTILINE)
    return {
        "entry_id": entry_id,
        "block_number": int(block_number_text) if block_number_text else None,
        "title": title_match.group(1) if title_match else "제목 없음",
        "status": status,
        "received_at": match(r"^- prompt_received_at: (.+)$") or "",
        "task_thread": match(r"^- task/thread: (.+)$") or "",
        "tags": match(r"^- tags: (.+)$") or "none",
        "resumes_block_number": int(resume_number_text) if resume_number_text else None,
        "resumes_entry_id": resume_id,
        "prompt_sha256": prompt_hash,
        "v2": v2,
    }


def _entry_metadata(block: str, entry_id: str) -> dict[str, Any]:
    parsed = _parse_entry_at(block, 0)
    if parsed["entry_id"] != entry_id:
        raise ValueError("Diary entry identity mismatch")
    return _metadata_from_parsed(parsed)


def _replace_metadata(block: str, entry_id: str, key: str, value: str) -> str:
    parsed = _parse_entry_at(block, 0)
    if parsed["entry_id"] != entry_id:
        raise ValueError("Diary entry identity mismatch")
    header = parsed["header"]
    replacement, count = re.subn(r"(?m)^- " + re.escape(key) + r": .+$", lambda _: f"- {key}: {value}", header)
    if count != 1:
        raise ValueError(f"Diary metadata missing or duplicated: {key}")
    return replacement + block[len(header):]


def _next_block_number(diary: str) -> int:
    entries = _iter_entries(diary)
    explicit = [_entry_metadata(block, entry_id)["block_number"] for entry_id, block in entries]
    return max([len(entries), *(value for value in explicit if value is not None)], default=0) + 1


def _find_entry(
    diary: str,
    *,
    entry_id: str | None = None,
    block_number: int | None = None,
) -> tuple[str, str, dict[str, Any]]:
    if (entry_id is None) == (block_number is None):
        raise ValueError("provide exactly one of entry_id or block_number")
    if entry_id is not None:
        entry_id = _clean(entry_id, "entry_id").strip()
        if not re.fullmatch(r"[0-9a-f]{32}", entry_id):
            raise ValueError("entry_id must be a 32-character lowercase hex id")
        block = _extract_entry(diary, entry_id)
        return entry_id, block, _entry_metadata(block, entry_id)
    if isinstance(block_number, bool) or not isinstance(block_number, int) or block_number < 1:
        raise ValueError("block_number must be a positive integer")
    matches = []
    for candidate_id, block in _iter_entries(diary):
        meta = _entry_metadata(block, candidate_id)
        if meta["block_number"] == block_number:
            matches.append((candidate_id, block, meta))
    if len(matches) != 1:
        raise ValueError(f"expected one block {block_number}, found {len(matches)}")
    return matches[0]


def _terminal_tag(prompt: str, explicit: str = "none") -> str:
    explicit = _clean(explicit, "tags").strip().lower()
    if explicit not in {"none", "fast", "nod"}:
        raise ValueError("tags must be one of: none, fast, nod")
    trailing = re.search(r"(?:\((?:nod|fast)\)\s*)+$", prompt, re.IGNORECASE)
    if not trailing:
        return explicit
    found = [value.lower() for value in re.findall(r"\((nod|fast)\)", trailing.group(0), re.I)]
    return "nod" if "nod" in found else "fast" if "fast" in found else explicit


def _parse_numbered(content: str) -> list[str]:
    if not content or content.lstrip().startswith("-"):
        return []
    result = []
    continuation_width = 0
    for line in content.split("\n"):
        match = re.match(r"^(\d+)\. (.*)$", line)
        if match:
            result.append(match.group(2))
            continuation_width = len(match.group(1)) + 2
        elif result:
            prefix = " " * continuation_width
            # Old blocks used unindented continuations. Preserve those too;
            # new blocks have exactly one added indentation level to remove.
            continuation = line[len(prefix):] if line.startswith(prefix) else line
            result[-1] += "\n" + continuation
    return result


def _fallback_section(block: str, heading: str, next_headings: Iterable[str]) -> str:
    start = block.find(heading)
    if start < 0:
        return ""
    start = block.find("\n", start)
    if start < 0:
        return ""
    candidates = [block.find(item, start + 1) for item in next_headings]
    candidates = [item for item in candidates if item >= 0]
    end = min(candidates) if candidates else len(block)
    return block[start:end].strip()


def _entry_content(block: str, entry_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    try:
        prompt = _extract_field(block, entry_id, "PROMPT", exact=meta["v2"])
    except ValueError:
        prompt = ""
    if meta["v2"]:
        plan = _parse_numbered(_extract_field(block, entry_id, "PLAN"))
        progress = _parse_numbered(_extract_field(block, entry_id, "PROGRESS"))
        journal = _extract_field(block, entry_id, "JOURNAL")
    else:
        plan = _parse_numbered(
            _fallback_section(block, "### 작업 방향·계획", ["### 진행 상황", "### 작업 결과"])
        )
        progress = _parse_numbered(
            _fallback_section(block, "### 진행 상황", ["### 작업 결과"])
        )
        try:
            result = _extract_field(block, entry_id, "RESULT")
        except ValueError:
            result = ""
        if result and result != "- 작업 중.":
            progress.append(result)
        try:
            journal = _extract_field(block, entry_id, "LESSONS")
        except ValueError:
            journal = ""
    return {"prompt": prompt, "plan": plan, "progress": progress, "journal": journal}


def _append_progress_to_block(
    block: str,
    entry_id: str,
    milestones: list[str],
    *,
    checkpoint: bool,
    stamp: datetime,
) -> str:
    meta = _entry_metadata(block, entry_id)
    if not meta["v2"]:
        raise ValueError("milestone append requires a v2 numbered diary block")
    existing = _parse_numbered(_extract_field(block, entry_id, "PROGRESS"))
    prefix = "체크포인트 — " if checkpoint else ""
    existing.extend(f"[{_display_time(stamp)}] {prefix}{item}" for item in milestones)
    return _replace_field(
        block,
        entry_id,
        "PROGRESS",
        _render_numbered(existing, empty="아직 기록된 주요 진행 상황 없음."),
    )


def _set_block_status(block: str, entry_id: str, status: str) -> str:
    status = _normalise_status(status)
    updated = _replace_metadata(block, entry_id, "status", status)
    if "STATUS" in _parse_entry_at(updated, 0)["fields"]:
        updated = _replace_field(updated, entry_id, "STATUS", status)
    return updated


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _active_summaries(diary: str, *, include_paused_details: bool = True) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry_id, block in _iter_entries(diary):
        meta = _entry_metadata(block, entry_id)
        if meta["status"] not in {STATUS_WORKING, STATUS_PAUSED}:
            continue
        if meta["status"] == STATUS_PAUSED and not include_paused_details:
            result.append(meta)
            continue
        content = _entry_content(block, entry_id, meta)
        result.append(
            {
                **meta,
                "prompt_sha256": _sha(content["prompt"].encode("utf-8")),
                "prompt_excerpt": _clip(content["prompt"], SUMMARY_PROMPT_CHARS),
                "plan": content["plan"],
                "recent_progress": content["progress"][-SUMMARY_PROGRESS_ITEMS:],
                "journal_excerpt": _clip(content["journal"], SUMMARY_JOURNAL_CHARS),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            item["block_number"] is not None,
            item["block_number"] or 0,
            item["received_at"],
        ),
        reverse=True,
    )


class ContinuityStore:
    def __init__(self, root: Path | str | None = None) -> None:
        # An explicit directory remains available for isolated tests/legacy import.
        # Production tools must use for_thread; never fall back to a shared diary.
        if root is None:
            raise ValueError("Explicit thread scope required; use ContinuityStore.for_thread")
        self.root = Path(root).expanduser()
        self.scope = None
        self.diary_path = self.root / "DIARY.md"
        self.protocol_path = self.root / "CONTINUITY_PROTOCOL.md"
        self.checkpoint_path = self.root / "checkpoints" / "ACTIVE_CHECKPOINT.md"

    @classmethod
    def for_thread(
        cls, thread_id: str, parent_thread_id: str | None = None,
        *, base_root: Path | None = None,
    ) -> "ContinuityStore":
        try:
            from .continuity_scope import resolve_scope
        except ImportError:
            from continuity_scope import resolve_scope
        scope = resolve_scope(thread_id, parent_thread_id, base_root=base_root)
        instance = cls(scope.root)
        instance.scope = scope
        instance.diary_path = scope.diary_path
        instance.protocol_path = scope.protocol_path
        instance.checkpoint_path = scope.checkpoint_path
        instance._scope_base = scope.base_root
        return instance

    @contextmanager
    def _transaction(self, *, write: bool = True):
        if self.scope is not None:
            try:
                from .continuity_scope import ensure_scope, resolve_scope
            except ImportError:
                from continuity_scope import ensure_scope, resolve_scope
            current = resolve_scope(
                self.scope.thread_id, self.scope.parent_thread_id,
                base_root=self._scope_base,
            )
            if current.root != self.root:
                raise ValueError("Diary scope moved; resolve its identity again")
        if not write:
            # Reads must not create an empty scope or even a lock file.  Once a
            # writer has initialized its lock, join it with a read-only shared
            # lock.  Before then, atomic replacement guarantees a whole-file
            # snapshot without a lock-file creation side effect.
            lock_path = self.root / ".continuity-journal.lock"
            if lock_path.exists():
                with lock_path.open("r", encoding="utf-8") as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
                    try:
                        yield
                    finally:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            else:
                yield
            return
        with _locked(self.root):
            if self.scope is not None and write:
                ensure_scope(self.scope)
            yield

    def register_scope(self) -> dict[str, Any]:
        if self.scope is None:
            raise ValueError("register_scope requires an explicit thread scope")
        with self._transaction():
            if not self.diary_path.exists():
                _atomic_write(self.diary_path, DIARY_HEADER)
        return self.scope.metadata()

    def _task_identity(self, value: str) -> str:
        if self.scope is None:
            return _singleline(value, "task_thread")
        if value not in {"current Codex task", self.scope.thread_id}:
            raise ValueError("task_thread must equal the scoped thread_id")
        return self.scope.thread_id

    def diary_start(
        self,
        *,
        prompt: str,
        title: str,
        plan: Iterable[str],
        task_thread: str = "current Codex task",
        received_at: str | None = None,
        tags: str = "none",
    ) -> dict[str, Any]:
        prompt = _exact_prompt(prompt)
        tags = _terminal_tag(prompt, tags)
        if tags == "nod":
            return {"skipped": True, "reason": "nod", "entry_id": None}
        if tags == "fast":
            return {
                "skipped": True,
                "reason": "fast-defers-diary-until-finish",
                "entry_id": None,
            }
        title = _singleline(title, "title")
        task_thread = self._task_identity(task_thread)
        plan_items = _list(plan, "plan", allow_empty=False)
        received = _parse_received(received_at)
        entry_id = uuid.uuid4().hex
        with self._transaction():
            diary = _read(self.diary_path)
            block_number = _next_block_number(diary)
            block = _render_v2_entry(
                entry_id=entry_id,
                block_number=block_number,
                title=title,
                status=STATUS_WORKING,
                received=received,
                task_thread=task_thread,
                tags=tags,
                prompt=prompt,
                plan=plan_items,
                progress=[],
                journal="- 작업 완료 후 기록.",
            )
            updated = _append_newest(diary, block)
            _atomic_write(self.diary_path, updated)
        return {
            "skipped": False,
            "entry_id": entry_id,
            "block_number": block_number,
            "status": STATUS_WORKING,
            "received_at": _iso_time(received),
            "prompt_sha256": _sha(prompt.encode("utf-8")),
            "diary_path": str(self.diary_path),
        }

    def diary_progress_append(
        self,
        *,
        milestones: Iterable[str],
        entry_id: str | None = None,
        block_number: int | None = None,
        checkpoint: bool = False,
    ) -> dict[str, Any]:
        values = _list(milestones, "milestones", allow_empty=False)
        stamp = _now()
        with self._transaction():
            diary = _read(self.diary_path)
            found_id, block, meta = _find_entry(
                diary, entry_id=entry_id, block_number=block_number
            )
            if meta["status"] != STATUS_WORKING:
                raise ValueError("progress can only be appended to a 작업 중 block")
            replacement = _append_progress_to_block(
                block, found_id, values, checkpoint=checkpoint, stamp=stamp
            )
            updated = _replace_entry(diary, found_id, replacement)
            _atomic_write(self.diary_path, updated)
        return {
            "entry_id": found_id,
            "block_number": meta["block_number"],
            "appended": len(values),
            "checkpoint": bool(checkpoint),
            "recorded_at": _iso_time(stamp),
        }

    def diary_correction(
        self,
        *,
        prompt: str,
        checkpoint_progress: Iterable[str],
        entry_id: str | None = None,
        block_number: int | None = None,
        revised_plan: Iterable[str] | None = None,
        tags: str = "none",
        posthoc: bool = False,
    ) -> dict[str, Any]:
        prompt = _exact_prompt(prompt)
        effective_tag = _terminal_tag(prompt, tags)
        if effective_tag == "nod":
            return {"skipped": True, "reason": "nod"}
        if not isinstance(posthoc, bool):
            raise ValueError("posthoc must be a boolean")
        if effective_tag == "fast" and not posthoc:
            return {"skipped": True, "reason": "fast-defers-diary-until-finish"}
        checkpoint_items = _list(
            checkpoint_progress, "checkpoint_progress", allow_empty=False
        )
        plan_items = (
            None
            if revised_plan is None
            else _list(revised_plan, "revised_plan", allow_empty=False)
        )
        stamp = _now()
        with self._transaction():
            diary = _read(self.diary_path)
            found_id, block, meta = _find_entry(
                diary, entry_id=entry_id, block_number=block_number
            )
            if meta["status"] != STATUS_WORKING or not meta["v2"]:
                raise ValueError("correction requires a v2 작업 중 block")
            replacement = _append_progress_to_block(
                block, found_id, checkpoint_items, checkpoint=True, stamp=stamp
            )
            original = _extract_field(replacement, found_id, "PROMPT", exact=True)
            corrected = f"{original}\n\n+ [{_display_time(stamp)}] 정정·추가 프롬프트\n{prompt}"
            replacement = _replace_field(
                replacement, found_id, "PROMPT", corrected, exact=True
            )
            replacement = _replace_metadata(
                replacement, found_id, "prompt_sha256",
                f"`{_sha(corrected.encode('utf-8'))}`",
            )
            if plan_items is not None:
                replacement = _replace_field(
                    replacement,
                    found_id,
                    "PLAN",
                    _render_numbered(plan_items, empty="계획 없음."),
                )
            updated = _replace_entry(diary, found_id, replacement)
            _atomic_write(self.diary_path, updated)
        return {
            "skipped": False,
            "entry_id": found_id,
            "block_number": meta["block_number"],
            "checkpoint_items": len(checkpoint_items),
            "plan_revised": plan_items is not None,
            "prompt_sha256": _sha(corrected.encode("utf-8")),
            "recorded_at": _iso_time(stamp),
        }

    def diary_set_status(
        self,
        *,
        status: str,
        entry_id: str | None = None,
        block_number: int | None = None,
        progress: Iterable[str] | None = None,
        journal: str | None = None,
    ) -> dict[str, Any]:
        status = _normalise_status(status)
        progress_items = _list(progress, "progress")
        journal_value = None if journal is None else _clean(journal, "journal", allow_empty=True)
        stamp = _now()
        with self._transaction():
            diary = _read(self.diary_path)
            found_id, block, meta = _find_entry(
                diary, entry_id=entry_id, block_number=block_number
            )
            replacement = block
            if progress_items:
                replacement = _append_progress_to_block(
                    replacement,
                    found_id,
                    progress_items,
                    checkpoint=status == STATUS_PAUSED,
                    stamp=stamp,
                )
            if journal_value is not None:
                field = "JOURNAL" if meta["v2"] else "LESSONS"
                if field in _parse_entry_at(replacement, 0)["fields"]:
                    replacement = _replace_field(
                        replacement, found_id, field, journal_value
                    )
            replacement = _set_block_status(replacement, found_id, status)
            updated = _replace_entry(diary, found_id, replacement)
            linked_sources = []
            if status == STATUS_COMPLETE:
                visited = {found_id}
                link_meta = meta
                while link_meta["resumes_entry_id"]:
                    source_id = link_meta["resumes_entry_id"]
                    if source_id in visited:
                        raise ValueError("cycle in diary resume ancestry")
                    visited.add(source_id)
                    _, source_block, source_meta = _find_entry(updated, entry_id=source_id)
                    if source_meta["block_number"] != link_meta["resumes_block_number"]:
                        raise ValueError("resume source block number does not match entry id")
                    if source_meta["status"] not in {STATUS_PAUSED, STATUS_COMPLETE}:
                        raise ValueError("resume source must remain paused or completed")
                    source_block = _set_block_status(source_block, source_id, STATUS_COMPLETE)
                    updated = _replace_entry(updated, source_id, source_block)
                    linked_sources.append(source_id)
                    link_meta = source_meta
            _atomic_write(self.diary_path, updated)
        return {
            "entry_id": found_id,
            "block_number": meta["block_number"],
            "status": status,
            "linked_source_completed": linked_sources[0] if linked_sources else None,
            "linked_sources_completed": linked_sources,
            "updated_at": _iso_time(stamp),
            "diary_sha256": _sha(updated.encode("utf-8")),
        }

    def diary_resume(
        self,
        *,
        resume_prompt: str,
        entry_id: str | None = None,
        block_number: int | None = None,
        revised_plan: Iterable[str] | None = None,
        task_thread: str = "current Codex task",
        received_at: str | None = None,
        posthoc: bool = False,
    ) -> dict[str, Any]:
        resume_prompt = _exact_prompt(resume_prompt, "resume_prompt")
        effective_tag = _terminal_tag(resume_prompt)
        if effective_tag == "nod":
            return {"skipped": True, "reason": "nod"}
        if not isinstance(posthoc, bool):
            raise ValueError("posthoc must be a boolean")
        if effective_tag == "fast" and not posthoc:
            return {"skipped": True, "reason": "fast-defers-diary-until-finish"}
        plan_items = (
            None
            if revised_plan is None
            else _list(revised_plan, "revised_plan", allow_empty=False)
        )
        received = _parse_received(received_at)
        new_entry_id = uuid.uuid4().hex
        with self._transaction():
            diary = _read(self.diary_path)
            source_id, source_block, source_meta = _find_entry(
                diary, entry_id=entry_id, block_number=block_number
            )
            if source_meta["status"] != STATUS_PAUSED:
                raise ValueError("only a 작업 보류 block can be resumed")
            if source_meta["block_number"] is None:
                raise ValueError("legacy unnumbered block must be migrated before resume")
            source = _entry_content(source_block, source_id, source_meta)
            new_number = _next_block_number(diary)
            combined_prompt = (
                f"{resume_prompt}\n\n+ {source_meta['block_number']}번 보류 블록의 기존 프롬프트\n"
                f"{source['prompt']}"
            )
            copied_progress = list(source["progress"])
            copied_progress.append(
                f"[{_display_time(received)}] {source_meta['block_number']}번 블록 작업을 재개했다."
            )
            block = _render_v2_entry(
                entry_id=new_entry_id,
                block_number=new_number,
                title=source_meta["title"],
                status=STATUS_WORKING,
                received=received,
                task_thread=self._task_identity(task_thread),
                tags="fast" if _terminal_tag(resume_prompt) == "fast" else "none",
                prompt=combined_prompt,
                plan=plan_items
                or source["plan"]
                or ["보류된 작업의 다음 단계부터 이어서 수행한다."],
                progress=copied_progress,
                journal=source["journal"],
                resumes_block_number=source_meta["block_number"],
                resumes_entry_id=source_id,
            )
            updated = _append_newest(diary, block)
            _atomic_write(self.diary_path, updated)
        return {
            "skipped": False,
            "entry_id": new_entry_id,
            "block_number": new_number,
            "status": STATUS_WORKING,
            "resumes_block_number": source_meta["block_number"],
            "resumes_entry_id": source_id,
            "received_at": _iso_time(received),
        }

    def diary_finish(
        self,
        *,
        entry_id: str,
        status: str,
        result: str = "",
        changes: str = "",
        verification: str = "",
        lessons: str = "",
        progress: Iterable[str] | None = None,
        journal: str | None = None,
    ) -> dict[str, Any]:
        """Finish v2 blocks while preserving the v1 call contract."""
        status_value = _normalise_status(status)
        if status_value == STATUS_WORKING:
            raise ValueError("diary_finish cannot leave a block 작업 중")
        with self._transaction():
            diary = _read(self.diary_path)
            block = _extract_entry(diary, entry_id)
            meta = _entry_metadata(block, entry_id)
        if meta["v2"]:
            progress_items = _list(progress, "progress")
            for label, value in (("결과", result), ("변경", changes), ("검증", verification)):
                if isinstance(value, str) and value.strip():
                    progress_items.append(f"{label}: {_clean(value, label).strip()}")
            return self.diary_set_status(
                entry_id=entry_id,
                status=status_value,
                progress=progress_items,
                journal=(journal if journal is not None else lessons) or None,
            )

        result = _clean(result or "- 상태만 갱신.", "result")
        changes = _clean(changes or "- 없음.", "changes")
        verification = _clean(verification or "- 없음.", "verification")
        lessons = _clean(lessons or "- 없음.", "lessons")
        with self._transaction():
            diary = _read(self.diary_path)
            block = _extract_entry(diary, entry_id)
            for field, value in (
                ("RESULT", result),
                ("CHANGES", changes),
                ("VERIFICATION", verification),
                ("LESSONS", lessons),
            ):
                if field in _parse_entry_at(block, 0)["fields"]:
                    block = _replace_field(block, entry_id, field, value)
            block = _set_block_status(block, entry_id, status_value)
            updated = _replace_entry(diary, entry_id, block)
            _atomic_write(self.diary_path, updated)
        return {
            "entry_id": entry_id,
            "status": status_value,
            "diary_sha256": _sha(updated.encode("utf-8")),
            "diary_path": str(self.diary_path),
        }

    def diary_record_fast(
        self,
        *,
        prompt: str,
        title: str,
        status: str,
        plan: Iterable[str] | None = None,
        progress: Iterable[str] | None = None,
        journal: str | None = None,
        result: str = "",
        changes: str = "",
        verification: str = "",
        lessons: str = "",
        task_thread: str = "current Codex task",
        received_at: str | None = None,
    ) -> dict[str, Any]:
        prompt = _exact_prompt(prompt)
        if _terminal_tag(prompt, "fast") == "nod":
            return {"skipped": True, "reason": "nod", "entry_id": None}
        status_value = _normalise_status(status)
        plan_items = _list(plan, "plan") or ["긴급 요청을 우선 처리하고 결과를 사후 기록한다."]
        progress_items = _list(progress, "progress")
        for label, value in (("결과", result), ("변경", changes), ("검증", verification)):
            if isinstance(value, str) and value.strip():
                progress_items.append(f"{label}: {_clean(value, label).strip()}")
        if not progress_items:
            progress_items = ["긴급 요청의 현재 진행 상황과 상태를 사후 기록했다."]
        title = _singleline(title, "title")
        task_thread = self._task_identity(task_thread)
        journal_value = _clean(
            (journal if journal is not None else lessons) or "- 별도 교훈 없음.", "journal",
        )
        received = _parse_received(received_at)
        entry_id = uuid.uuid4().hex
        with self._transaction():
            diary = _read(self.diary_path)
            block_number = _next_block_number(diary)
            block = _render_v2_entry(
                entry_id=entry_id,
                block_number=block_number,
                title=title,
                status=status_value,
                received=received,
                task_thread=task_thread,
                tags="fast",
                prompt=prompt,
                plan=plan_items,
                progress=progress_items,
                journal=journal_value,
            )
            updated = _append_newest(diary, block)
            _atomic_write(self.diary_path, updated)
        return {
            "entry_id": entry_id,
            "block_number": block_number,
            "status": status_value,
            "received_at": _iso_time(received),
            "diary_sha256": _sha(updated.encode("utf-8")),
            "diary_path": str(self.diary_path),
        }

    def diary_list_active(self) -> dict[str, Any]:
        with self._transaction(write=False):
            diary = _read(self.diary_path)
        entries = _active_summaries(diary)
        return {
            "diary_path": str(self.diary_path),
            "active_count": sum(item["status"] == STATUS_WORKING for item in entries),
            "paused_count": sum(item["status"] == STATUS_PAUSED for item in entries),
            "entries": entries,
        }

    def diary_read(
        self, *, entry_id: str | None = None, block_number: int | None = None,
    ) -> dict[str, Any]:
        """Read exactly one entry in this scope, with its full exact prompt.

        This is the explicit expansion operation for compact summaries.  Other
        entries are structurally/integrity checked, never expanded or returned.
        """
        with self._transaction(write=False):
            found_id, block, meta = _find_entry(
                _read(self.diary_path), entry_id=entry_id, block_number=block_number,
            )
            content = _entry_content(block, found_id, meta)
        return {"diary_path": str(self.diary_path), **meta, **content}

    # The legacy single-checkpoint API stays available for old tasks.  New tasks
    # use numbered diary blocks as the primary pause/resume state machine.
    def checkpoint_save(
        self,
        *,
        objective: str,
        last_prompt: str,
        completed: Iterable[str] | None = None,
        pending: Iterable[str] | None = None,
        blockers: Iterable[str] | None = None,
        important_paths: Iterable[str] | None = None,
        external_state: Iterable[str] | None = None,
        next_steps: Iterable[str] | None = None,
        do_not: Iterable[str] | None = None,
        status: str = "PAUSED",
    ) -> dict[str, Any]:
        status = _clean(status, "status").strip().upper()
        if status not in ALLOWED_CHECKPOINT_STATUSES:
            raise ValueError(f"status must be one of {sorted(ALLOWED_CHECKPOINT_STATUSES)}")
        sections = {
            "완료 상태": _list(completed, "completed"),
            "미완료 항목": _list(pending, "pending"),
            "현재 차단 요소": _list(blockers, "blockers"),
            "중요한 경로": _list(important_paths, "important_paths"),
            "외부 상태": _list(external_state, "external_state"),
            "다음 실행 순서": _list(next_steps, "next_steps"),
            "금지": _list(do_not, "do_not"),
        }
        lines = [
            "# ACTIVE CHECKPOINT",
            "",
            f"- updated_at: {_iso_time(_now())}",
            f"- status: {status}",
            "",
            "## 목표",
            "",
            _clean(objective, "objective").rstrip(),
            "",
            "## 마지막 관련 사용자 프롬프트 원문",
            "",
            _exact_prompt(last_prompt, "last_prompt"),
            "",
        ]
        for heading, values in sections.items():
            lines.extend([f"## {heading}", ""])
            lines.extend([f"{index}. {value}" for index, value in enumerate(values, 1)] or ["- 없음."])
            lines.append("")
        text = "\n".join(lines).rstrip() + "\n"
        with self._transaction():
            _atomic_write(self.checkpoint_path, text)
        return {
            "status": status,
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_sha256": _sha(text.encode("utf-8")),
            "bytes": len(text.encode("utf-8")),
        }

    def checkpoint_read(self) -> dict[str, Any]:
        with self._transaction(write=False):
            text = _read(self.checkpoint_path)
        return {
            "empty": not bool(text),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_sha256": _sha(text.encode("utf-8")),
            "bytes": len(text.encode("utf-8")),
            "content": text,
        }

    def checkpoint_clear(self, *, expected_sha256: str, objective_complete: bool) -> dict[str, Any]:
        if objective_complete is not True:
            raise ValueError("objective_complete must be true")
        expected_sha256 = _clean(expected_sha256, "expected_sha256").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("expected_sha256 must be a 64-character lowercase hex digest")
        with self._transaction():
            current = _read(self.checkpoint_path)
            current_sha = _sha(current.encode("utf-8"))
            if current_sha != expected_sha256:
                raise ValueError(
                    f"checkpoint SHA changed; expected {expected_sha256}, current {current_sha}"
                )
            _atomic_write(self.checkpoint_path, "")
        return {
            "cleared": True,
            "checkpoint_path": str(self.checkpoint_path),
            "previous_sha256": current_sha,
            "current_sha256": _sha(b""),
        }

    def continuity_resume(
        self, *, reason: str, next_action: str, entry_id: str | None = None
    ) -> dict[str, Any]:
        """Return detailed working summaries, never paused bodies or the full diary."""
        reason = _clean(reason, "reason")
        next_action = _clean(next_action, "next_action")
        if entry_id is not None:
            entry_id = _clean(entry_id, "entry_id").strip()
        now = _now()
        recovery_recorded = False
        recovery_target_entry_id: str | None = None
        with self._transaction():
            protocol = _read(self.protocol_path)
            diary = _read(self.diary_path)
            checkpoint = _read(self.checkpoint_path)
            if not protocol:
                raise ValueError(f"continuity protocol is missing: {self.protocol_path}")
            tracked = _active_summaries(diary, include_paused_details=False)
            working_v2 = [
                item for item in tracked if item["status"] == STATUS_WORKING and item["v2"]
            ]
            if entry_id is not None:
                matching = [item for item in working_v2 if item["entry_id"] == entry_id]
                if not matching:
                    raise ValueError("entry_id does not identify a working v2 diary block")
                target = matching[0]
            else:
                if len(working_v2) > 1:
                    raise ValueError("Multiple working blocks; pass an explicit entry_id")
                target = working_v2[0] if working_v2 else None
            if target is not None:
                block = _append_progress_to_block(
                    _extract_entry(diary, target["entry_id"]),
                    target["entry_id"],
                    [f"컨텍스트 복구: {reason.rstrip()} 다음 작업: {next_action.rstrip()}"],
                    checkpoint=True,
                    stamp=now,
                )
                diary = _replace_entry(diary, target["entry_id"], block)
                _atomic_write(self.diary_path, diary)
                recovery_recorded = True
                recovery_target_entry_id = target["entry_id"]
                tracked = _active_summaries(diary, include_paused_details=False)
            working = [item for item in tracked if item["status"] == STATUS_WORKING]
            paused_count = sum(item["status"] == STATUS_PAUSED for item in tracked)
        return {
            "recovered_at": _iso_time(now),
            "protocol_path": str(self.protocol_path),
            "protocol_sha256": _sha(protocol.encode("utf-8")),
            "diary_path": str(self.diary_path),
            "diary_sha256": _sha(diary.encode("utf-8")),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_sha256": _sha(checkpoint.encode("utf-8")),
            "checkpoint_empty": not bool(checkpoint),
            "checkpoint_bytes": len(checkpoint.encode("utf-8")),
            "checkpoint_requires_explicit_read": bool(checkpoint),
            "active_count": len(working),
            "paused_count": paused_count,
            "active_entries": working,
            "recovery_recorded_in_active_block": recovery_recorded,
            "recovery_target_entry_id": recovery_target_entry_id,
            "next_action": next_action,
        }

    def status(self) -> dict[str, Any]:
        with self._transaction(write=False):
            protocol = _read(self.protocol_path)
            diary = _read(self.diary_path)
            checkpoint = _read(self.checkpoint_path)
        active = _active_summaries(diary)
        return {
            "root": str(self.root),
            "protocol": {
                "exists": bool(protocol),
                "sha256": _sha(protocol.encode("utf-8")),
                "bytes": len(protocol.encode("utf-8")),
            },
            "diary": {
                "exists": bool(diary),
                "sha256": _sha(diary.encode("utf-8")),
                "bytes": len(diary.encode("utf-8")),
                "active_entry_ids": [
                    item["entry_id"] for item in active if item["status"] == STATUS_WORKING
                ],
                "paused_entry_ids": [
                    item["entry_id"] for item in active if item["status"] == STATUS_PAUSED
                ],
                "active_and_paused": active,
            },
            "checkpoint": {
                "empty": not bool(checkpoint),
                "sha256": _sha(checkpoint.encode("utf-8")),
                "bytes": len(checkpoint.encode("utf-8")),
            },
        }
