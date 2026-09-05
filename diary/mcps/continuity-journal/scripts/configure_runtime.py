"""Generate this machine's explicit runtime and MCP launch configuration.

This configures files in the supplied MCP package, not the host application's
global settings. Review the printed configuration, then register/reload the
continuity-journal server in the host. No Diary records are changed. The optional
--configure-plugin flag connects the deployment copy's plugin manifest to its
generated MCP configuration; the checked-in example is not an installed server.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

if __package__:
    from .continuity_runtime import SOURCE_ROOT, _absolute_path, validate_runtime_paths
else:
    from continuity_runtime import SOURCE_ROOT, _absolute_path, validate_runtime_paths


def _write_json(path: Path, value: dict) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Refusing non-regular configuration: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build_configuration(diary_base: str, protocol_path: str, python: str) -> tuple[dict, dict]:
    base = _absolute_path(diary_base, "diary_base")
    protocol = _absolute_path(protocol_path, "protocol_path")
    executable = _absolute_path(python, "python")
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("python must identify an existing executable")
    validate_runtime_paths(base, protocol)
    runtime = {"schema_version": 1, "diary_base": str(base), "protocol_path": str(protocol)}
    mcp = {"mcpServers": {"continuity-journal": {
        "command": str(executable),
        "args": [str(SOURCE_ROOT / "scripts" / "continuity_mcp.py")],
        "cwd": str(SOURCE_ROOT),
        "env": {"CONTINUITY_CONFIG": str(SOURCE_ROOT / "runtime.json")},
    }}}
    return runtime, mcp


def configured_plugin_manifest() -> tuple[Path, dict]:
    """Prepare the requested deployment manifest update without writing it yet."""
    path = SOURCE_ROOT / ".codex-plugin" / "plugin.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Plugin configuration requires a regular .codex-plugin/plugin.json")
    with path.open("r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    if not isinstance(manifest, dict) or manifest.get("name") != "continuity-journal":
        raise ValueError("Refusing to configure an unrelated plugin manifest")
    if manifest.get("mcpServers") not in (None, "./.mcp.json"):
        raise ValueError("Refusing to replace an unrecognized plugin MCP configuration")
    manifest["mcpServers"] = "./.mcp.json"
    return path, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diary-base", required=True)
    parser.add_argument("--protocol-path", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--write", action="store_true", help="Write package runtime.json and .mcp.json")
    parser.add_argument("--configure-plugin", action="store_true",
                        help="With --write, point this deployment's plugin manifest to .mcp.json")
    args = parser.parse_args(argv)
    try:
        if args.configure_plugin and not args.write:
            raise ValueError("--configure-plugin requires --write; run without either flag for a preview")
        runtime, mcp = build_configuration(args.diary_base, args.protocol_path, args.python)
        plugin_update = configured_plugin_manifest() if args.configure_plugin else None
        if args.write:
            for destination in (SOURCE_ROOT / "runtime.json", SOURCE_ROOT / ".mcp.json"):
                if destination.is_symlink() or (destination.exists() and not destination.is_file()):
                    raise ValueError(f"Refusing non-regular configuration: {destination}")
            _write_json(SOURCE_ROOT / "runtime.json", runtime)
            _write_json(SOURCE_ROOT / ".mcp.json", mcp)
            if plugin_update is not None:
                _write_json(*plugin_update)
        print(json.dumps({"written": args.write, "source_root": str(SOURCE_ROOT),
                          "plugin_configured": args.configure_plugin,
                          "runtime": runtime, "mcp": mcp}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
