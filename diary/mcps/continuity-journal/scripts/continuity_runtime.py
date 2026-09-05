"""Operator configuration and provenance for the portable local MCP runtime.

Configuration selects storage, never a current thread. Every MCP call still
requires an explicit thread_id. CONTINUITY_CONFIG is a server-startup override
for deployments/tests, not a per-tool path parameter.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RuntimeConfig:
    diary_base: Path
    protocol_path: Path
    config_path: Path


def _absolute_path(value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must be absolute without parent traversal")
    return path


def validate_runtime_paths(diary_base: Path, protocol_path: Path) -> None:
    """Reject unusable storage/protocol configuration without creating anything.

    A new Diary base may not exist yet. Existing path components must still be
    directories, while the configured base itself cannot be a symlink. System
    parent aliases are left for the scope resolver to canonicalize.
    """
    if diary_base.is_symlink():
        raise ValueError("diary_base must not be a symlink")
    for component in (diary_base, *diary_base.parents):
        if component.exists() and not component.is_dir():
            raise ValueError(f"diary_base path component is not a directory: {component}")
    if protocol_path.is_symlink() or not protocol_path.is_file():
        raise ValueError("protocol_path must identify an existing regular non-symlink file")
    if not os.access(protocol_path, os.R_OK):
        raise ValueError("protocol_path must be readable by the MCP process")


def load_runtime_config() -> RuntimeConfig:
    override = os.environ.get("CONTINUITY_CONFIG")
    config_path = _absolute_path(override, "CONTINUITY_CONFIG") if override else SOURCE_ROOT / "runtime.json"
    if not config_path.exists():
        # A copied package without configuration is not silently routed into a
        # different user's storage. Configure it explicitly before connecting.
        raise ValueError(f"MCP runtime configuration missing: {config_path}; run configure_runtime.py")
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("MCP runtime configuration must be a regular non-symlink file")
    with config_path.open("r", encoding="utf-8") as stream:
        raw = stream.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("MCP runtime configuration is too large")
    values = json.loads(raw)
    if not isinstance(values, dict) or set(values) != {"schema_version", "diary_base", "protocol_path"}:
        raise ValueError("Expected runtime schema_version, diary_base and protocol_path only")
    if type(values["schema_version"]) is not int or values["schema_version"] != 1:
        raise ValueError("Unsupported MCP runtime configuration schema")
    diary_base = _absolute_path(values["diary_base"], "diary_base")
    protocol_path = _absolute_path(values["protocol_path"], "protocol_path")
    validate_runtime_paths(diary_base, protocol_path)
    return RuntimeConfig(diary_base, protocol_path, config_path)


def runtime_info() -> dict:
    config = load_runtime_config()
    manifest = json.loads((SOURCE_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    return {
        "source_root": str(SOURCE_ROOT),
        "server_path": str(SOURCE_ROOT / "scripts" / "continuity_mcp.py"),
        "config_path": str(config.config_path),
        "diary_base": str(config.diary_base),
        "protocol_path": str(config.protocol_path),
        "version": manifest["version"],
        "python_executable": sys.executable,
    }
