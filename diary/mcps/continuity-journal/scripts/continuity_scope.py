"""Read-only thread routing and atomic identity registration for Diary trees.

Public callers supply a stable current thread ID and, for children, only the
immediate parent ID.  Parent discovery reads directory names and scope metadata,
never another thread's Diary contents.  There is deliberately no environment,
active-thread, or old global-Diary fallback.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

if __package__:
    from .continuity_runtime import load_runtime_config
else:
    from continuity_runtime import load_runtime_config


SCHEMA_VERSION = 1
PROTOCOL_PATH = Path.home() / "Desktop" / "Diaries" / "CONTINUITY_PROTOCOL.md"
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)


def _thread_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(
            f"{field} must be an explicit stable thread ID (1-128 ASCII letters, "
            "digits, underscores or hyphens; first character alphanumeric); "
            "paths and /root agent names are not thread IDs"
        )
    return value


@dataclass(frozen=True)
class Scope:
    thread_id: str
    parent_thread_id: str | None
    ancestor_thread_ids: tuple[str, ...]
    base_root: Path
    root: Path
    diary_path: Path
    checkpoint_path: Path
    protocol_path: Path = PROTOCOL_PATH

    def metadata(self) -> dict[str, Any]:
        lineage = [*self.ancestor_thread_ids, self.thread_id]
        return {
            "schema_version": SCHEMA_VERSION,
            "thread_id": self.thread_id,
            "parent_thread_id": self.parent_thread_id,
            "root_thread_id": lineage[0],
            "ancestor_thread_ids": list(self.ancestor_thread_ids),
            "lineage": lineage,
            "canonical_directory": str(self.root),
            "diary_path": str(self.diary_path),
            "checkpoint_path": str(self.checkpoint_path),
            "protocol_path": str(self.protocol_path),
        }


def _directory_chain(path: Path) -> None:
    """Reject links before resolving or traversing any existing path component."""
    for component in [*reversed(path.parents), path]:
        if component.is_symlink():
            raise ValueError(f"Diary scope path must not traverse a symlink: {component}")
        if component.exists() and not component.is_dir():
            raise ValueError(f"Diary scope directory is not a directory: {component}")


def _regular_file(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Diary scope file must not be a symlink: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"Diary scope file is not a regular file: {path}")


def _make_scope(base: Path, lineage: tuple[str, ...]) -> Scope:
    if not lineage or len(set(lineage)) != len(lineage):
        raise ValueError("Diary ancestry contains a repeated ID or cycle")
    for index, value in enumerate(lineage):
        _thread_id(value, f"lineage[{index}]")
    directory = base / lineage[0]
    for value in lineage[1:]:
        directory = directory / "Child" / value
    return Scope(
        thread_id=lineage[-1],
        parent_thread_id=lineage[-2] if len(lineage) > 1 else None,
        ancestor_thread_ids=lineage[:-1],
        base_root=base,
        root=directory,
        diary_path=directory / "Diary",
        checkpoint_path=directory / "checkpoints" / "ACTIVE_CHECKPOINT.md",
        protocol_path=load_runtime_config().protocol_path,
    )


def _lineage_from_path(base: Path, path: Path) -> tuple[str, ...]:
    try:
        parts = path.relative_to(base).parts
    except ValueError as exc:
        raise ValueError("Diary scope is outside the configured base root") from exc
    if not parts or len(parts) % 2 == 0 or any(
        parts[index] != "Child" for index in range(1, len(parts), 2)
    ):
        raise ValueError(f"Not a canonical Diary tree path: {path}")
    return tuple(parts[::2])


def _validate_scope(scope: Scope) -> None:
    expected = _make_scope(scope.base_root, _lineage_from_path(scope.base_root, scope.root))
    if scope != expected:
        raise ValueError("Scope fields do not match its canonical identity and paths")
    _directory_chain(scope.root)
    _directory_chain(scope.checkpoint_path.parent)
    for path in (
        scope.diary_path,
        scope.root / "Diary.md",
        scope.root / "scope.json",
        scope.root / ".continuity-journal.lock",
        scope.checkpoint_path,
    ):
        _regular_file(path)
    if (scope.root / "Diary.md").exists():
        if scope.diary_path.exists():
            raise ValueError(
                f"Reconciliation required: both Diary and Diary.md exist in {scope.root}; "
                "select and preserve one canonical Diary before writing"
            )
        raise ValueError(
            f"Migration required: {scope.root / 'Diary.md'} exists but the canonical "
            "Diary file does not; resolve the legacy filename before writing"
        )
    metadata_path = scope.root / "scope.json"
    if metadata_path.exists():
        try:
            with metadata_path.open("r", encoding="utf-8") as stream:
                raw = stream.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("scope metadata is too large")
            existing = json.loads(raw)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(f"Invalid Diary scope metadata: {metadata_path}") from exc
        if existing != scope.metadata():
            raise ValueError(f"Diary scope identity metadata mismatch: {metadata_path}")


def _scope_directories(base: Path) -> Iterator[Path]:
    """Iterate canonical directory positions, without descending into Diaries."""
    if not base.exists():
        return
    stack = [base]
    while stack:
        container = stack.pop()
        if container.is_symlink():
            continue
        with os.scandir(container) as entries:
            children = [
                Path(entry.path)
                for entry in entries
                if _ID_RE.fullmatch(entry.name)
                and entry.is_dir(follow_symlinks=False)
            ]
        for directory in children:
            # A directory left by interrupted bootstrap is not registered yet.
            if (directory / "scope.json").exists() or (directory / "Diary").exists():
                yield directory
            child_container = directory / "Child"
            if child_container.is_dir() and not child_container.is_symlink():
                stack.append(child_container)


def _find_registered(base: Path, thread_id: str) -> list[Path]:
    return [path for path in _scope_directories(base) if path.name == thread_id]


def resolve_scope(
    thread_id: str,
    parent_thread_id: str | None = None,
    *,
    base_root: Path | None = None,
) -> Scope:
    """Resolve one thread without creating files; children need only parent ID.

    A parent must already have scope.json or Diary in its canonical tree
    directory.  Missing/ambiguous parents, reused IDs and incompatible metadata
    are errors rather than invitations to choose a different thread's Diary.
    """
    current = _thread_id(thread_id, "thread_id")
    parent = _thread_id(parent_thread_id, "parent_thread_id") if parent_thread_id is not None else None
    supplied_base = Path(base_root) if base_root is not None else load_runtime_config().diary_base
    if ".." in supplied_base.parts:
        raise ValueError("Diary base root must not contain parent traversal")
    supplied_base = supplied_base.absolute()
    # System parents may be aliases (notably macOS /var -> /private/var).
    # Canonicalize those before choosing the trust root, but never accept a
    # symlink for the configured Diaries directory or anything below it.
    if supplied_base.is_symlink():
        raise ValueError(f"Diary base root must not be a symlink: {supplied_base}")
    base = supplied_base.parent.resolve() / supplied_base.name
    _directory_chain(base)
    # One name-only traversal serves both parent lookup and current-ID collision
    # detection. Never cache a mutable global active task or read Diary bodies.
    relevant = {current, parent} if parent is not None else {current}
    locations: dict[str, list[Path]] = {identity: [] for identity in relevant}
    for directory in _scope_directories(base):
        if directory.name in locations:
            locations[directory.name].append(directory)
    if parent is None:
        lineage = (current,)
    else:
        if current == parent:
            raise ValueError("A thread cannot be its own parent (ancestry cycle)")
        matches = locations[parent]
        if not matches:
            raise ValueError(f"Parent thread {parent!r} is missing or not registered in {base}")
        if len(matches) != 1:
            raise ValueError(f"Parent thread {parent!r} is ambiguous: multiple Diary directories")
        parent_lineage = _lineage_from_path(base, matches[0])
        _validate_scope(_make_scope(base, parent_lineage))
        lineage = (*parent_lineage, current)
    scope = _make_scope(base, lineage)
    _validate_scope(scope)
    matches = locations[current]
    if any(path != scope.root for path in matches):
        raise ValueError(f"Thread ID {current!r} is already registered at another Diary location")
    return scope


@contextmanager
def _registry_locked(base: Path) -> Iterator[None]:
    """Serialize initial identities, never Diary contents or scope-lock acquisition.

    Lock ordering is caller's per-scope lock -> this registry lock.  This helper
    and its callers must never acquire a per-scope lock while holding it.
    """
    _directory_chain(base)
    base.mkdir(parents=True, exist_ok=True)
    _directory_chain(base)
    lock_path = base / ".scope-registry.lock"
    _regular_file(lock_path)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ValueError(f"Cannot safely open Diary scope registry lock: {lock_path}") from exc
    with os.fdopen(descriptor, "r+", encoding="utf-8") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ValueError(f"Diary scope registry lock is not a regular file: {lock_path}")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _register_identity(scope: Scope) -> None:
    """Called with registry lock held, following revalidation of all routing."""
    scope.root.mkdir(parents=True, exist_ok=True)
    _validate_scope(scope)
    metadata_path = scope.root / "scope.json"
    if metadata_path.exists():
        return
    handle, temporary = tempfile.mkstemp(prefix=".scope.json.", dir=scope.root)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(scope.metadata(), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, metadata_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ensure_scope(scope: Scope) -> Scope:
    """Register identity atomically; the caller must hold this scope's write lock.

    This creates the canonical directory, scope.json and a shared registry lock,
    not a Diary or checkpoint.  A very short registry lock serializes only first
    identity registration across different parents.  Existing identities do not
    acquire it.  Lock order is scope -> registry, never registry -> scope.
    """
    _validate_scope(scope)
    metadata_path = scope.root / "scope.json"
    if metadata_path.exists():
        resolved = resolve_scope(scope.thread_id, scope.parent_thread_id, base_root=scope.base_root)
        if resolved != scope:
            raise ValueError("Diary scope changed between resolution and registration")
        return scope
    with _registry_locked(scope.base_root):
        _validate_scope(scope)
        resolved = resolve_scope(scope.thread_id, scope.parent_thread_id, base_root=scope.base_root)
        if resolved != scope:
            raise ValueError("Diary scope changed between resolution and registration")
        _register_identity(scope)
    return scope
