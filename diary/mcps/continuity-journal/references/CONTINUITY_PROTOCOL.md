# Diary Continuity Protocol v3 — Independent, Recursive Task Diaries

This is the installed operational contract for preserving exact user requests, objectives, completed and unfinished work, verification evidence, and lessons across context compaction, task switches, and resumptions. The setup procedure is documented in `diary/SETUP_PROMPT.md` in the source repository. Normal recovery returns compact working-state summaries rather than the entire conversation or journal.

## 1. Paths, Identity, and Scope

- Default base: the verified absolute expansion of `~/Desktop/Diaries` on this machine.
- Root Diary: `<DIARY_BASE>/<thread_id>/Diary`, with no extension.
- Child Diary: `<immediate-parent-Diary-directory>/Child/<thread_id>/Diary`.
- Repeat the same rule for grandchildren and later generations; do not impose an arbitrary application depth limit or flatten the tree.
- Each task's checkpoint is its own `checkpoints/ACTIVE_CHECKPOINT.md`.
- Each task's `scope.json` records its ID, immediate parent ID (`null` for roots), root and ancestor identities, paths, and schema version. It is identity metadata, not shared task content.
- Block numbering, states, locks, and checkpoints are independent in every Diary. No global mutable current-task or active-block pointer is permitted.
- Legacy shared or project Diaries are migration sources only, never fallback locations for new records or recovery.
- Prefer platform-provided unique thread/agent IDs. A display title, `/root`, working directory, or inherited parent `CODEX_THREAD_ID` is not a child's identity.
- If the platform does not expose a unique ID, assign one UUID continuity ID once and persist its mapping to the runtime task. A parent assigns its child's fallback ID. Retain that mapping through compaction and retries.
- Before delegation, the parent registers its own scope and supplies its ID as the child's immediate `parent_thread_id`. Passing every ancestor or the full parent path is unnecessary.
- Resolve a parent by directory name only in the registered Diary tree. Exactly one verified `Diary`/`scope.json` location must match. Zero or multiple matches fail; never choose an arbitrary result or a shared default.
- Reject duplicate identity placement, mismatched ancestry, cycles, traversal, unexpected file types, and symlink escapes. Revalidate scope immediately before writing. A registered child missing its parent argument must not become a new root.
- Supply `thread_id` on every MCP call and `parent_thread_id` for a child. Callers cannot select arbitrary Diary paths, and entry IDs/block numbers resolve only within the selected Diary.

## 2. Canonical Block Format

Append new numbered blocks to the physical bottom without reordering existing history. Allocate `max(block_number) + 1` within this Diary, starting at 1; never reuse numbers. Give every new block its own unique `entry_id`. Preserve valid legacy formats for reading and deliberately validate any migration needed before editing or resuming them.

The six sections, in order, are:

1. **Number and timestamp metadata:** entry ID, status, title, thread identity, exception tags, exact-prompt SHA-256, and source block number plus source entry ID for resumptions. The existing `prompt_received_at` field contains a verifiable supplied receipt time or otherwise the server's current time; it is not two independent receipt/write fields. Keep parent identity in `scope.json` and handoffs. Record a needed receipt-versus-write distinction in timestamped progress without changing serialization. Timestamp generation defaults to `Asia/Seoul` (KST, UTC+09:00); do not invent historical receipt times.
2. **Exact user-authored prompt:** preserve spelling, profanity, whitespace, and line endings. Do not summarize, trim, or normalize it. Exclude separately injected system/environment/UI/attachment text, while preserving quotations actually authored inside the user's message. Never invent unseen attachment contents.
3. **Ordered plan:** number actions from 1 in execution order. Replace with the latest priorities when corrected, but preserve the state and resumption point of unfinished prior work.
4. **Progress and checkpoints:** record meaningful milestones immediately, in chronological order. Include actual changes, artifact paths, reusable evidence, verification, remaining work, blockers, exact next steps, and actions to avoid. Distinguish verified facts from assumptions. Avoid trivial shell noise. Checkpoint the old state before changing direction.
5. **Journal and lessons:** record decisions, misunderstandings, mistakes, their causes, corrected preferences, and prevention rules useful to the next context window. Complete these notes on completion, cancellation, or pause.
6. **Exactly one canonical status:** use the compatibility tokens below, not translated synonyms.

| Stored/MCP value | Meaning |
| --- | --- |
| `작업 중` | Working / in progress |
| `작업 완료` | Completed |
| `작업 취소` | Canceled |
| `작업 보류` | Paused |

Set working status at the start. Mark completed only after all applicable work is done; canceled or paused only when the user instructs that transition. A blocked task or an ended response is not automatically paused or completed. Status fields are recovery indexes and must remain synchronized atomically.

Use the supplied length-framed entry/field format. Literal headings, numbers, or marker-like text inside a prompt must not be interpreted as record structure. Preserve multiline content and exact-prompt hashes through edits and copies.

## 3. Ordinary Requests and Corrections

For a new ordinary request, use one `diary_start` before work to create the number, server timestamp, exact prompt/hash, ordered plan, and working status. Retain the returned `entry_id` and block number.

Append significant milestones with `diary_progress_append`. On an actual terminal transition, use `diary_set_status` to record final progress, journal notes, and true status in one call. `diary_finish` remains a compatibility operation. Do not add a separate time-query tool or Bash timestamp-transfer workflow; recording tools timestamp their own operations.

For a mid-task correction, added request, changed priority, or request to do something else first without an explicit pause:

1. Use `diary_correction` on the current working block, not `diary_start`.
2. Atomically checkpoint the old state in section 4, append `+` and the exact correction beneath the old prompt, and replace the ordered plan when necessary.
3. Keep the block working. Completing only the intervening request does not complete earlier unfinished work.

## 4. Ambiguity and Prior-Work Status Questions

Before answering questions about earlier completion, progress, remaining work, last state, or artifact locations, consult relevant records in this task's Diary. Do this before investigating files, starting servers, or checking external state, not after answering from memory.

Locate candidate blocks by the task name, target, domain, artifact path, or relevant phrase. Inspect newest matches first, then follow earlier correction/resumption links only when needed. Stop when the latest record supplies sufficient state and evidence. If no candidate can be established, narrow to recent records and expand backward as necessary; do not start by reading the full file from its beginning.

Use scoped MCP reads. The current tool surface has no general history-search endpoint: do not invent one or use that gap to justify normal manual whole-file access. Repair the MCP lookup capability when authorized, or request a precise block reference if the needed record cannot otherwise be selected.

For ambiguous expressions, check relevant original prompts, corrections, lessons, prior definitions, and linked checkpoints. If uncertainty remains, especially when interpretations would change an external action, stop the affected action and ask about the exact unclear phrase. Do not fabricate an interpretation; do not ask unnecessary questions for clear instructions.

When present-state verification is needed, start from recorded authoritative paths and verification methods. Prefer verified reality over a stale entry, and record the discrepancy. If records are absent or conflicting, do not infer completion. These rules apply regardless of whether compaction occurred.

## 5. Pause, Resume, and Cancel

**Pause:** use one `diary_set_status` to record progress, remaining work, and the precise restart point, and set `작업 보류`. Leave the source in place.

**Resume:** use `diary_resume` with the explicitly selected paused source. It copies that block to the physical bottom, allocates a new number/entry ID/time, identifies the source in metadata, and places the exact resumption prompt above the copied original prompt. Preserve the old plan, progress, and journal; update the plan if requested. Only the copy becomes `작업 중`; the source remains paused. Do not create a separate ordinary block for the resumption prompt.

Completing a resumed copy atomically completes all linked paused sources within the same Diary. Verify source numbers and entry IDs agree and reject cyclic or cross-Diary links before changing anything. Repeated pause/resume chains retain every source relationship.

**Cancel:** set the user-selected working or paused task to `작업 취소`. Canceling a resumed copy does not complete its sources or cancel them without an instruction to do so.

These links are between blocks in one Diary, not parent/child task relationships.

## 6. Terminal `(nod)` and `(fast)` Tags

Only the actual user's final non-whitespace tag area activates these exceptions. Mentions inside quotations, explanatory text, examples, attachments, system text, or ambient UI do not.

- `(nod)`: perform the task with no Diary operations for that prompt, including no recovery record or status change. Never backfill its prompt or result later.
- `(fast)`: perform urgent work before logging, reducing unnecessary or excessive process and extra verification. Essential authorization and data protection still apply. After completion/interruption/pause/cancellation and before ending the response, record the exact request, actual plan/progress/lessons, and true status.
- If both appear in the terminal tag area, `(nod)` wins.

Fast recording preserves the request's relationship to existing work:

- New task: `diary_record_fast`, including honest `작업 중` if unfinished.
- Existing working-block correction: `diary_correction(posthoc=true)` after work.
- Explicit paused-task resumption: `diary_resume(posthoc=true)` after work, retaining source links.
- Follow with `diary_set_status` when needed for the true outcome. Fast correction/resumption without `posthoc=true` defers writing. `(nod)` always overrides post-hoc writes.

Do not create duplicate blocks, invent completion, or silently lose an interrupted task's restart state because a request was urgent.

## 7. Child Assignments and Compaction Recovery

Children record labeled **Parent assignment** text in their own Diaries, not as directly user-authored prompts. Do not copy the user's complete prompt into every child. Parents record only child IDs, purpose, Diary locations, key results, and blockers in their own blocks; detailed child logs remain with the child. Neither side overwrites the other's history. Child completion does not complete a parent block. Preserve inherited no-diary and urgency constraints through delegation.

After compaction, summary handoff, resumption, or missing context, and before substantive work:

1. Consult the persistent protocol and resolve this thread's own scope from explicit current and parent IDs.
2. Select the retained `entry_id`. If lost, use read-only scoped working summaries and match the objective. Multiple working candidates require explicit selection; never use a globally newest task or a reusable agent name.
3. Call `continuity_resume` with the explicit identity and selected entry. It returns compact working-state summaries, a paused-block count, and existence/size/hash metadata for the separate checkpoint, rather than paused/checkpoint bodies or the whole Diary.
4. Recover the plan, recent progress/checkpoints, lessons, and next action. Read paused/finished records or the separate checkpoint only for an explicit reference, resumption, or status question.
5. If the summary is insufficient, use `diary_read` for that selected entry or block. Do not recursively read other task bodies or habitually reread all history.
6. Record the recovery reason and next action in the selected working block's section 4. Do not write into another chat when no matching target exists.
7. Continue from evidence: do not repeat completed work or assume unverified work is done.

Handoffs retain thread ID, parent ID, absolute Diary path, entry ID, block number, objective, latest checkpoint, and exact next action. Status scans use actual metadata fields, not status words appearing inside prompts. Whole-file forensic access, migration, and installation repair are separate explicit maintenance operations with backups and validation, not normal recovery alternatives.

## 8. Mandatory MCP Runtime and Tool Surface

Authoritative installed code: `<DIARY_BASE>/mcps/continuity-journal`. The server is `scripts/continuity_mcp.py`. `runtime.json` sets the actual absolute Diary base and authoritative protocol path; `.mcp.json` describes the host connection. For a fresh public installation, the protocol is this package's installed `references/CONTINUITY_PROTOCOL.md`. Do not copy machine-specific generated settings from another computer.

All normal Diary reading, writing, and recovery must use this MCP. Verify `continuity_status.runtime`: source root, server/config paths, Diary base, protocol path, version, and Python executable must match the intended deployment. A familiar tool name alone does not prove a current server.

The 16 tools are:

| Tool | Purpose |
| --- | --- |
| `diary_resolve_scope` | Read-only identity and parent-path resolution. |
| `diary_register_scope` | Explicit first registration, preserving existing content. |
| `diary_start` | Create number, time, exact prompt, plan, and working status together. |
| `diary_progress_append` | Append a meaningful milestone or checkpoint to section 4. |
| `diary_correction` | Atomic checkpoint → exact `+` prompt → optional plan replacement. |
| `diary_set_status` | Atomic true status, final progress, journal, and linked completion. |
| `diary_list_active` | Scoped working/paused summaries. |
| `diary_read` | Full read-only contents of one explicitly selected entry or block. |
| `diary_resume` | Linked paused-block copy at the Diary's bottom. |
| `diary_record_fast` | One post-hoc record for a new urgent task. |
| `continuity_resume` | Compact working recovery and selected-entry recovery checkpoint. |
| `continuity_status` | Scoped integrity/status metadata and runtime provenance. |
| `diary_finish` | Legacy-compatible finish operation. |
| `checkpoint_save` | Save this scope's separate active checkpoint. |
| `checkpoint_read` | Read that checkpoint and its clearing-guard hash. |
| `checkpoint_clear` | Clear only after actual completion and a matching fresh hash. |

Every call requires explicit task identity. Use the numbered-block APIs as the normal authority; compatibility tools do not replace them.

An already-open chat may retain an old server/schema after installation updates. Reconnect before writing, particularly when records use newer length framing. If the host cannot reconnect immediately, invoke the same installed Python environment's `scripts/continuity_mcp_client.py` with:

```text
<method> --thread-id <id> [--parent-thread-id <id>] --json '<payload>'
```

This bridge uses real MCP `initialize` → `tools/list` → `tools/call`, with bounded waits and no automatic write retries. It is not direct-core CLI access. `continuity_cli.py` is administrative/test compatibility only, not a normal workflow option.

After an uncertain write timeout/disconnection, inspect the scoped result before retrying to prevent duplicates. If no MCP connection can be established, report or repair the limitation; do not silently skip recording, switch to manual edits, or declare setup successful. Preserve `(nod)`/`(fast)` exceptions.

Install the supplied requirements in a suitable Python environment. Preview this machine's actual absolute paths with `scripts/configure_runtime.py`, then use `--write --configure-plugin` on the installed package to generate `runtime.json` and `.mcp.json` and attach the generated configuration to the plugin manifest. The pristine public manifest intentionally omits `mcpServers`; `.mcp.example.json` is illustrative only. Register the configured installed server using the host's supported mechanism, and exclude generated machine-specific settings from the source repository. The current POSIX locking implementation runs on macOS/Linux; use a verified compatible environment such as WSL on Windows, or validate a proper native port. Source, installed tools, skill, and persistent instructions must stay consistent; changing a cache alone is insufficient.

## 9. Integrity, Concurrency, and Privacy

- Each Diary read-modify-write uses its own directory lock. Allocate block numbers and append under the same lock.
- Use a flushed/fsynced temporary file followed by atomic replacement. Never leave an authoritative file temporarily deleted between separate delete/add operations.
- A short `.scope-registry.lock` prevents duplicate-ID creation races during initial registration. Ordinary Diary updates remain independently locked per task.
- Verify exact-prompt SHA-256; corrections update the combined digest. Preserve whitespace, CRLF, and multiline fields.
- Validate resume sources by both block number and entry ID; reject cycles, mismatches, and cross-Diary links before partial changes.
- Recheck scope and file types immediately before writes. Callers do not supply arbitrary storage paths.
- Restrict private records to the local user where supported. Do not publish or transmit Diaries, scope metadata, checkpoints, or generated machine-specific configuration without the user's request. Public source and generic documentation are distinct from private runtime state.
- Keep one separate active checkpoint per scope only when needed; section 4 remains authoritative. Clearing requires a fresh read, matching current hash, and genuine completion.

## 10. Migration, Verification, and Reporting

Migrate only records demonstrably owned by the current task using real identity and prompt/conversation evidence, not a `/root` label. Do not classify/delete other tasks' history on their behalf. Preserve IDs, original numbers, timestamps, exact prompts/hashes, status, progress, lessons, and resume links.

Check destination duplicates and conflicts; never overwrite newer records with older copies. Verify the copy and retain a recoverable source backup before any removal. For an authorized move, recheck the selected source block's current hash under its lock and remove only that block. Keep legacy shared history until every task's migration is verified; do not arbitrarily complete/cancel its remaining work. Deliberately reconcile `Diary.md` to extensionless `Diary`; never maintain both as competing authorities.

Across machines, verify and update absolute paths while retaining task identity and record semantics. Do not merge uncertain old/new platform identity mappings. Persist the protocol reference in an instruction mechanism the host actually auto-loads; do not claim automatic continuity where the host lacks it. Preserve unrelated instructions.

Test in isolated temporary directories, never by damaging production records. Cover root/sibling/child/grandchild isolation; parent-only discovery; absent/duplicate/invalid identity rejection; registration races; foreign-entry rejection; exact text and marker preservation; atomic corrections; repeated pause/resume links; cancellation and child-completion boundaries; compact recovery; terminal tag timing and precedence; concurrent numbering; runtime/path validation; and stale-tool handling.

Report only the configuration actually applied, authoritative paths, current task scope, working MCP/bridge connection, test results, and any reconnect steps needed for existing chats. A promise to remember is not a substitute for persistent rules, exact records, explicit scope, and verified recovery.
