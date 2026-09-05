"""Stateless, explicitly thread-scoped MCP surface for the continuity journal."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

if __package__:
    from .continuity_dispatch import invoke
    from .continuity_runtime import SOURCE_ROOT
else:
    from continuity_dispatch import invoke
    from continuity_runtime import SOURCE_ROOT


mcp = FastMCP(
    "continuity-journal",
    instructions=(
        f"Diary operations MUST use this MCP. Canonical source: {SOURCE_ROOT}. "
        "The administrative CLI is not a workflow "
        "fallback. If this chat has stale MCP schemas, reconnect the MCP or use its actual MCP "
        "client bridge; do not bypass MCP with direct core calls or file writes. "
        "Every call requires the actual current thread_id and accepts its immediate parent_thread_id. "
        "There is no current-store global and inherited environment variables are not identity. "
        "Root diaries live at the configured diary_base/<thread_id>/Diary, normally "
        "~/Desktop/Diaries/<thread_id>/Diary. continuity_status reports effective runtime paths. "
        "A child lives under its parent's "
        "directory at Child/<thread_id>/Diary, recursively. Resolve scope before recovery; resolution "
        "is read-only. Registration explicitly initializes an empty scoped diary when needed. "
        "For normal prompts call diary_start with exact prompt and ordered plan before work. Record "
        "milestones with diary_progress_append. For corrections use diary_correction to checkpoint "
        "first, then append the exact '+' prompt. Use diary_set_status for 작업 완료/작업 취소/작업 보류. "
        "Resume paused work with diary_resume; completing its copy completes linked sources in this "
        "same diary. Terminal (fast) means urgent work first, then log when finished, interrupted or "
        "cancelled. For a new fast task use diary_record_fast; interrupted unfinished work remains "
        "작업 중 unless the user explicitly pauses it. For a fast correction or paused-task resume, "
        "reuse diary_correction or diary_resume with posthoc=true after urgent work; their default "
        "posthoc=false defers fast prompts without writing. Terminal (nod) means no diary calls and "
        "always wins over fast or posthoc. Never mark unfinished work complete. After compaction "
        "continuity_resume returns this scope's compact 작업 중 state. If a compact summary is "
        "insufficient, use diary_read with the specific entry_id or block_number to read only that "
        "block through MCP, never direct file access. "
        "Entry IDs, block numbers, locks and checkpoints never resolve across diary scopes."
    ),
    log_level="WARNING",
)

ThreadId = Annotated[str, Field(description="Required actual current thread/task ID, never a display title")]
ParentId = Annotated[str | None, Field(description="Actual immediate parent thread/task ID for child scope")]
EntryId = Annotated[str | None, Field(description="Entry ID within this thread's Diary")]
BlockNumber = Annotated[int | None, Field(description="Block number within this thread's Diary")]
Posthoc = Annotated[bool, Field(description="True only after urgent (fast) work has finished or been interrupted/cancelled; never overrides (nod)")]


def _annotations(*, read_only: bool = False, idempotent: bool = False, destructive: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=False,
    )


def _call(method: str, arguments: dict) -> dict:
    arguments = dict(arguments)
    return invoke(
        method,
        thread_id=arguments.pop("thread_id"),
        parent_thread_id=arguments.pop("parent_thread_id"),
        payload=arguments,
    )


@mcp.tool(title="Resolve thread diary scope", annotations=_annotations(read_only=True, idempotent=True))
def diary_resolve_scope(thread_id: ThreadId, parent_thread_id: ParentId = None) -> dict:
    """Read-only identity/path resolution; reject ambiguous parent matches without creating files."""
    return _call("diary_resolve_scope", locals())


@mcp.tool(title="Register thread diary scope", annotations=_annotations(idempotent=True))
def diary_register_scope(thread_id: ThreadId, parent_thread_id: ParentId = None) -> dict:
    """Explicitly initialize this thread's empty Diary and identity metadata; preserve existing data."""
    return _call("diary_register_scope", locals())


@mcp.tool(title="Start diary entry", annotations=_annotations())
def diary_start(
    thread_id: ThreadId,
    prompt: Annotated[str, Field(description="Exact user-authored prompt; omit ambient UI text")],
    title: str,
    plan: Annotated[list[str], Field(description="Non-empty ordered execution plan")],
    parent_thread_id: ParentId = None,
    task_thread: str | None = None,
    received_at: str | None = None,
    tags: str = "none",
) -> dict:
    """Before normal work, append exact prompt/plan/timestamp and 작업 중. (fast) defers writing; (nod) skips. task_thread must match thread_id."""
    return _call("diary_start", locals())


@mcp.tool(title="Append diary milestones", annotations=_annotations())
def diary_progress_append(
    thread_id: ThreadId,
    milestones: list[str],
    parent_thread_id: ParentId = None,
    entry_id: EntryId = None,
    block_number: BlockNumber = None,
    checkpoint: bool = False,
) -> dict:
    """Append timestamped major milestones to one 작업 중 block in this scope."""
    return _call("diary_progress_append", locals())


@mcp.tool(title="Record correction in active block", annotations=_annotations())
def diary_correction(
    thread_id: ThreadId,
    prompt: str,
    checkpoint_progress: list[str],
    parent_thread_id: ParentId = None,
    entry_id: EntryId = None,
    block_number: BlockNumber = None,
    revised_plan: list[str] | None = None,
    tags: str = "none",
    posthoc: Posthoc = False,
) -> dict:
    """Keep the active block: checkpoint first, exact '+' prompt, optional replacement plan. Fast prompts defer unless posthoc=true after urgent work; nod always skips."""
    return _call("diary_correction", locals())


@mcp.tool(title="Set diary block status", annotations=_annotations())
def diary_set_status(
    thread_id: ThreadId,
    status: Annotated[str, Field(description="작업 중, 작업 완료, 작업 취소, or 작업 보류")],
    parent_thread_id: ParentId = None,
    entry_id: EntryId = None,
    block_number: BlockNumber = None,
    progress: list[str] | None = None,
    journal: str | None = None,
) -> dict:
    """Set lifecycle status and optional timestamped progress/journal. Keep unfinished work 작업 중; pause only on explicit request. Completion propagates to resumed ancestors only inside this Diary. Repeated calls may append progress."""
    return _call("diary_set_status", locals())


@mcp.tool(title="Resume paused diary block", annotations=_annotations())
def diary_resume(
    thread_id: ThreadId,
    resume_prompt: str,
    parent_thread_id: ParentId = None,
    entry_id: EntryId = None,
    block_number: BlockNumber = None,
    revised_plan: list[str] | None = None,
    task_thread: str | None = None,
    received_at: str | None = None,
    posthoc: Posthoc = False,
) -> dict:
    """Copy a paused block to the bottom, retain provenance and put exact resume prompt above the original. Fast defers unless posthoc=true after urgent work; nod always skips. Finalize the new copy separately according to actual outcome."""
    return _call("diary_resume", locals())


@mcp.tool(title="List active and paused diary blocks", annotations=_annotations(read_only=True, idempotent=True))
def diary_list_active(thread_id: ThreadId, parent_thread_id: ParentId = None) -> dict:
    """Return compact 작업 중/작업 보류 summaries for this thread only, not the full diary."""
    return _call("diary_list_active", locals())


@mcp.tool(title="Read one selected diary block", annotations=_annotations(read_only=True, idempotent=True))
def diary_read(
    thread_id: ThreadId,
    parent_thread_id: ParentId = None,
    entry_id: EntryId = None,
    block_number: BlockNumber = None,
) -> dict:
    """Read only the explicitly selected block's metadata, exact prompt, plan, progress and journal. Supply entry_id or block_number within this scope; use when compact recovery is insufficient or a specific older block is referenced. Never defaults to a global/latest block or reads other Diaries."""
    return _call("diary_read", locals())


@mcp.tool(title="Finish diary entry", annotations=_annotations())
def diary_finish(
    thread_id: ThreadId,
    entry_id: str,
    status: str,
    parent_thread_id: ParentId = None,
    result: str = "",
    changes: str = "",
    verification: str = "",
    lessons: str = "",
    progress: list[str] | None = None,
    journal: str | None = None,
) -> dict:
    """Compatibility finalization of one entry without changing its exact prompt. Prefer diary_set_status; repeated calls may append timestamped progress."""
    return _call("diary_finish", locals())


@mcp.tool(title="Record fast task after urgent work", annotations=_annotations())
def diary_record_fast(
    thread_id: ThreadId,
    prompt: str,
    title: str,
    status: Annotated[str, Field(description="Actual outcome: 작업 중 for interrupted unfinished work; 작업 완료, 작업 취소, or explicit 작업 보류")],
    parent_thread_id: ParentId = None,
    plan: list[str] | None = None,
    progress: list[str] | None = None,
    journal: str | None = None,
    result: str = "",
    changes: str = "",
    verification: str = "",
    lessons: str = "",
    task_thread: str | None = None,
    received_at: str | None = None,
) -> dict:
    """After a new fast task finishes/is interrupted/is cancelled, record its exact prompt and actual state once. 작업 중 is valid when unfinished; nod always skips. Existing-block corrections/resumes use their own tool with posthoc=true instead."""
    return _call("diary_record_fast", locals())


@mcp.tool(title="Resume after compaction", annotations=_annotations())
def continuity_resume(
    thread_id: ThreadId,
    reason: str,
    next_action: str,
    parent_thread_id: ParentId = None,
    entry_id: EntryId = None,
) -> dict:
    """Recover working state only in this thread and record recovery in its selected active block."""
    return _call("continuity_resume", locals())


@mcp.tool(title="Get continuity status", annotations=_annotations(read_only=True, idempotent=True))
def continuity_status(thread_id: ThreadId, parent_thread_id: ParentId = None) -> dict:
    """Read-only scope paths/digests, compact active entries and checkpoint state, plus runtime/config/source provenance. Never return other scopes' diary bodies."""
    return _call("continuity_status", locals())


@mcp.tool(title="Save active checkpoint", annotations=_annotations())
def checkpoint_save(
    thread_id: ThreadId,
    objective: str,
    last_prompt: str,
    parent_thread_id: ParentId = None,
    completed: list[str] | None = None,
    pending: list[str] | None = None,
    blockers: list[str] | None = None,
    important_paths: list[str] | None = None,
    external_state: list[str] | None = None,
    next_steps: list[str] | None = None,
    do_not: list[str] | None = None,
    status: str = "PAUSED",
) -> dict:
    """Compatibility API: replace only this thread's active checkpoint with timestamped resume state. Prefer numbered diary blocks; repeated calls refresh the timestamp."""
    return _call("checkpoint_save", locals())


@mcp.tool(title="Read active checkpoint", annotations=_annotations(read_only=True, idempotent=True))
def checkpoint_read(thread_id: ThreadId, parent_thread_id: ParentId = None) -> dict:
    """Read this thread's active checkpoint and its SHA-256 guard."""
    return _call("checkpoint_read", locals())


@mcp.tool(title="Clear completed checkpoint", annotations=_annotations(destructive=True))
def checkpoint_clear(
    thread_id: ThreadId,
    expected_sha256: str,
    objective_complete: bool,
    parent_thread_id: ParentId = None,
) -> dict:
    """Clear this scope's checkpoint only with completion confirmation and its exact SHA-256 guard."""
    return _call("checkpoint_clear", locals())


if __name__ == "__main__":
    mcp.run()
