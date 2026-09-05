"""Synthetic, manually checkpointed recall comparison using actual Codex calls.

This is not a benchmark of automatic context compaction or internal model memory.
The Diary arm is harness-managed and may consume more recovered context than the
rolling-summary arm. All datasets and answers are synthetic. No private journals
or model reasoning are exported. Run --pilot before the bounded --run experiment.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODEL = "gpt-5.5"
EFFORT = "medium"
SEEDS = (17, 29, 43)
CHECKPOINTS = (4000, 8000, 16000, 32000)
SUMMARY_TARGET = 2048
PACKAGE = Path(__file__).resolve().parents[1] / "mcps" / "continuity-journal"
ENCODING_NAME = "o200k_base"


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def encoding():
    import tiktoken
    return tiktoken.get_encoding(ENCODING_NAME)


def token_count(value: str) -> int:
    return len(encoding().encode(value, disallowed_special=()))


def parse_codex_events(stdout: str) -> dict[str, Any]:
    """Retain final answer and provider usage, never reasoning or real thread IDs."""
    final = None
    usage = None
    events = []
    diagnostics = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        event_type = event.get("type", "")
        events.append(event_type)
        item = event.get("item", {})
        if item:
            kind = item.get("type")
            if kind not in {"reasoning", "agent_message", "error"}:
                raise RuntimeError(f"Rejected unexpected model item type: {kind}")
            if kind == "error":
                # CLI metadata warnings can coexist with a successful completed turn.
                message = item.get("message", "")
                matched = re.search(r"Model metadata for `([^`]+)` not found", message)
                diagnostics.append({"kind": "model_metadata_warning" if matched else "cli_warning",
                                    "cli_resolved_model": matched.group(1) if matched else None})
            if kind == "agent_message" and event_type == "item.completed":
                final = item.get("text")
        if event_type in {"turn.failed", "error"}:
            raise RuntimeError("Codex reported a failed turn; no automatic retry")
        if event_type == "turn.completed":
            usage = event.get("usage")
    if not isinstance(final, str) or not isinstance(usage, dict):
        raise RuntimeError("Missing final agent_message or completed-turn usage")
    # Usage contains counters, not instructions or opaque diagnostic payloads.
    def numeric_only(value):
        if value is None or isinstance(value, (int, float)):
            return value
        if isinstance(value, dict):
            return {str(k): numeric_only(v) for k, v in value.items()}
        raise RuntimeError("Unexpected non-numeric provider usage value")
    return {"response": final, "usage": numeric_only(usage), "event_types": events,
            "cli_diagnostics": diagnostics}


def codex_command(directory: Path, model: str | None = MODEL) -> list[str]:
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("codex executable unavailable")
    command = [executable, "exec", "--json", "--ephemeral", "--ignore-user-config",
               "--skip-git-repo-check", "-s", "read-only", "-C", str(directory),
               "-c", f"model_reasoning_effort={EFFORT}",
               "-c", "project_doc_max_bytes=0"]
    if model is not None:
        command += ["-m", model]
    for feature in ("plugins", "apps", "hooks", "multi_agent", "shell_tool", "unified_exec"):
        command += ["--disable", feature]
    return command + ["-"]


async def codex_call(prompt: str, *, model: str | None = MODEL, timeout: int = 180) -> dict[str, Any]:
    """Shared isolated real-model adapter; no retries or private-output persistence.

    Returns prompt,response,raw numeric usage, event-type names, measured wall time,
    and tokenizer estimates. Missing provider cache/reasoning counters stay null.
    """
    if not prompt.rstrip().endswith("(nod)"):
        raise ValueError("Synthetic model prompts must end with (nod)")
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="diary-recall-model-") as temporary:
        process = await asyncio.create_subprocess_exec(
            *codex_command(Path(temporary), model),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(prompt.encode()), timeout)
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError("Model call timed out; no automatic retry") from None
        if process.returncode:
            # stderr can contain private filesystem/authentication diagnostics.
            combined = stdout.decode(errors="replace") + "\n" + stderr.decode(errors="replace")
            categories = []
            for category, pattern in {
                "unknown-feature": r"(?i)(unknown|unrecognized) feature[^\n]*",
                "unsupported-model": r"(?i)(model[^\n]*(not supported|not available|not found)|unsupported model)[^\n]*",
                "authentication": r"(?i)(unauthorized|authentication|not logged in|sign in)",
                "rate-limit": r"(?i)(rate.limit|usage limit|quota)",
                "sandbox": r"(?i)(sandbox|operation not permitted)",
            }.items():
                if re.search(pattern, combined):
                    categories.append(category)
            # Only known error diagnostics, never entire stderr/events, leave the process.
            details = []
            for line in combined.splitlines():
                error_event = False
                try:
                    candidate = json.loads(line)
                    error_event = candidate.get("type") in {"error", "turn.failed"} or candidate.get("item", {}).get("type") == "error"
                    if error_event:
                        line = compact_json(candidate.get("error", candidate.get("message", candidate.get("item", {}).get("message", "unspecified error"))))
                except (ValueError, AttributeError):
                    pass
                if error_event or any(word in line.lower() for word in ("unknown feature", "unrecognized feature", "not supported", "not available", "not found", "error:")):
                    line = re.sub(r"(?:/Users|/home|/private|/var|/opt|/Library)/[^\s\"']+", "<local-path>", line)
                    line = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}", "<id>", line)
                    line = re.sub(r"sk-[A-Za-z0-9_-]+|eyJ[A-Za-z0-9_.-]+", "<redacted>", line)
                    details.append(line[:700])
            raise RuntimeError(f"Codex exited {process.returncode}; categories={categories}; diagnostics={details[-5:]}")
        result = parse_codex_events(stdout.decode())
    model_match = re.search(r"(?m)^model:\s*(\S+)", stderr.decode(errors="replace"))
    result.update({"prompt": prompt, "model": model, "requested_model": model,
                   "observed_model": model_match.group(1) if model_match else None, "reasoning_effort": EFFORT,
                   "wall_seconds": round(time.perf_counter() - started, 4),
                   "prompt_tokens_estimated": token_count(prompt),
                   "response_tokens_estimated": token_count(result["response"]),
                   "stderr_present": bool(stderr),
                   "cached_input_tokens": result["usage"].get("cached_input_tokens"),
                   "reasoning_tokens": result["usage"].get("reasoning_tokens", result["usage"].get("reasoning_output_tokens"))})
    return result


def build_corpus(seed: int) -> list[dict[str, Any]]:
    """Generate all event histories and fixed scoring oracles before any inference."""
    rng = random.Random(seed)
    requirements = {}
    stale = {}
    completed = []
    chunks = []
    history = ""
    target_history = ""
    enc = encoding()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    def value():
        return "v-" + "".join(rng.choice(alphabet) for _ in range(16))
    for stage, target_tokens in enumerate(CHECKPOINTS):
        previous_count = len(requirements)
        new_count = 32 if stage == 0 else 16
        lines = [f"Checkpoint {stage + 1}. CURRENT TASK: ORION-{seed}. Chronological event log."]
        for index in range(previous_count, previous_count + new_count):
            key = f"R{index + 1:03d}"
            requirements[key] = value()
            lines.append(f"USER, ORION-{seed}: Requirement {key} must use the exact code `{requirements[key]}`. Preserve its case and punctuation.")
        corrected = rng.sample(list(requirements), 4 if stage == 0 else 8)
        for key in corrected:
            old = requirements[key]
            stale.setdefault(key, []).append(old)
            requirements[key] = value()
            lines.append(f"USER CORRECTION, ORION-{seed}: Replace {key}={old} with {key}={requirements[key]}. The previous value is obsolete; the newest correction wins.")
        for action in range(3):
            action_id = f"A{stage * 3 + action + 1:03d}"
            completed.append(action_id)
            lines.append(f"VERIFIED RESULT, ORION-{seed}: Action {action_id} completed successfully. Do not repeat {action_id}; reuse its verified result.")
        lines.append(f"USER, ORION-{seed}: Remaining permitted next actions are P001 (review the next incoming event batch) and P002 (prepare the final handoff when requested). Neither is completed.")
        relevant = "\n".join(lines)
        filler = []
        for number in range(target_tokens):
            other = rng.choice(("LYRA", "VEGA", "DRACO"))
            key = rng.choice(list(requirements))
            filler.append(f"UNRELATED TASK {other}-{seed}, observation {number}: {key}={value()} belongs only to {other}; this routine inventory note does not revise ORION. Its owner will review a separate report.\n")
            if token_count("\n".join((history, relevant, "".join(filler)))) >= target_tokens:
                break
        # Only the final irrelevant filler is truncated; all relevant events stay intact.
        raw = "\n".join((history, relevant, "".join(filler))).lstrip("\n")
        raw = enc.decode(enc.encode(raw, disallowed_special=())[:target_tokens])
        if token_count(raw) != target_tokens:
            raise RuntimeError("Fixture tokenizer did not produce target checkpoint size")
        chunk = raw[len(history):].lstrip("\n") if history else raw
        target_history += ("\n" if target_history else "") + relevant
        history = raw
        oracle = {"requirements": dict(requirements), "stale": {k: list(v) for k, v in stale.items()},
                  "completed_actions": list(completed), "pending_actions": ["P001", "P002"],
                  "unknown_requirements": ["R998", "R999"]}
        chunks.append({"seed": seed, "checkpoint_tokens": target_tokens, "history": history,
                       "new_events": chunk, "target_events": relevant,
                       "target_history": target_history, "oracle": oracle})
    return chunks


def score_response(response: str, oracle: dict[str, Any]) -> dict[str, Any]:
    invalid = False
    try:
        data = json.loads(response)
        if not isinstance(data, dict) or not isinstance(data.get("requirements"), dict):
            raise ValueError("Invalid response schema")
    except (ValueError, TypeError):
        invalid = True
        data = {"requirements": {}, "completed_actions": [], "next_actions": []}
    answers = data["requirements"]
    required = oracle["requirements"]
    wrong = [key for key, value in required.items() if answers.get(key) != value]
    abstained = [key for key in required if answers.get(key) is None]
    corrections = list(oracle["stale"])
    correction_wrong = [key for key in corrections if answers.get(key) != required[key]]
    stale = [key for key in corrections if answers.get(key) in oracle["stale"][key]]
    next_actions = data.get("next_actions", [])
    if not isinstance(next_actions, list):
        next_actions = []
        invalid = True
    repeated = sorted(set(x for x in next_actions if isinstance(x, str)) & set(oracle["completed_actions"]))
    unknowns = oracle["unknown_requirements"]
    invented = [key for key in unknowns if answers.get(key) is not None]
    done = data.get("completed_actions", [])
    if not isinstance(done, list):
        done = []
        invalid = True
    pending_set = set(oracle["pending_actions"])
    next_set = set(x for x in next_actions if isinstance(x, str))
    return {"invalid_response": invalid, "requirements_total": len(required),
            "requirement_failures": len(wrong), "requirement_failure_rate": len(wrong) / len(required),
            "failed_requirement_ids": wrong, "known_requirement_abstentions": len(abstained),
            "corrected_requirements_total": len(corrections), "latest_correction_failures": len(correction_wrong),
            "stale_value_count": len(stale), "stale_requirement_ids": stale,
            "completed_actions_total": len(oracle["completed_actions"]),
            "completed_action_recall_failures": len(set(oracle["completed_actions"]) - set(x for x in done if isinstance(x, str))),
            "repeated_completed_actions": len(repeated), "repeated_completed_action_ids": repeated,
            "pending_actions_total": len(pending_set),
            "missing_pending_actions": len(pending_set - next_set),
            "invalid_next_actions": len(next_set - pending_set),
            "unknown_requirements_total": len(unknowns), "unknown_requirement_fabrications": len(invented)}


def continuation_prompt(memory: str, oracle: dict[str, Any], seed: int) -> str:
    # Only identifiers are probe inputs. The expected values/oracle are never supplied.
    identifiers = list(oracle["requirements"]) + oracle["unknown_requirements"]
    return (f"Synthetic continuation test. Current task is ORION-{seed}. Use only the supplied memory. "
            "Apply the newest correction for this task, ignore unrelated tasks, and never repeat a verified completed action. "
            "Return only JSON with requirements (each requested ID mapped to its exact current code or null if unknown), "
            "completed_actions (all known completed action IDs), and next_actions (remaining permitted action IDs). "
            "Do not guess a missing value. No tools, files, or external actions.\n"
            f"Requested requirement IDs: {compact_json(identifiers)}\n<MEMORY>\n{memory}\n</MEMORY>\n(nod)")


async def pilot(model: str | None = MODEL) -> dict[str, Any]:
    result = await codex_call('Synthetic route check. No tools or external actions. Return only {"route":"ok"}.\n(nod)', model=model, timeout=120)
    if json.loads(result["response"]) != {"route": "ok"}:
        raise RuntimeError("Pilot response did not match route check")
    return result


async def header_pilot() -> dict[str, Any]:
    """One explicitly authorized default-route header check; no raw diagnostics exported."""
    prompt = 'Synthetic route check. No tools or external actions. Return only {"route":"ok"}.\n(nod)'
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="diary-recall-header-") as temporary:
        command = codex_command(Path(temporary), None)
        command.remove("--json")
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(prompt.encode()), 120)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError("Header pilot timed out; no retry") from None
    text = stdout.decode() + "\n" + stderr.decode()
    model = re.search(r"(?m)^model:\s*(\S+)", text)
    if process.returncode or not model or json.loads(stdout.decode().strip()) != {"route": "ok"}:
        raise RuntimeError("Header pilot did not establish the default CLI model")
    return {"response": stdout.decode().strip(), "cli_resolved_model": model.group(1),
            "model_identity_source": "Codex CLI non-JSON model header; not backend response attestation",
            "wall_seconds": round(time.perf_counter() - started, 4), "prompt": prompt,
            "raw_diagnostics_exported": False, "provider_usage": None}


def summary_prompt(previous: str, new_events: str, seed: int) -> str:
    return (f"Create the next rolling handoff summary for synthetic task ORION-{seed}. "
            f"Target at most {SUMMARY_TARGET} tokens. Preserve exact current requirement codes, "
            "the latest corrections, verified completed action IDs that must not be repeated, "
            "and remaining permitted actions. Discard obsolete values and unrelated tasks. "
            "Use compact structured text if helpful. Do not invent missing facts. "
            "The previous summary and the new event batch below are your only inputs. "
            "This is a manual handoff, not automatic compaction. No tools or external actions.\n"
            f"<PREVIOUS_SUMMARY>\n{previous or '(none)'}\n</PREVIOUS_SUMMARY>\n"
            f"<NEW_EVENTS>\n{new_events}\n</NEW_EVENTS>\n(nod)")


def build_manifest(corpora: dict[int, list[dict]]) -> dict:
    return {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": MODEL, "requested_model": MODEL,
        "model_identity_source": "Successful default-route pilot and Codex CLI model header, then explicitly requested for every experimental call; not backend attestation",
        "reasoning_effort": EFFORT, "seeds": list(SEEDS),
        "checkpoints_history_tokens": list(CHECKPOINTS), "tokenizer": ENCODING_NAME,
        "summary_target_tokens": SUMMARY_TARGET, "summary_budget_is_prompt_target_not_forced_truncation": True,
        "max_planned_calls_excluding_route_pilot": 39,
        "summary_updates": 12, "paired_continuations": 24, "final_full_history_controls": 3,
        "oracle_generated_before_model_calls": True,
        "model_oracle_access": "Oracle values never included in probes; model tools disabled; fresh empty working directory.",
        "arm_order": "Alternate summary/diary first by seed index plus checkpoint index.",
        "logging": "Both arms receive the exact same mixed-task raw event batches. Harness-managed MCP writes do not inject answer maps or privileged relevance filtering.",
        "diary_recovery": "Actual stdio continuity_resume followed by explicitly selected diary_read expansion; includes all retained target events.",
        "limitations": [
            "Manual checkpoints, not automatic product context compaction.",
            "Harness-managed logging of the same mixed-task event batches, not autonomous agent adherence.",
            "Unequal memory lengths: recovered Diary is not capped at the rolling-summary target.",
            "Synthetic exact-code recall, not a representative work-quality or real-world retention benchmark.",
            "Three seeds only; no population-level or statistical significance claim.",
            "Provider usage includes CLI system context and possible cache effects; tokenizer estimates are not billable usage.",
        ],
        "datasets": [{"seed": seed, "checkpoint_tokens": c["checkpoint_tokens"],
                      "history_sha256": sha_text(c["history"]),
                      "target_events_sha256": sha_text(c["target_events"]),
                      "oracle_sha256": sha_text(compact_json(c["oracle"])),
                      "minimal_complete_answer_tokens_estimated": token_count(compact_json({
                          "requirements": c["oracle"]["requirements"] | {key: None for key in c["oracle"]["unknown_requirements"]},
                          "completed_actions": c["oracle"]["completed_actions"],
                          "next_actions": c["oracle"]["pending_actions"]}))}
                     for seed, chunks in corpora.items() for c in chunks],
        "versions": {"python": platform.python_version(), "platform": platform.system(),
                     "mcp": importlib.metadata.version("mcp"), "tiktoken": importlib.metadata.version("tiktoken"),
                     "runtime_package": json.loads((PACKAGE / ".codex-plugin" / "plugin.json").read_text())["version"]},
        "source_sha256": sha_text(Path(__file__).read_text()),
    }


async def run_seed(seed: int, seed_index: int, chunks: list[dict], output: Path) -> list[dict]:
    # Reuse the tested actual stdio MCP transport, not direct-core calls.
    from measure_mcp_overhead import connected_server, guard_temporary_scope, result_data, warm_call, validate_arguments
    results = []
    with tempfile.TemporaryDirectory(prefix="diary-recall-store-") as directory:
        root = Path(directory)
        base = root / "Diaries"
        config = root / "runtime.json"
        config.write_text(compact_json({"schema_version": 1, "diary_base": str(base),
                                       "protocol_path": str(PACKAGE / "references" / "CONTINUITY_PROTOCOL.md")}))
        async with connected_server(config) as (session, _, schemas, _):
            await guard_temporary_scope(session, config, base)
            thread_id = f"synthetic-orion-{seed}"
            entry_id = None
            previous = ""
            for stage, checkpoint in enumerate(chunks):
                mcp_samples = []
                async def call_mcp(name: str, payload: dict):
                    arguments = {"thread_id": thread_id, **payload}
                    validate_arguments(schemas, name, arguments)
                    raw, elapsed = await warm_call(session, name, arguments)
                    data = result_data(raw)
                    mcp_samples.append({"method": name, "wall_ms": round(elapsed, 4),
                                        "result_tokens_estimated": token_count(compact_json(raw))})
                    return data
                if entry_id is None:
                    record = await call_mcp("diary_start", {"prompt": checkpoint["new_events"],
                        "title": f"Synthetic ORION-{seed} continuation", "plan": [
                            "Preserve the current task's exact requirements and latest corrections.",
                            "Reuse verified completed actions and retain only permitted pending actions."],
                        "received_at": "2026-01-01T00:00:00+00:00"})
                    entry_id = record["entry_id"]
                else:
                    await call_mcp("diary_correction", {"entry_id": entry_id,
                        "prompt": checkpoint["new_events"], "checkpoint_progress": [
                            f"Received synthetic checkpoint {stage + 1}; preserve earlier raw events and apply the chronological additions below."]})
                summary = await codex_call(summary_prompt(previous, checkpoint["new_events"], seed))
                summary.update({"seed": seed, "checkpoint_tokens": checkpoint["checkpoint_tokens"], "arm": "summary_update",
                                "summary_target_tokens": SUMMARY_TARGET,
                                "summary_target_exceeded": summary["response_tokens_estimated"] > SUMMARY_TARGET})
                # Preserve actual model output even if it exceeds the target. Never force forgetting.
                previous = summary["response"]
                write_json(output / f"seed-{seed}-checkpoint-{checkpoint['checkpoint_tokens']}-summary-update.json", summary)
                results.append(summary)
                print(compact_json({"seed": seed, "checkpoint": checkpoint["checkpoint_tokens"], "arm": "summary_update",
                                    "wall_seconds": summary["wall_seconds"], "usage": summary["usage"]}), flush=True)
                await call_mcp("continuity_resume", {"entry_id": entry_id,
                    "reason": "Synthetic manually checkpointed continuation", "next_action": "Answer the supplied synthetic probe from this scoped record."})
                recovered = await call_mcp("diary_read", {"entry_id": entry_id})
                # Exclude private filesystem paths, generated entry IDs and scope metadata.
                memory = compact_json({key: recovered[key] for key in ("prompt", "plan", "progress", "journal")})
                for earlier in chunks[:stage + 1]:
                    if earlier["target_events"] not in recovered["prompt"]:
                        raise RuntimeError("MCP recovery lost raw target events")
                arms = [("summary", previous), ("diary", memory)]
                if (seed_index + stage) % 2:
                    arms.reverse()
                for arm, arm_memory in arms:
                    answer = await codex_call(continuation_prompt(arm_memory, checkpoint["oracle"], seed))
                    answer.update({"seed": seed, "checkpoint_tokens": checkpoint["checkpoint_tokens"], "arm": arm,
                                   "memory_tokens_estimated": token_count(arm_memory), "memory_sha256": sha_text(arm_memory),
                                   "metrics": score_response(answer["response"], checkpoint["oracle"])})
                    if arm == "diary":
                        answer["mcp_operations"] = mcp_samples
                    write_json(output / f"seed-{seed}-checkpoint-{checkpoint['checkpoint_tokens']}-{arm}.json", answer)
                    results.append(answer)
                    print(compact_json({"seed": seed, "checkpoint": checkpoint["checkpoint_tokens"], "arm": arm,
                                        "wall_seconds": answer["wall_seconds"], "usage": answer["usage"],
                                        "failure_rate": answer["metrics"]["requirement_failure_rate"]}), flush=True)
            final = chunks[-1]
            control = await codex_call(continuation_prompt(final["history"], final["oracle"], seed))
            control.update({"seed": seed, "checkpoint_tokens": final["checkpoint_tokens"], "arm": "full_history_control",
                            "memory_tokens_estimated": token_count(final["history"]),
                            "metrics": score_response(control["response"], final["oracle"])})
            write_json(output / f"seed-{seed}-full-history-control.json", control)
            results.append(control)
            print(compact_json({"seed": seed, "arm": "full_history_control", "usage": control["usage"],
                                "failure_rate": control["metrics"]["requirement_failure_rate"]}), flush=True)
    return results


async def run_benchmark(output: Path) -> dict:
    if output.exists():
        raise ValueError("Output directory already exists; no accidental overwrite or implicit resume")
    corpora = {seed: build_corpus(seed) for seed in SEEDS}
    manifest = build_manifest(corpora)
    process = await asyncio.create_subprocess_exec("codex", "--version", stdout=asyncio.subprocess.PIPE)
    version, _ = await process.communicate()
    manifest["versions"]["codex_cli"] = version.decode().strip()
    # Precommit all dataset/oracle hashes and actual fixtures before the first experiment call.
    write_json(output / "manifest.json", manifest)
    write_json(output / "synthetic-fixtures-and-oracles.json", {str(k): v for k, v in corpora.items()})
    tasks = []
    try:
        async with asyncio.TaskGroup() as group:
            for index, seed in enumerate(SEEDS):
                tasks.append(group.create_task(run_seed(seed, index, corpora[seed], output)))
    except Exception:
        write_json(output / "failure.json", {"status": "incomplete", "automatic_retries": False,
                                             "note": "Experiment stopped; completed call files remain. Do not interpret absent results as successes."})
        raise
    calls = [call for task in tasks for call in task.result()]
    result = {"status": "complete", "actual_calls": len(calls), "manifest_sha256": sha_text(compact_json(manifest)),
              "calls": [{key: call[key] for key in ("seed", "checkpoint_tokens", "arm", "usage", "wall_seconds", "response_tokens_estimated")}
                        | ({"metrics": call["metrics"], "memory_tokens_estimated": call["memory_tokens_estimated"]} if "metrics" in call else {})
                        for call in calls]}
    if len(calls) != 39:
        raise RuntimeError("Unexpected model-call count")
    write_json(output / "results.json", result)
    return result


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--header-pilot", action="store_true")
    parser.add_argument("--model", default=MODEL, help="Explicit model for a route pilot; 'default' omits -m.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sum((args.pilot, args.run, args.header_pilot)) != 1:
        parser.error("Choose exactly one of --pilot, --header-pilot, or --run")
    if args.header_pilot:
        result = asyncio.run(header_pilot())
        write_json(args.output, result)
        print(compact_json(result))
        return
    if args.pilot:
        result = asyncio.run(pilot(None if args.model == "default" else args.model))
        write_json(args.output, result)
        print(compact_json({k: result[k] for k in ("response", "usage", "wall_seconds", "event_types")}))
    else:
        result = asyncio.run(run_benchmark(args.output))
        print(compact_json({"status": result["status"], "actual_calls": result["actual_calls"]}))


if __name__ == "__main__":
    main()
