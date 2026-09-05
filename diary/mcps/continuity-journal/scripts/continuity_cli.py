"""Administrative/test compatibility CLI for explicitly scoped continuity.

Not a normal agent workflow or a fallback for stale/unavailable MCP tools.
Use the installed MCP; reconnect it or use the actual MCP client bridge when
the calling chat has stale schemas. This CLI is retained for explicit admin
maintenance and test compatibility only.

Payloads are JSON objects supplied with --json, --json-file, or stdin.
Identity is supplied only by flags, never inferred from inherited environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
if __package__:
    from .continuity_dispatch import IDENTITY_METHODS, METHODS, invoke
else:
    from continuity_dispatch import IDENTITY_METHODS, METHODS, invoke


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method", choices=tuple(METHODS))
    parser.add_argument("--thread-id", required=True, help="Actual current thread/task identity")
    parser.add_argument("--parent-thread-id", help="Actual immediate parent identity, for child tasks")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--json", dest="json_payload", help="JSON object containing operation arguments")
    sources.add_argument("--json-file", type=Path, help="Read JSON object from this file; '-' means stdin")
    args = parser.parse_args(argv)
    try:
        if args.json_payload is not None:
            raw = args.json_payload
        elif args.json_file is not None and str(args.json_file) != "-":
            raw = args.json_file.read_text(encoding="utf-8")
        elif args.json_file is not None or not sys.stdin.isatty():
            raw = sys.stdin.read()
        else:
            raw = ""
        payload = json.loads(raw) if raw.strip() else {}
        result = invoke(
            args.method,
            thread_id=args.thread_id,
            parent_thread_id=args.parent_thread_id,
            payload=payload,
        )
    except (OSError, TypeError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
