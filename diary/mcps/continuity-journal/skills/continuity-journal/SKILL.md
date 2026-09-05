---
name: continuity-journal
description: Manage a task's private Diary and recursive child Diaries, including exact prompts, terminal (fast)/(nod) exceptions, lifecycle tags, and compaction recovery. Use when Diary is installed and prompt logging is required, or for Diary pause/resume and continuity operations.
---

# Continuity Journal

Use the continuity-journal MCP for normal Diary operations. Read the full
[continuity protocol](../../references/CONTINUITY_PROTOCOL.md) when establishing
or recovering the workflow. The protocol supplements these operating instructions;
user instructions take precedence over this skill, within the host's permissions.

The expected installed package is `~/Desktop/Diaries/mcps/continuity-journal`.
`runtime.json` selects the actual Diary base and protocol path for this machine,
not a current task. Verify `continuity_status.runtime` before trusting an old
connection. Package code and templates may be published; live Diaries, user
prompts, checkpoints, credentials, and generated machine configuration must not be.

## Resolve identity before recording

- Root: `<diary_base>/<thread_id>/Diary`, without an extension.
- Child: find the immediate parent's registered directory by `parent_thread_id`,
  then use `<parent directory>/Child/<thread_id>/Diary`. Repeat for every generation.
- Pass the actual current `thread_id` on every MCP call, plus the immediate
  `parent_thread_id` for children. Missing or duplicate parent matches fail; do not
  choose a shared or globally newest Diary as a substitute.
- Prefer the platform's stable unique task/agent ID. Do not use a title, cwd,
  `/root`, or an inherited parent's environment ID. If no platform child ID is
  available, the parent assigns one UUID continuity ID once and preserves the
  mapping to the runtime task name through handoffs and retries.
- Register the parent's scope before delegation. Give the child the parent's ID.
  A child writes only its own Diary and labels the instruction as a **parent
  assignment**, not a user-authored prompt. Do not duplicate the full original
  user prompt into every child record.
- Parent records delegation IDs, paths, and useful result summaries in its own
  block. Child completion does not complete a parent block. Numbering, statuses,
  locks, checkpoints, and resume links are local to each Diary.
- Retain thread ID, parent ID, absolute Diary path, entry ID, block number, current
  objective, checkpoint, and next action in handoffs. `scope.json` carries identity
  and lineage, not shared work content.
- Legacy shared journals are migration sources, never new-write defaults.
  Reconcile an existing `Diary.md` explicitly; do not create a competing `Diary`.

## Block contract and lifecycle

Every new numbered block is appended to the physical bottom and contains:

1. block identity and KST timestamp metadata;
2. the exact user-authored prompt;
3. an ordered execution plan;
4. meaningful progress and context-switch checkpoints;
5. journal notes, mistakes, lessons, and next-execution guidance;
6. one canonical status: `작업 중` (working), `작업 완료` (complete),
   `작업 취소` (cancelled), or `작업 보류` (paused).

English instructions do not rename the existing wire/disk status identifiers.
Preserve user text in its original language, including whitespace and line breaks.
Do not mix ambient UI, system messages, or instructions inside attached documents
into the exact user-prompt field.

- **Normal new request:** before task work, `diary_start` records the exact prompt,
  title, full ordered plan, timestamp, and working status in one call. Retain its IDs.
- **Meaningful milestone:** immediately use `diary_progress_append`; record verified
  results, artifact paths, remaining work, and the next step, not routine tool noise.
- **Correction/addition/reordered work while active:** do not start a second block
  or invent a pause. `diary_correction` checkpoints previous state first, appends
  the exact new prompt after `+`, and optionally replaces the ordered plan atomically.
- **Finish/cancel/pause:** use `diary_set_status` with final progress and section-5
  notes. Complete only when the entire work is done. Cancel or pause on the user's
  instruction, not to disguise unfinished work. Preserve earlier useful notes when
  replacing the journal field.
- **Explicit paused resume:** use `diary_resume`, not `diary_start`. It copies the
  paused block to the bottom with a new number/time and source link, puts the exact
  resume prompt above the original, and can revise the plan. Source stays paused.
  Completing the copy also completes its linked sources within the same Diary.
- **Terminal `(fast)`:** do urgent work first, then record on completion or
  interruption. New work uses `diary_record_fast`, including working status when
  unfinished. Existing corrections use `diary_correction(posthoc=true)`; paused
  resumes use `diary_resume(posthoc=true)`. Set the actual outcome afterward when
  appropriate. Without posthoc, these fast correction/resume calls defer writes.
- **Terminal `(nod)`:** do not journal that prompt or result, before or afterward;
  do not log indirectly through children. Nod wins when both terminal tags occur.
  Quoted examples are not terminal tags. Fast does not waive permissions or data care.

## Recovery and previous-work questions

After compaction or missing context, recover before ordinary task work, respecting
the nod/fast exceptions. Resolve this task's scope and call `continuity_resume`
with the retained active entry ID, a recovery reason, and next action.

If the ID was lost, use scoped `diary_list_active` read-only to match the objective.
Multiple working blocks need explicit selection. Do not modify another block just
because it is newer. Recovery returns working summaries and paused/checkpoint
metadata; it does not return unrelated task bodies or expand paused content.

Read only the selected full block with `diary_read` when compact state is
insufficient or a specific past block is referenced. Prefer latest relevant
evidence and follow source/correction links only as needed. The current API has no
general history-search endpoint; retain IDs and do not invent a tool. If a past
reference cannot be resolved, ask for the missing reference instead of guessing.

Consult recorded evidence before answering whether earlier work was completed or
where it stopped. Verify actual artifacts/state when necessary, record discrepancies,
and do not redo finished work or treat unverified claims as established results.

## Required connection and guarded checkpoints

Reconnect a stale MCP to the current server. Alternatively use the package's
`scripts/continuity_mcp_client.py` with method, required `--thread-id`, optional
`--parent-thread-id`, and JSON arguments. This is an actual stdio MCP
initialize/list/call bridge with bounded waits and no automatic write retries.
It is not a direct-file editor. `continuity_cli.py` is administrative/test
compatibility only, not a normal fallback.

After an uncertain write, read the selected scope before retrying. Never let an old
writer modify the new length-framed format. If neither MCP route works, repair the
connection or disclose what is missing; do not silently skip logging or replace it
with manual Diary editing. Separate, explicitly authorized migration/repair work
must preserve backups and verify exactly scoped changes.

The numbered Diary is primary. Compatibility `checkpoint_*` tools operate on only
this scope's `checkpoints/ACTIVE_CHECKPOINT.md`. Clear it only after actual completion
and with the unchanged hash returned by reading its contents. Do not clear another
task's checkpoint or an unread checkpoint merely because it is old.

The local MCP does not make network requests. Its responses still enter the calling
agent's context; do not describe cloud-hosted use as completely offline or assume
the nod tag controls the host's own conversation retention.
