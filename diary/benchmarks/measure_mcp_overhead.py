"""Measure Diary bookkeeping through real MCP, without model inference.

Run from any working directory, with mcp and tiktoken installed:
    python diary/benchmarks/measure_mcp_overhead.py --output result.json

All journal data lives in TemporaryDirectory and is removed at exit. Output is
aggregate metadata plus numeric per-operation samples, never prompts, local
paths, identities, tool schemas, or journal bodies. Token counts are o200k_base
serialization proxies, not provider usage, billing, or agent reasoning tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PACKAGE = Path(__file__).resolve().parents[1] / "mcps" / "continuity-journal"
PROTOCOL = PACKAGE / "references" / "CONTINUITY_PROTOCOL.md"
SKILL = PACKAGE / "skills" / "continuity-journal" / "SKILL.md"
SCENARIOS = {"short": 80, "medium": 800, "long": 4000}
OPERATIONS = ("diary_start", "diary_progress_append", "diary_set_status")


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def summarize(values: list[float | int]) -> dict[str, float | int]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "p95_nearest_rank": round(ordered[max(0, math.ceil(len(values) * 0.95) - 1)], 4),
        "minimum": round(ordered[0], 4),
        "maximum": round(ordered[-1], 4),
    }


class TokenCounter:
    def __init__(self, name: str = "o200k_base") -> None:
        import tiktoken
        self.name = name
        self.encoding = tiktoken.get_encoding(name)

    def text(self, value: str) -> int:
        return len(self.encoding.encode(value, disallowed_special=()))

    def json(self, value: Any) -> int:
        return self.text(compact_json(value))


def synthetic_prompt(target_tokens: int, counter: TokenCounter) -> str:
    unit = (
        "Review the synthetic inventory fixture. Keep item identifiers stable, "
        "check the expected total, record the verified artifact and the remaining "
        "step, and preserve the owner's explicit constraint. "
    )
    repeated = unit * (target_tokens // max(counter.text(unit), 1) + 2)
    tokens = counter.encoding.encode(repeated)
    while len(tokens) < target_tokens:
        repeated += unit
        tokens = counter.encoding.encode(repeated)
    return counter.encoding.decode(tokens[:target_tokens])


def operation_payload(name: str, *, prompt: str, entry_id: str | None = None) -> dict:
    if name == "diary_start":
        return {"prompt": prompt, "title": "Synthetic inventory verification", "plan": [
            "Read the synthetic fixture and preserve its identifiers.",
            "Verify the expected total and record evidence.",
            "Summarize the result and the exact next step.",
        ]}
    if name == "diary_progress_append":
        return {"entry_id": entry_id, "milestones": [
            "Synthetic fixture checked: identifiers are unchanged and the expected total matches.",
            "Verification is complete; only the final summary and status transition remain.",
        ]}
    if name == "diary_set_status":
        return {"entry_id": entry_id, "status": "작업 완료", "progress": [
            "Finished the synthetic verification and recorded the result.",
        ], "journal": "Preserve verified identifiers; do not repeat a completed check without new evidence."}
    raise ValueError(f"Unsupported benchmark operation: {name}")


def validate_arguments(schemas, name: str, arguments: dict) -> None:
    definition = next((tool for tool in schemas.tools if tool.name == name), None)
    if definition is None:
        raise ValueError("Benchmark method is not advertised by the MCP server")
    allowed = set(definition.inputSchema.get("properties", {}))
    unknown = set(arguments) - allowed
    if unknown:
        raise ValueError(f"Unknown benchmark MCP arguments: {sorted(unknown)}")
    required = set(definition.inputSchema.get("required", []))
    if not required <= arguments.keys():
        raise ValueError("Benchmark is missing required MCP arguments")


def result_data(result: dict) -> dict:
    if result.get("isError"):
        raise RuntimeError("MCP reported an error; benchmark aborted without automatic retry")
    texts = [item["text"] for item in result.get("content", []) if item.get("type") == "text"]
    value = json.loads("\n".join(texts))
    if not isinstance(value, dict):
        raise RuntimeError("Unexpected MCP result shape")
    return value


def sample_call(name: str, arguments: dict, result: dict, elapsed_ms: float,
                counter: TokenCounter) -> dict:
    result_data(result)
    input_tokens = counter.json({"name": name, "arguments": arguments})
    result_tokens = counter.json(result)
    text_result = "\n".join(item["text"] for item in result.get("content", []) if item.get("type") == "text")
    return {
        "operation": name,
        "elapsed_ms": round(elapsed_ms, 4),
        "serialized_call_input_tokens": input_tokens,
        "serialized_call_result_tokens": result_tokens,
        "serialized_input_plus_result_tokens": input_tokens + result_tokens,
        "result_text_only_tokens": counter.text(text_result),
    }


@asynccontextmanager
async def connected_server(config: Path):
    params = StdioServerParameters(
        command=sys.executable, args=[str(PACKAGE / "scripts" / "continuity_mcp.py")],
        cwd=str(PACKAGE), env={"CONTINUITY_CONFIG": str(config)},
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        started = time.perf_counter()
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
                initialization = await session.initialize()
                schemas = await session.list_tools()
                startup_ms = (time.perf_counter() - started) * 1000
                yield session, initialization, schemas, startup_ms


async def warm_call(session: ClientSession, name: str, arguments: dict) -> tuple[dict, float]:
    started = time.perf_counter()
    result = await session.call_tool(name, arguments)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return result.model_dump(mode="json", by_alias=True, exclude_none=True), elapsed_ms


async def bridge_call(config: Path, name: str, arguments: dict) -> tuple[dict, float]:
    payload = {key: value for key, value in arguments.items() if key != "thread_id"}
    command = [sys.executable, str(PACKAGE / "scripts" / "continuity_mcp_client.py"),
               name, "--thread-id", arguments["thread_id"], "--json", compact_json(payload)]
    started = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "CONTINUITY_CONFIG": str(config)},
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=45)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("Fresh MCP bridge exceeded 45 seconds; no retry was attempted") from None
    elapsed_ms = (time.perf_counter() - started) * 1000
    if process.returncode:
        raise RuntimeError("Fresh MCP bridge failed; local error text omitted from public results")
    return json.loads(stdout), elapsed_ms


async def guard_temporary_scope(session: ClientSession, config: Path, base: Path) -> None:
    raw, _ = await warm_call(session, "continuity_status", {"thread_id": "benchmark-guard"})
    status = result_data(raw)
    if status["runtime"]["config_path"] != str(config) or status["runtime"]["diary_base"] != str(base):
        raise RuntimeError("Server did not prove temporary runtime isolation; no writes permitted")
    if base.exists():
        raise RuntimeError("Unexpected prior data in a fresh benchmark fixture")


async def measure_group(*, mode: str, scenario: str, count: int, session: ClientSession,
                        config: Path, counter: TokenCounter, schemas) -> dict:
    prompt = synthetic_prompt(SCENARIOS[scenario], counter)
    thread = f"benchmark-{mode}-{scenario}"
    blocks = []
    for index in range(count):
        entry_id = None
        operations = []
        for name in OPERATIONS:
            arguments = {"thread_id": thread, **operation_payload(name, prompt=prompt, entry_id=entry_id)}
            validate_arguments(schemas, name, arguments)
            if mode == "warm":
                raw, elapsed_ms = await warm_call(session, name, arguments)
            else:
                raw, elapsed_ms = await bridge_call(config, name, arguments)
            if name == "diary_start":
                entry_id = result_data(raw)["entry_id"]
            operations.append(sample_call(name, arguments, raw, elapsed_ms, counter))
        read_result, _ = await warm_call(session, "diary_read", {"thread_id": thread, "entry_id": entry_id})
        stored = result_data(read_result)
        expected_finish = operation_payload("diary_set_status", prompt=prompt, entry_id=entry_id)
        expected_plan = operation_payload("diary_start", prompt=prompt)["plan"]
        expected_progress = operation_payload("diary_progress_append", prompt=prompt)["milestones"] + expected_finish["progress"]
        if stored["prompt"] != prompt or stored["status"] != "작업 완료" or stored["journal"] != expected_finish["journal"] or stored["plan"] != expected_plan:
            raise RuntimeError("Stored benchmark block failed exact prompt/status/journal validation")
        if not all(any(item.endswith(text) for item in stored["progress"]) for text in expected_progress):
            raise RuntimeError("Milestones or final progress were not stored in the benchmark block")
        blocks.append({
            "block_index": index + 1,
            "cycle_elapsed_ms": round(sum(item["elapsed_ms"] for item in operations), 4),
            "cycle_serialized_input_tokens": sum(item["serialized_call_input_tokens"] for item in operations),
            "cycle_serialized_result_tokens": sum(item["serialized_call_result_tokens"] for item in operations),
            "cycle_serialized_input_plus_result_tokens": sum(item["serialized_input_plus_result_tokens"] for item in operations),
            "stored_block_verified": True,
            "operations": operations,
        })
    fields = ("cycle_elapsed_ms", "cycle_serialized_input_tokens", "cycle_serialized_result_tokens",
              "cycle_serialized_input_plus_result_tokens")
    return {
        "mode": mode, "scenario": scenario, "prompt_tokens": counter.text(prompt),
        "prompt_characters": len(prompt), "blocks": count,
        "history_growth": "All measured blocks in this scenario/mode share one initially empty Diary.",
        "summary": {field: summarize([block[field] for block in blocks]) for field in fields},
        "operation_latency_ms": {name: summarize([
            item["elapsed_ms"] for block in blocks for item in block["operations"] if item["operation"] == name
        ]) for name in OPERATIONS},
        "samples": blocks,
    }


async def run_benchmark(warm_blocks: int, bridge_blocks: int, counter: TokenCounter) -> dict:
    if warm_blocks < 1 or bridge_blocks < 0:
        raise ValueError("warm_blocks must be positive and bridge_blocks nonnegative")
    with tempfile.TemporaryDirectory(prefix="diary-mcp-benchmark-") as directory:
        root = Path(directory).resolve()
        base, protocol, config = root / "Diaries", root / "PROTOCOL.md", root / "runtime.json"
        protocol.write_text(PROTOCOL.read_text(encoding="utf-8"), encoding="utf-8")
        config.write_text(compact_json({"schema_version": 1, "diary_base": str(base),
                                      "protocol_path": str(protocol)}), encoding="utf-8")
        async with connected_server(config) as (session, initialization, schemas, startup_ms):
            await guard_temporary_scope(session, config, base)
            schema_json = schemas.model_dump(mode="json", by_alias=True, exclude_none=True)
            costs = {
                "tools_list_json_tokens": counter.json(schema_json),
                "server_instructions_text_tokens": counter.text(initialization.instructions or ""),
                "protocol_text_tokens": counter.text(PROTOCOL.read_text(encoding="utf-8")),
                "skill_text_tokens": counter.text(SKILL.read_text(encoding="utf-8")),
            }
            groups = []
            for mode, count in (("warm", warm_blocks), ("fresh_bridge", bridge_blocks)):
                if count:
                    for scenario in SCENARIOS:
                        groups.append(await measure_group(mode=mode, scenario=scenario, count=count,
                                                          session=session, config=config, counter=counter, schemas=schemas))
            manifest = json.loads((PACKAGE / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
            return {
                "schema_version": 1,
                "experiment": "actual_stdio_mcp_bookkeeping_overhead",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "environment": {
                    "python": platform.python_version(), "os": platform.system(),
                    "os_release": platform.release(), "architecture": platform.machine(),
                    "mcp_version": importlib.metadata.version("mcp"),
                    "tiktoken_version": importlib.metadata.version("tiktoken"),
                    "package_version": manifest["version"],
                },
                "methodology": {
                    "cycle": list(OPERATIONS), "token_encoding": counter.name,
                    "token_counting": "Tokenize compact, sorted-key, ensure_ascii=false JSON for tools/call params and SDK CallToolResult; excludes JSON-RPC framing and host chat wrappers.",
                    "result_text_only": "Secondary per-operation count for text content only; hosts may expose different result envelopes.",
                    "warm_latency": "Tool request round trip on one already initialized server; excludes startup, schema retrieval, tokenization, and model work.",
                    "fresh_bridge_latency": "One fresh client and server process per tool call, including initialization, list_tools, call, shutdown, and subprocess collection.",
                    "percentile": "Nearest-rank empirical p95; small sample sizes limit tail estimates.",
                    "scenarios": "Synthetic prompt lengths 80/800/4000 o200k_base tokens; identical three-step plan and one progress update; no model executes these tasks.",
                    "isolation": "Temporary configuration and storage; read-only provenance check before all writes; no existing journals touched.",
                    "validation": "Reject unknown MCP input keys against live schemas; after every cycle, read back the selected block and assert exact prompt, final progress, journal, and completed status. Validation read latency/tokens are excluded from the measured three-write cycle.",
                    "excluded": ["LLM inference", "reasoning tokens", "planning generation", "provider billing", "network model latency", "rework saved", "end-to-end task time", "compaction recovery"],
                    "limitations": ["Single host and run, sequential scenarios, no CPU scheduling control.",
                                    "Random entry IDs, hashes, and temporary path lengths affect serialization token counts.",
                                    "Fresh bridge includes process/import overhead; it is not representative of a persistent host connection.",
                                    "Completed blocks accumulate within each scenario; this is not a large-history scalability test."],
                },
                "no_diary_bookkeeping_baseline": {"calls_per_block": 0, "serialized_tokens_per_block": 0,
                                                  "diary_bookkeeping_ms_per_block": 0,
                                                  "interpretation": "Definition of omitting Diary bookkeeping, not an independently measured model/task baseline."},
                "startup": {
                    "observed_initialize_and_list_tools_ms": round(startup_ms, 4),
                    "tool_count": len(schemas.tools), "token_proxies": costs,
                    "reference_bundle_sum_tokens": sum(costs.values()),
                    "illustrative_amortization_over_100_blocks_tokens": round(sum(costs.values()) / 100, 2),
                    "interpretation": "Separate reference exposure costs, not charged once per measured block. Actual host prompt assembly, caching, and repeated recovery reads differ.",
                },
                "source_hashes": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (
                    Path(__file__), PACKAGE / "scripts" / "continuity_mcp.py",
                    PACKAGE / "scripts" / "continuity_core.py", PROTOCOL, SKILL,
                )},
                "groups": groups,
            }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warm-blocks", type=int, default=30)
    parser.add_argument("--bridge-blocks", type=int, default=10)
    parser.add_argument("--encoding", default="o200k_base")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = asyncio.run(run_benchmark(args.warm_blocks, args.bridge_blocks, TokenCounter(args.encoding)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(compact_json({"experiment": result["experiment"], "groups": [
        {"mode": group["mode"], "scenario": group["scenario"], "blocks": group["blocks"],
         "latency_ms": group["summary"]["cycle_elapsed_ms"],
         "serialized_tokens": group["summary"]["cycle_serialized_input_plus_result_tokens"]}
        for group in result["groups"]
    ]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
