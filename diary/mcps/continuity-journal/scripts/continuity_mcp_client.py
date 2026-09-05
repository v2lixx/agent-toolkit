"""Call the canonical stdio MCP server when a host's loaded tools are stale.

This is an MCP client, not a direct-core Diary editor. Every invocation performs
initialize -> tools/list schema validation -> tools/call. It never retries writes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

if __package__:
    from .continuity_runtime import SOURCE_ROOT, load_runtime_config
else:
    from continuity_runtime import SOURCE_ROOT, load_runtime_config


async def call_tool(method: str, arguments: dict) -> dict:
    config = load_runtime_config()
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(SOURCE_ROOT / "scripts" / "continuity_mcp.py")],
        cwd=str(SOURCE_ROOT),
        env={"CONTINUITY_CONFIG": str(config.config_path)},
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
            await session.initialize()
            available = await session.list_tools()
            definition = next((tool for tool in available.tools if tool.name == method), None)
            if definition is None or "thread_id" not in definition.inputSchema.get("required", []):
                raise ValueError("Unknown tool or stale unscoped MCP schema; no write performed")
            if not isinstance(arguments.get("thread_id"), str) or not arguments["thread_id"]:
                raise ValueError("Explicit thread_id is required")
            result = await session.call_tool(method, arguments)
            return result.model_dump(mode="json", by_alias=True, exclude_none=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method")
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--parent-thread-id")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--json", dest="json_payload")
    sources.add_argument("--json-file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.json_payload is not None:
            raw = args.json_payload
        elif args.json_file is not None and str(args.json_file) != "-":
            with args.json_file.open("r", encoding="utf-8", newline="") as stream:
                raw = stream.read()
        else:
            raw = sys.stdin.read() if not sys.stdin.isatty() else "{}"
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict) or {"thread_id", "parent_thread_id"} & payload.keys():
            raise ValueError("JSON must be an object without identity overrides")
        payload["thread_id"] = args.thread_id
        if args.parent_thread_id is not None:
            payload["parent_thread_id"] = args.parent_thread_id
        result = asyncio.run(call_tool(args.method, payload))
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get("isError") else 0
    except Exception as error:
        print(json.dumps({"error": str(error), "retried": False,
                          "write_outcome": "If a request was sent, verify its result before retrying."},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
