# Diary Setup Prompt — Independent, Recursive Task Diaries

Set up the Diary system in this environment according to the rules below, and continue applying them to subsequent work. Do not stop at an explanation: implement and verify the configuration using the persistent files, global instructions, and tools actually available. Do not assume capabilities or permissions that do not exist. Clearly report anything you cannot configure.

You are, by the unavoidable design of this system, a patient with short-term amnesia. Therefore, you must keep a diary for your future self, so that forgetting does not lead you to foolishly repeat work you have already done.

The point of this metaphor is to restore the exact working state from persistent records after context compaction or session resumption, instead of filling memory gaps with guesses. Preserve the user's original words, objectives, completed work, remaining work, verification evidence, mistakes, and lessons. Prevent duplicate effort and drift from the task. A promise to “remember” is not a completed installation.

## 1. Initial Setup and Persistent Enforcement

1. Check only what is necessary: the operating system, filesystem permissions, how this environment exposes actual task/thread identity, the supported global instruction mechanism, and available tools. If a Diary system already exists, inspect its relevant rules and configuration first. Do not create a competing installation.
2. The default storage base, `DIARY_BASE`, is the current user's `~/Desktop/Diaries`. Here, `~` means the home directory on this machine. If the operating system uses a different Desktop location, resolve its actual location. If no usable Desktop exists, agree on an alternative persistent location with the user. Never copy another machine's username or absolute home path.
3. Maintain one authoritative `CONTINUITY_PROTOCOL.md` that all relevant tasks can locate. For a fresh installation of this distribution, use `<DIARY_BASE>/mcps/continuity-journal/references/CONTINUITY_PROTOCOL.md`, bundled with the package. If a valid authoritative protocol already exists, reuse its location and reconcile the rules instead of creating competing authorities. Configure `--protocol-path` with that document's actual installed absolute path.
4. Add the following to the global instruction file that this environment actually loads automatically—for example, a supported `AGENTS.md`—or an equivalent persistent configuration:
   - A requirement to apply this Diary routine to all applicable tasks and threads.
   - How to locate the authoritative protocol and the task's own Diary.
   - The recording routine for an ordinary incoming prompt.
   - The recovery routine that runs before work after compaction or resumption.
   - How to provide the current task ID and its immediate parent's ID explicitly.
   - How to reject stale tools and establish a supported MCP connection.
5. Use a filename the product actually supports. Do not invent a file such as `AGENT.md` and claim it is automatically loaded. Preserve unrelated global and project instructions; reconcile only Diary-related conflicts.
6. Only the rules and tool code are shared globally. Tasks must not share a working Diary, active-block pointer, task state, or checkpoint.
7. If the environment has no persistent files or automatically loaded global instructions, explain that limitation and provide reusable rules and records the user can supply again. Do not guarantee automatic enforcement in new conversations or after compaction when it cannot be established.
8. Record this installation request too, unless a terminal exception tag applies. Once the minimum bootstrap work has made the paths and MCP usable, immediately record the exact prompt and plan. Do not fabricate an unverified receipt timestamp or earlier work history.

## 2. Task Paths and the Recursive Child Tree

A task/thread is a uniquely identified conversation or execution unit. Do not create a new directory for every ordinary prompt in the same thread. Append or update blocks in that thread's existing Diary.

Root task:

```text
DIARY_BASE/<current-task-or-thread-id>/Diary
```

Child task/thread:

```text
<immediate-parent-Diary-directory>/Child/<current-child-id>/Diary
```

Repeat the same rule for every subsequent generation:

```text
~/Desktop/Diaries/
├── A/
│   ├── Diary
│   └── Child/
│       └── B/
│           ├── Diary
│           └── Child/
│               └── C/
│                   ├── Diary
│                   └── Child/...
└── another-root/
    └── Diary
```

- The authoritative filename is `Diary`, with no extension.
- Use the spelling and capitalization of `Child` consistently.
- Do not impose an arbitrary application-level depth limit. If an actual filesystem limit is reached, report it; do not silently flatten the tree or switch to a shared file.
- Each Diary has independent block numbering, task states, file locking, and checkpoints. Block 1 in a parent and block 1 in a child are different records.
- Children do not jointly edit their parent's Diary. Siblings do not write into one another's Diary.

## 3. Resolve a Path from the Current ID and Immediate Parent ID

1. Prefer the actual unique task, thread, or agent ID supplied by the platform. Do not use a display name, chat title, working directory, or reusable name such as `/root` as a unique identity.
2. If the platform exposes no suitable unique ID, assign one UUID-based continuity ID to that execution unit and persist its mapping to the actual runtime unit. For a child, the parent assigns and passes this ID. Reuse it through compaction, resumption, and retries; do not allocate a new one each time.
3. Before delegation, the parent registers its own Diary directory and passes its ID as the child's immediate `parent_thread_id`.
4. A child needs only its own `thread_id` and immediate `parent_thread_id`. Do not require every ancestor ID or the full parent path.
5. Resolve the parent by its directory name in the recognized Diary tree under `DIARY_BASE`. The MCP's directory lookup performs this name-only search; locating a parent does not require reading or searching other Diary bodies.
6. Confirm that the matching directory is a registered location using its `Diary`, `scope.json`, or corresponding validated registration metadata.
   - Exactly one parent match: use its `Child/<current-id>/Diary`.
   - No match: check the parent's registration and ID.
   - Multiple matches: do not write until the duplicate or conflict is resolved.
   - Never select the first arbitrary result, the globally newest task, or a shared Diary as a substitute.
7. Do not register the same ID beneath another parent or at another root location. A missing parent argument for an existing child is not permission to create a new root Diary.
8. Store the current ID, immediate parent ID (`null` for a root), root ID, ancestry derived from the path, authoritative Diary and checkpoint paths, and schema version in that Diary directory's `scope.json`. This is identity metadata, not a shared work journal.
9. Validate identity/path consistency, repeated or cyclic ancestor IDs, traversal such as `../`, unexpected file types, and symlink-based escapes. Resolve legitimate system path aliases when establishing the base; do not permit bypasses beneath the Diary tree.
10. Include the current ID, parent ID, and resolved Diary path in handoffs. An inherited environment variable may contain the parent's ID; never automatically treat it as the child's identity.

## 4. The Authoritative Diary Block Format

Append each new ordinary block to the physical bottom of the current task's Diary. Do not move earlier blocks upward or reorder history.

Preserve existing legacy blocks, including unnumbered blocks or older English status values, with read compatibility. New blocks use the format below. Any conversion needed to number or resume legacy content must be explicitly validated. Preserve compatibility with valid existing entry and field markers.

Each block has six sections, in this order:

1. **Block number, date, and time metadata**
   - Allocate one more than the largest existing block number in this Diary. Start at 1 when empty; never reuse a number.
   - Assign a separate unique `entry_id`.
   - Preserve the supplied runtime's metadata schema: its `prompt_received_at` is a verifiable original receipt time when explicitly supplied, otherwise the server's current timestamp. Record the timezone, title, current task/thread identity, and exception tags. The immediate parent ID is stored in this Diary's `scope.json` and retained in handoffs; do not invent a second per-block parent field.
   - If original receipt time must be distinguished from a later recording time, retain that distinction in timestamped progress rather than pretending the block has two independently stored timestamp fields. Never present an unverified receipt time as fact.
   - The default timezone remains `Asia/Seoul` (KST, UTC+09:00). An explicitly requested timezone change must be consistently implemented and labeled; do not merely relabel timestamps the runtime still generates in KST.
   - Record the SHA-256 of the exact prompt.
   - For a resumed copy, record both the source block number and source `entry_id`.

2. **The user's exact prompt**
   - Preserve the complete user-authored prompt exactly as received.
   - Do not transform, shorten, summarize, correct spelling, soften profanity, strip surrounding whitespace, or normalize line endings.
   - Preserve correction, addition, and resumption prompts just as exactly.
   - Do not mix separately injected system messages, browser state, environment metadata, or automatically extracted attachment contents into the user-authored prompt.
   - Text the user directly quotes inside their own message is part of that message; retain it.
   - Distinguish actual attachment representations from references to attachments. Never invent unseen attachment contents as original prompt text.

3. **Direction and ordered plan**
   - Number actions from 1 in intended execution order.
   - Update the plan when a correction changes priorities.
   - Do not silently erase unfinished earlier work from the plan. Preserve its state and exact resumption point.

4. **Progress and checkpoints**
   - Record meaningful milestones immediately when they occur.
   - Preserve completed work, actual changes, artifact paths, reusable evidence, verification results, current state, remaining work, blockers, the exact next action, and actions to avoid.
   - Distinguish observed or verified facts from assumptions and unverified claims.
   - Record enough that reading this section after an interruption allows precise continuation without repeating finished work.
   - Omit trivial shell output, meaningless tool-call noise, and repetitive narration.

5. **Journal and lessons**
   - On completion, pause, or cancellation, record decisions, mistakes, and lessons your future self should remember.
   - For example: which instruction you misunderstood, how that interpretation led to the wrong action, and the rule that should prevent recurrence.
   - Include corrected user preferences, unverified assumptions, and prohibited repeat actions when relevant.

6. **Task status**
   - Maintain exactly one of the four canonical values below. These Korean strings are stable on-disk and MCP compatibility tokens, not prose to translate:

     | Canonical value | English meaning |
     | --- | --- |
     | `작업 중` | Working / in progress |
     | `작업 완료` | Completed |
     | `작업 취소` | Canceled |
     | `작업 보류` | Paused |

   - If status also appears in metadata or an index, update it atomically with the body's status.
   - Do not substitute approximate wording, invent additional states, or leave conflicting states in the same block.

Use stable, machine-readable entry and field boundaries. Prompt text resembling headings, numbers, or internal markers must not break parsing. Preserve multiline plans, progress text, and nested numbering through edits and copies. Use the supplied implementation's length-framed fields rather than inventing a competing serialization.

## 5. Ordinary Prompts and Mid-Task Corrections

For a new ordinary task request:

1. Determine terminal exception tags and the request's relationship to current work.
2. Resolve this task's own Diary scope.
3. Before task work, record the block number, timestamp, exact prompt, ordered plan, and `작업 중` together.
4. Update section 4 at each meaningful milestone.
5. Only after all applicable work has actually been performed, record final progress, journal notes, and `작업 완료`.

For a correction, addition, priority change, or “do this other thing first” instruction during active work:

- Unless the user explicitly pauses the earlier task, do not create a new ordinary block or mark the earlier task paused.
- First add a checkpoint in section 4 of the same block so the prior state can be resumed accurately.
- Append the exact new prompt beneath the existing prompt with a `+` marker and correction timestamp.
- Revise the priorities in section 3 if needed.
- Apply checkpoint → prompt append → plan replacement in one atomic operation.
- Keep status `작업 중`.
- Finishing the intervening request does not complete the original unfinished work.

Difficulty, a blocker, or the need to end a response is not evidence of completion, cancellation, or a user-requested pause. Record the unfinished state, reason, and remaining work honestly.

## 6. Exact Pause, Resume, and Cancellation Semantics

**Pause**

- When the user requests a pause, record the final progress, remaining work, and exact resumption point in section 4, and set `작업 보류`.
- Commit the checkpoint and status change together.
- Leave the source block in its original position.

**Resume**

1. Identify the paused block the user means in this task's own Diary.
2. Do not turn the source block back into a working block in place.
3. Copy its contents into a new block at the physical bottom of the Diary.
4. Allocate a new number, `entry_id`, and current timestamp. The metadata must clearly identify it as a resumption of source block N.
5. Put the exact new resumption prompt above the copied original prompt.
6. Preserve the existing plan, progress, and journal. Adjust the plan if the resumed request changes the work.
7. Set only the new copy to `작업 중`; keep the source at `작업 보류`.
8. Do not create another ordinary block just for the resumption prompt.
9. When the resumed work is completed, atomically set both the resumed copy and its linked paused sources to `작업 완료`.
10. If pause/resume cycles have produced a chain, follow all linked source blocks. Validate that each source block number matches its `entry_id`, and reject cycles.

**Cancel**

- Set the task the user cancels to `작업 취소`.
- Canceling a resumed copy must not automatically complete its sources.
- Do not alter source states unless the user also instructs you to cancel those sources.

Completion propagation applies only to pause/resume block links within one Diary. It is not propagation between parent and child tasks.

## 7. `(nod)` and `(fast)`

Recognize exception tags only in the actual user's final non-whitespace tag area. A mention in explanatory text, a quotation, code example, attachment, ambient UI, or system message is not a directive.

**`(nod)` — no diary**

- Perform the request without Diary operations for that prompt: no creation, editing, recovery record, or status change.
- Do not secretly backfill the prompt or its result later.

**`(fast)` — urgent work first**

- Prioritize the requested work over advance Diary recording.
- Reduce unnecessary or excessive extra checks and process overhead; act as quickly as practicable.
- After completion, interruption, pause, or cancellation, but before ending the response, record the exact prompt, timestamps, actual execution plan, progress, lessons, and true status retrospectively.
- If earlier work was interrupted, preserve its resumption state afterward as well.
- This does not permit false completion claims or omission of essential authorization and data-protection checks.
- `(fast)` changes recording time, not block ownership or correction/resumption relationships. Record a new task in a new post-hoc block, an active-task correction in its existing block, and an explicit paused-task resumption in a linked copy. Do not duplicate a request across blocks.
- If urgent work is interrupted while unfinished and the user has not paused or canceled it, the status remains `작업 중`. If a tool cannot represent that state, repair the MCP workflow; do not lie with a completed or paused status to satisfy a tool limitation.

If both tags appear in the terminal tag area, `(nod)` takes precedence.

## 8. Child-Task Recording and Handoffs

- A child records its assignment in its own Diary. Apply the same rules if it delegates further.
- A parent's delegation message is not a directly received user prompt. Label its title or metadata **“Parent assignment”**, and record the delegation text distinctly.
- Do not duplicate the original user's entire prompt into every child Diary.
- In its own block, the parent records the child's unique ID, assignment purpose, Diary location, key results, and blockers.
- Keep detailed implementation logs in the child Diary. Return only the results and continuation information the parent needs.
- Child completion does not automatically complete a parent block.
- A child must not modify its parent's or siblings' block states or checkpoints.
- Receiving a child's result does not authorize the parent to overwrite the child's detailed history.
- If the direct user request and delegated work fall under `(nod)`, do not bypass that instruction through child logging. Preserve `(fast)` urgency through delegation as well.

## 9. Recovery After Compaction, Summarization, or Resumption

When context compaction, a summary handoff, execution resumption, or a memory gap is detected, perform the following before resuming substantive work, subject to the terminal exception rules:

1. Consult the persistent Diary rules and path-resolution instructions.
2. Confirm the current `thread_id` and immediate `parent_thread_id`, then resolve this task's Diary.
3. If a handoff provides an `entry_id`, explicitly select that working block.
4. If the entry ID was lost, perform a read-only lookup of working blocks in this Diary and match their objectives. With multiple candidates, do not choose a globally newest entry arbitrarily.
5. For ordinary recovery, retrieve a compact snapshot of the selected working block's plan, recent progress and checkpoints, lessons, and next action.
6. Initially inspect only existence, counts, sizes, or hashes for paused blocks and separate checkpoints. Read their bodies only when the user explicitly resumes or references that work, or asks about its status.
7. Expand to the selected full block only when its summary is insufficient. Do not repeatedly scan the entire Diary or parent, child, and sibling Diary bodies.
8. Once the recovery target is established, record the recovery and next action in that block's section 4. A missing target is not permission to write into another task's block.
9. Do not repeat completed work or assume unverified work is finished.

Every compaction handoff must retain at least: current ID, parent ID, absolute Diary path, `entry_id`, block number, current objective, latest checkpoint, and exact next action.

Status scanning must inspect actual block status fields, not count status-looking strings inside prompts. Reserve full-file rereads for genuinely necessary format recovery or corruption investigations.

## 10. Ambiguous Instructions and Questions About Earlier Work

- When the user asks whether earlier work was completed, how far it progressed, what remains, where an artifact is, or says “did we do that?”, “where did we get to?”, or “continue it,” consult relevant records in this task's Diary before answering from memory or a summary.
- Before inspecting files, starting servers, or checking external state for such a status question, locate relevant Diary records by task name, target, domain, artifact path, or other identifying keywords.
- Inspect the newest matching block first, working backward. Stop when the latest record establishes the state and verification evidence. Expand only to linked earlier records when a correction, resumption, missing detail, or conflict requires it.
- If no keyword candidate can be established, narrow the inquiry to this Diary's recent records, expanding backward only as needed. Use scoped MCP operations; do not invent an unavailable history-search tool or replace normal MCP access with manual whole-file scans. If necessary lookup support is missing, repair that MCP capability or ask for a precise block reference.
- Resolve ambiguous expressions by consulting related exact prompts, corrections, and lessons first. Expand to relevant earlier definitions or prohibitions, and a linked checkpoint when necessary.
- If the meaning remains unclear, or alternative interpretations would change an external action, stop the affected action, quote the ambiguous expression, and ask the user what it means. Do not fabricate a plausible interpretation.
- Do not add unnecessary clarification questions when meaning and scope are already clear.
- If the records indicate that current files or external state should be verified, start from the recorded authoritative paths and verification methods.
- When Diary records disagree with verified reality, preserve the discrepancy in the current block and use the verified current state.
- If relevant records are missing or contradictory, do not guess that the task was completed. Ask which prior work the user means.
- These rules apply to every prior-work status question, whether or not compaction occurred.

## 11. Tool Use

Normal Diary reads, writes, and recovery **must use the `continuity-journal` MCP**. The agent does not choose among MCP, a direct-core CLI, and manual editing. Any “safe alternative” elsewhere in these rules means repairing or reconnecting the MCP, or using the actual MCP bridge below—not directly editing a Diary.

### 11.1. Authoritative Paths and Installation on Another Machine

- `DIARY_BASE`: the verified absolute expansion of `~/Desktop/Diaries` in this environment. If another operating system requires a different location, agree on it with the user and keep persistent instructions and configuration consistent.
- Repository source package: `diary/mcps/continuity-journal/` inside `agent-toolbox`.
- Authoritative installed package: `<DIARY_BASE>/mcps/continuity-journal/`.
- MCP server: `<package>/scripts/continuity_mcp.py`.
- Runtime and storage configuration: `<package>/runtime.json`.
- MCP host configuration: `<package>/.mcp.json`.
- Public configuration example: `<package>/.mcp.example.json`; illustrative placeholders only, not an installed connection.
- Dependencies: `<package>/requirements.txt`.
- Per-machine configuration generator: `<package>/scripts/configure_runtime.py`.
- Actual MCP connection bridge: `<package>/scripts/continuity_mcp_client.py`.
- Operational contract: `<package>/references/CONTINUITY_PROTOCOL.md` for a fresh installation.
- Skill and package instructions: `<package>/skills/continuity-journal/SKILL.md` and `<package>/README.md`.
- `mcps` contains shared tool code; it is not a shared task Diary. All task records retain their separate root/`Child` locations.

Bring the `mcps/continuity-journal` package along with this prompt to another machine. The prompt does not conjure the implementation or prove that MCP is installed. If the package is missing, report that it must be obtained; do not invent an incomplete substitute and declare installation complete.

The current implementation uses POSIX file locking on macOS/Linux. On Windows, establish a compatible execution environment such as WSL and paths accessible from it. A native port must first preserve locking, atomicity, and isolation, with passing tests.

Installation sequence:

1. Place the supplied package at the authoritative installed location, and install `requirements.txt` in an appropriate Python environment. If using a virtual environment, run the server with that environment's interpreter too.
2. Confirm the authoritative operational contract exists at an actual absolute path. For a fresh installation, use the installed `references/CONTINUITY_PROTOCOL.md`; reuse and reconcile an existing authoritative contract when appropriate.
3. With that interpreter, run `scripts/configure_runtime.py`, supplying actual absolute paths for `--diary-base`, `--protocol-path`, and `--python`. Inspect the generated configuration first; then use `--write --configure-plugin` to create `runtime.json` and `.mcp.json` and add the generated MCP configuration to the installed plugin manifest. The pristine public manifest intentionally has no `mcpServers` entry: configure this installed copy before attempting plugin installation. The example configuration is not an executable default.
4. Register and connect the server described by the generated `.mcp.json` using the MCP host's supported server-registration or plugin-installation procedure. Never reuse another machine's username, interpreter path, or protocol path. Keep generated machine-specific settings out of the source repository.
5. Call `continuity_status` and verify that `runtime.source_root`, `server_path`, `config_path`, `diary_base`, `protocol_path`, `version`, and `python_executable` match the intended live installation. Seeing tools with familiar names is not sufficient verification.

### 11.2. Required MCP Tools

Use the pipeline's 16 tools:

- `diary_resolve_scope`: resolve this task's path read-only from the current and immediate parent IDs.
- `diary_register_scope`: register this task's directory, scope metadata, and empty Diary while preserving existing contents.
- `diary_start`: create the number, timestamp, exact prompt, ordered plan, and `작업 중` in one operation.
- `diary_progress_append`: append major progress or a checkpoint to the selected working block.
- `diary_correction`: atomically add a checkpoint, append `+` and the exact new prompt, and optionally replace the plan in the same working block.
- `diary_set_status`: atomically update a selected block's true status, final progress, and journal. Completing a resumed block also completes linked sources in the same Diary.
- `diary_list_active`: return summaries of this Diary's working and paused blocks. Prefer `continuity_resume` for ordinary compaction recovery that does not need paused content.
- `diary_read`: read the full prompt, plan, progress, journal, and metadata of one block explicitly selected by either `entry_id` or `block_number`. Use it when a summary is insufficient or a specific historical block is referenced.
- `diary_resume`: copy a paused block to the file's bottom with a new number and timestamp, preserving its source link and exact resumption prompt.
- `diary_record_fast`: after a new urgent task, record its actual execution and status retrospectively. An unfinished interruption can remain `작업 중`.
- `continuity_resume`: restore compact working-state snapshots for this Diary, and record the recovery and next action in the explicitly selected working block. Do not expand paused bodies in the response.
- `continuity_status`: inspect this scope's state, integrity metadata, actual MCP runtime paths, and version.
- `diary_finish`: retain compatibility with earlier tasks; prefer `diary_set_status` for normal status transitions.
- `checkpoint_save`: save this scope's single separate active checkpoint.
- `checkpoint_read`: read this scope's active checkpoint and the hash needed to guard clearing it.
- `checkpoint_clear`: clear this checkpoint only after actual completion and a matching hash from a fresh read.

Supply the actual current `thread_id` on every call, plus the immediate `parent_thread_id` for a child. Entry IDs and block numbers are interpreted only within that call's Diary. Never use an arbitrary Diary path, a globally newest block, or an inherited parent environment variable as an implicit current-task default. Do not keep a shared “currently selected task” in the server.

For `(fast)`, work first, then choose the tool matching the request's relationship: `diary_record_fast` for a new task, `diary_correction(posthoc=true)` for an active-block correction, or `diary_resume(posthoc=true)` for an explicit paused-task resumption. Follow with `diary_set_status` when needed to represent the true outcome. Fast correction/resumption calls without `posthoc=true` defer writing. `(nod)` takes precedence even over post-hoc recording.

### 11.3. Connection Refresh, Call Efficiency, and Errors

- Reuse the host's connected MCP during normal operation. Use the supplied single atomic calls for grouped actions such as prompt/plan/time/status creation, correction checkpoints, and source-completion propagation.
- Do not create a separate time-query MCP or a chain of Bash timestamp variables. Recording tools generate the actual timestamp together with the write. Supply an original receipt time through a supported field only when it is verifiable and needed.
- Read only the summaries needed for recovery and status checks. Expand selected blocks with `diary_read`; do not dump all Diary contents or other tasks' records on each call.
- Existing chats may retain a pre-update server or schema. Do not write through old tools before reconnecting to the current server and verifying its runtime. An old writer may corrupt or reject records using the new length-framed format.
- If the host cannot immediately reconnect, run `scripts/continuity_mcp_client.py` with the same Python environment, a method, required `--thread-id`, a child's `--parent-thread-id`, and JSON arguments. This bridge performs actual MCP `initialize` → `tools/list` validation → `tools/call`. It is not a direct-core CLI or a manual-editing substitute.
- Retain `continuity_cli.py` only for administrative maintenance and test compatibility. Do not select it as a normal Diary recording or recovery path.
- The bridge bounds request waits and never automatically retries writes. After a timeout or disconnect with an uncertain outcome, inspect the records in this scope before retrying. Avoid duplicate prompts, milestones, and resumed copies.
- If no MCP connection works, repair the connection/configuration or report the required action. Do not silently skip recording, switch to manual edits, or claim setup is complete. Existing `(fast)` and `(nod)` exceptions still apply.
- Improve the authoritative source under `mcps`, test it, and deploy it to the actual running installation. Do not change only an installation cache or leave an independent stale copy pretending to be authoritative.

## 12. Atomicity, Concurrency, Integrity, and Privacy

- Perform each Diary read-modify-write under that Diary directory's own lock.
- Allocate numbers and append blocks within the same lock interval to prevent duplicate numbering and lost concurrent writes.
- Write a temporary file, flush and `fsync`, then replace atomically, or use the operating system's equivalent. Never delete the authoritative file first and recreate it later.
- Prevent simultaneous registration of one ID beneath different parents with a short registry lock limited to initial identity registration. Do not combine ordinary task writes into one global content lock or shared journal.
- Path discovery is read-only. Scope registration is an explicit creation action; registration may also happen atomically during a necessary first normal write.
- Verify the exact prompt's hash, and update the digest when a correction changes the combined prompt. Preserve whitespace and line endings through edits and resume copies.
- Reject source-number/entry-ID mismatches, cyclic resume links, and links crossing into another Diary before any partial modification.
- Revalidate scope and file types immediately before writing so another file is not overwritten.
- Restrict Diary, scope, and checkpoint access to the local user as far as the platform permits.
- Never include private Diary records, identity metadata, checkpoints, or machine-specific configuration in public repositories, web roots, deployment artifacts, or external reports. Do not transmit them without the user's request. The sanitized framework source and generic documentation are distributable; private runtime records are not.

If a separate checkpoint is needed, use only `checkpoints/ACTIVE_CHECKPOINT.md` inside this task's own Diary directory. Do not share it with a parent or another task. Section 4 of the Diary block remains authoritative progress; do not proliferate extra checkpoint files. Clear a checkpoint only after reading its actual contents, confirming its hash still matches, and verifying the task is genuinely completed.

## 13. Migrating Existing Diaries and Moving Between Machines

- If shared or project-level legacy Diaries exist, migrate only blocks whose ownership by this task is established. Do not classify or delete all other tasks' records while those tasks perform their own migrations.
- A reusable label such as `/root` is insufficient evidence of ownership. Cross-check actual identity against conversation and record evidence.
- Preserve `entry_id`, original number, timestamps, exact prompts and hashes, status, progress, lessons, and pause/resume links.
- If the destination already contains the same ID, check for duplicates or content conflicts. Never overwrite newer content with an older copy.
- Do not remove a source without a recoverable copy and verified destination. If an actual move requires source removal, recheck the selected block's latest hash under the source lock and remove only that block.
- A legacy global Diary is migration history, not the authority for new work or recovery. Do not delete it wholesale or arbitrarily alter remaining task states before all verified migrations finish.
- If `Diary.md` exists, validate its contents before deliberately migrating to extensionless `Diary`. If both exist, resolve the conflict before overwriting either or treating both as authorities.
- Retain the same root/`Child` recursion and identity rules on another machine. Verify and update machine-specific absolute paths without arbitrarily changing task identity, prompt text, or status. Do not merge records when the mapping from new platform IDs to existing IDs is unclear.

## 14. Installation Verification and Completion Report

Do not damage real Diary data for testing. In isolated temporary directories, verify at least:

1. Root, sibling, child, and grandchild isolation for paths, numbers, states, and checkpoints.
2. Parent discovery using only the immediate parent's ID; rejection of missing or duplicate parents and invalid IDs.
3. Prevention of duplicate placement and concurrent registration of the same ID.
4. Rejection of mutation or recovery using another Diary's `entry_id`.
5. Preservation of prompt whitespace, CRLF, multiline plans, and progress content.
6. Atomic checkpoint/prompt/plan updates during corrections.
7. Pause→resume and repeated-resume source links, completion propagation, and prevention of false completion on cancellation.
8. Child completion leaving the parent state unchanged.
9. Compaction recovery avoiding unnecessary other-task or paused-body output.
10. No recording for `(nod)`, no advance recording for `(fast)`, and correct precedence when both occur.
11. Concurrent writes without lost records or duplicate numbers.
12. Stale-tool rejection, path validation, and agreement between the actual installed runtime and persistent instructions.

When updating an existing plugin or tool, do not stop after editing a temporary installation cache. Keep the authoritative source, rules, and tools consistent, and verify deployment to the installation actually in use.

Finish with a concise account of the global instructions actually changed, authoritative protocol location, current task's Diary path, MCP connection and bridge path, verification results, and any steps needed for already-open chats. Do not claim work you did not perform.

Thereafter, follow this routine without requiring the user to repeat it. Its continuity mechanism is not a promise to remember: it is persistent instructions, exact records, explicit scope, and recovery after compaction.
