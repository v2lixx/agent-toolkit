# Continuity Journal MCP

The local execution layer for [Diary](../../README.md), an agent continuity
framework in [agent-toolkit](../../../README.md). It provides 16 explicitly
thread-scoped MCP tools for exact prompt logging, task state transitions,
recursive child journals, and compact recovery after context loss.

This package includes the server, a real stdio MCP client bridge, an agent
[skill](skills/continuity-journal/SKILL.md), the complete
[operating protocol](references/CONTINUITY_PROTOCOL.md),
and an isolated test suite. The repository contains **software and instructions,
not anyone's journal**.

## Requirements

- A POSIX environment: macOS or Linux. The implementation uses `fcntl` locks;
  native Windows is not supported. Use a Linux environment such as WSL instead.
- Python 3.11 or newer is the intended baseline. The release suite was executed
  with Python 3.11.9 and 3.14; other interpreter/platform combinations need validation.
- The pinned packages in [requirements.txt](requirements.txt), installed in a
  dedicated virtual environment.
- An MCP-capable agent host, explicit current thread identity, and a writable
  private storage directory.

## Configure a deployment copy

Use the full [setup prompt](../../SETUP_PROMPT.md) when an agent should install
the framework and its persistent behavioral instructions. The commands below
configure the runtime only; they do not configure an agent host automatically.

1. Copy this package into `~/Desktop/Diaries/mcps/continuity-journal`. Keep actual
   Diary records outside your Git checkout. If a deployment already exists,
   inspect and back it up before updating it; do not overwrite another setup.
2. From the copied package, create the environment and install dependencies:

   ```sh
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

3. Set task-specific shell variables to the absolute deployment paths. On a
   typical macOS/Linux setup:

   ```sh
   DIARY_PACKAGE="$(pwd -P)"
   DIARY_STORAGE="$HOME/Desktop/Diaries"
   DIARY_PROTOCOL="$DIARY_PACKAGE/references/CONTINUITY_PROTOCOL.md"
   ```

   Run these commands from the **deployment package**, not the source checkout.
   Select a different private storage root if needed. Never repurpose `HOME` or
   an agent host's existing environment variables.

4. Preview the generated configuration without writing files:

   ```sh
   .venv/bin/python scripts/configure_runtime.py \
     --diary-base "$DIARY_STORAGE" \
     --protocol-path "$DIARY_PROTOCOL"
   ```

5. Generate the machine-specific configuration and connect the deployment's
   plugin manifest to it:

   ```sh
   .venv/bin/python scripts/configure_runtime.py \
     --diary-base "$DIARY_STORAGE" \
     --protocol-path "$DIARY_PROTOCOL" \
     --write --configure-plugin
   ```

   This writes `runtime.json` and `.mcp.json` with absolute paths and adds or updates
   only `mcpServers` in the deployment manifest to `./.mcp.json`. Existing
   metadata is retained. Plain `--write` leaves the manifest alone, which is
   useful when registering `.mcp.json` directly in a non-plugin MCP host.

6. Register the generated server configuration in your MCP host, or install the
   **configured deployment copy** as a plugin through the host's supported
   mechanism. Installing the skill alone is not enough. Do not install or run
   the checked-in `.mcp.example.json`: it contains illustrative absolute paths,
   not a functioning local server.
7. Reconnect the host and call `continuity_status` with your actual `thread_id`
   and, for a child, `parent_thread_id`. Verify its `runtime.source_root`,
   `server_path`, `config_path`, `diary_base`, `protocol_path`, interpreter, and
   version before the first write.

Missing or invalid runtime configuration fails closed. No server-wide
"currently selected task" or inherited thread-ID fallback is used. Copying the
package to another machine requires regenerating its configuration; do not
reuse another user's absolute paths.

## MCP tool surface

Every tool requires `thread_id`; children also supply their immediate
`parent_thread_id`. Callers cannot pass arbitrary storage paths.

| Tool | Purpose |
| --- | --- |
| `diary_resolve_scope` | Resolve identity and recursive paths without creating files. |
| `diary_register_scope` | Explicitly initialize the caller's scope and empty Diary. |
| `diary_start` | Append exact prompt, numbered plan, timestamp, and working state. |
| `diary_progress_append` | Append meaningful milestones and context-switch checkpoints. |
| `diary_correction` | Checkpoint first, append the exact correction, optionally replace the plan. |
| `diary_set_status` | Update status, final progress, and lessons together. |
| `diary_resume` | Copy a paused block to the bottom while retaining its source relationship. |
| `diary_list_active` | Return working/paused summaries within this Diary only. |
| `diary_read` | Read one explicitly selected full block. |
| `diary_record_fast` | Record a new urgent task after execution or interruption. |
| `continuity_resume` | Recover compact working state and record the next action. |
| `continuity_status` | Inspect scoped state, integrity metadata, and runtime provenance. |
| `diary_finish` | Compatibility entry point for finishing an existing entry. |
| `checkpoint_save` | Compatibility entry point for a scoped checkpoint. |
| `checkpoint_read` | Read the caller's compatibility checkpoint. |
| `checkpoint_clear` | Guarded removal of the caller's compatibility checkpoint. |

Normal Diary workflows **must use MCP**. `continuity_cli.py` is retained only
for administrative/test compatibility; it is not an alternative workflow.

### Stale host connection

Reconnect the host to the deployed server. If the host still exposes stale
schemas, the included bridge uses the actual MCP initialize → list → call
protocol:

```sh
.venv/bin/python scripts/continuity_mcp_client.py continuity_status \
  --thread-id '<actual-current-thread-id>' --json '{}'
```

Add `--parent-thread-id '<actual-immediate-parent-id>'` for a child. JSON
operation arguments can also come from `--json-file` or standard input; thread
identity belongs in the explicit flags, not the JSON payload.

The bridge bounds each MCP request wait at 30 seconds and never automatically
retries writes. After an uncertain response, inspect the target entry before
deciding whether a retry would duplicate a mutation. If neither the connected
MCP nor the bridge works, repair or report the connection issue instead of
silently editing a Diary by hand.

## Compatibility and data format

Public documentation and the skill are in English. The existing journal
format, field markers, KST timestamps, and four canonical status values are
intentionally unchanged:

| Canonical stored value | English meaning |
| --- | --- |
| `작업 중` | Working |
| `작업 완료` | Complete |
| `작업 취소` | Cancelled |
| `작업 보류` | Paused |

Do not translate those machine-facing values or the existing numbered field
headings in stored records. Prompt text remains in the author's original
language and is preserved exactly, including whitespace. Korean test fixtures
exercise this compatibility; they are synthetic, not private Diary exports.

The core combines per-Diary locking, atomic file replacement, exact prompt
hashes, length-framed fields, and scoped lineage validation. These safeguards
protect the journal's structure; they do not guarantee that an LLM's recorded
claim is true, prevent privileged local tampering, or create cross-user access
control for clients sharing a server.

## Test without installing or touching a journal

From this package directory, using an environment with the requirements:

```sh
python3 -m unittest discover -s tests -v
```

The suite uses temporary protocols, operator configurations, Diary trees, and
fresh MCP subprocesses. A clean checkout does **not** need `runtime.json` or a
real Diary. Integration tests verify the temporary runtime provenance before
issuing writes. Tests cover exact prompt round trips, marker collisions,
concurrent numbering, scope isolation, pause/resume ancestry, `(fast)`/`(nod)`,
configuration rejection, and actual stdio MCP calls.

These are deterministic implementation checks, not a measured recall score or
proof that every model will follow the protocol. See the framework README for
the evidence and limitations of continuity recovery.

## Source files and private deployment state

Publish `scripts/`, `tests/`, `skills/`, `requirements.txt`, the plugin manifest,
and `*.example.json`. Keep `.venv/`, `__pycache__/`, generated `runtime.json`,
generated `.mcp.json`, actual `Diary` files, `scope.json`, checkpoints, logs, and
local backups out of version control. The template manifest deliberately omits
`mcpServers`: a pristine package contains source and a skill, not a configured
MCP connection. The `--configure-plugin` step adds that connection after its
launch file exists. Configuration must precede installation.

Review the Git staging area before publishing. Ignore rules reduce accidental
disclosure but do not remove files that have already been tracked.
