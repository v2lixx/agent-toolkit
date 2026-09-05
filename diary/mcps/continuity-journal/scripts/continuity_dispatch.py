"""Internal explicitly scoped domain dispatch shared by MCP and admin tests.

Normal agent workflows use MCP, not this module or the administrative CLI.
No store or current identity is retained between operations.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

if __package__:
    from .continuity_core import ContinuityStore
else:
    from continuity_core import ContinuityStore


# Immutable and explicit: never expose arbitrary store attributes.
METHODS = MappingProxyType({
    "diary_start": "diary_start",
    "diary_progress_append": "diary_progress_append",
    "diary_correction": "diary_correction",
    "diary_set_status": "diary_set_status",
    "diary_resume": "diary_resume",
    "diary_list_active": "diary_list_active",
    "diary_read": "diary_read",
    "diary_finish": "diary_finish",
    "diary_record_fast": "diary_record_fast",
    "continuity_resume": "continuity_resume",
    "continuity_status": "status",
    "checkpoint_save": "checkpoint_save",
    "checkpoint_read": "checkpoint_read",
    "checkpoint_clear": "checkpoint_clear",
    "diary_resolve_scope": None,
    "diary_register_scope": "register_scope",
})
IDENTITY_METHODS = frozenset({"diary_start", "diary_resume", "diary_record_fast"})


def invoke(
    method: str,
    *,
    thread_id: str,
    parent_thread_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch one operation to a fresh store bound to the explicit identity."""
    if method not in METHODS:
        raise ValueError(f"Unsupported continuity method: {method}")
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object")
    arguments = dict(payload or {})
    if {"thread_id", "parent_thread_id"} & arguments.keys():
        raise ValueError("Supply thread identity only via the explicit identity arguments")
    if method in IDENTITY_METHODS:
        label = arguments.pop("task_thread", None)
        if label is not None and label != thread_id:
            raise ValueError("task_thread must equal thread_id; use title for a human-readable label")
        arguments["task_thread"] = thread_id
    store = ContinuityStore.for_thread(thread_id, parent_thread_id=parent_thread_id)
    if method == "diary_resolve_scope":
        if arguments:
            raise ValueError("diary_resolve_scope does not accept a payload")
        return store.scope.metadata()
    result = getattr(store, METHODS[method])(**arguments)
    if method == "continuity_status":
        if __package__:
            from .continuity_runtime import runtime_info
        else:
            from continuity_runtime import runtime_info
        result["runtime"] = runtime_info()
    return result
