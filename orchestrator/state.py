"""Resumable orchestrator state.

State is stored as JSON and written atomically (temp file + replace) so an
interrupted process never leaves a half-written state file. Loading a corrupt
or unexpected state file raises :class:`StateError` instead of silently
resetting, which preserves the "never silently reset" guarantee.

The state also records, per active run, the attempt counter, the last command
exit code and the ``needs_review`` flag so an ambiguous interruption is never
resumed without an explicit recovery decision. Approvals are recorded per
(action, run_id) and consumed on first use so they cannot authorize unrelated
tasks or actions.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

STATE_VERSION = 1
TERMINAL_STATUSES = {"completed", "failed", "blocked"}
VALID_STATUSES = {
    "pending",
    "running",
    "completed",
    "failed",
    "blocked",
    "awaiting_approval",
}


class StateError(Exception):
    """Raised when the state file is corrupt or structurally invalid."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "active_run": None,
        "tasks": {},
        "history": [],
        "approvals": {},
    }


def load_state(path: str | os.PathLike[str]) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return default_state()
    raw = p.read_text(encoding="utf-8").strip()
    if not raw:
        return default_state()
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StateError(f"state file is corrupt: {p}: {exc}") from exc
    _validate_state(state, p)
    # Backward-compatible migration: add empty approvals if the key predates
    # this field. This is a default fill, never a reset of user data.
    if "approvals" not in state:
        state["approvals"] = {}
    return state


def _validate_state(state: Any, path: Path) -> None:
    if not isinstance(state, dict):
        raise StateError(f"state root must be an object: {path}")
    for key in ("active_run", "tasks", "history"):
        if key not in state:
            raise StateError(f"state is missing required key {key!r}: {path}")
    if not isinstance(state["tasks"], dict):
        raise StateError(f"state 'tasks' must be an object: {path}")
    if not isinstance(state["history"], list):
        raise StateError(f"state 'history' must be a list: {path}")
    active = state.get("active_run")
    if active is not None and not isinstance(active, dict):
        raise StateError(f"state 'active_run' must be null or an object: {path}")


def save_state(path: str | os.PathLike[str], state: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def get_task_state(state: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    entry = state["tasks"].get(task_id)
    if entry is None:
        entry = {"status": "pending", "attempts": 0, "last_run_id": None, "updated_at": None}
        state["tasks"][task_id] = entry
    return entry


def set_task_status(
    state: Dict[str, Any],
    task_id: str,
    status: str,
    run_id: Optional[str] = None,
    attempts: Optional[int] = None,
) -> None:
    if status not in VALID_STATUSES:
        raise StateError(f"invalid task status: {status}")
    entry = get_task_state(state, task_id)
    entry["status"] = status
    entry["updated_at"] = utcnow()
    if run_id is not None:
        entry["last_run_id"] = run_id
    if attempts is not None:
        entry["attempts"] = attempts


def begin_run(
    state: Dict[str, Any],
    run_id: str,
    task_id: str,
    stage: str,
) -> None:
    state["active_run"] = {
        "run_id": run_id,
        "task_id": task_id,
        "stage": stage,
        "started_at": utcnow(),
        "attempts": 0,
        "last_exit_code": None,
        "status": "running",
        "needs_review": False,
        "needs_review_reason": "",
    }
    set_task_status(state, task_id, "running", run_id=run_id)


def active_attempts(state: Dict[str, Any]) -> int:
    active = state.get("active_run")
    if not active:
        return 0
    return int(active.get("attempts", 0))


def set_active_attempts(state: Dict[str, Any], attempts: int) -> None:
    active = state.get("active_run")
    if active is None:
        raise StateError("cannot set attempts: no active run")
    active["attempts"] = int(attempts)


def set_active_exit_code(state: Dict[str, Any], exit_code: Optional[int]) -> None:
    active = state.get("active_run")
    if active is None:
        return
    active["last_exit_code"] = exit_code


def get_active_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return a safe snapshot of the active run for resume decisions."""
    active = state.get("active_run")
    if not active:
        return {}
    return {
        "run_id": active.get("run_id"),
        "task_id": active.get("task_id"),
        "stage": active.get("stage"),
        "status": active.get("status", "running"),
        "attempts": int(active.get("attempts", 0)),
        "last_exit_code": active.get("last_exit_code"),
        "needs_review": bool(active.get("needs_review", False)),
        "needs_review_reason": active.get("needs_review_reason", "") or "",
        "paused": bool(active.get("status") == "paused_provider_limit"),
        "pause_reason": active.get("pause_reason", "") or "",
        "pause_models_tried": list(active.get("pause_models_tried") or []),
        "started_at": active.get("started_at"),
    }


def mark_needs_review(state: Dict[str, Any], reason: str) -> None:
    active = state.get("active_run")
    if active is None:
        raise StateError("cannot mark needs_review: no active run")
    active["needs_review"] = True
    active["needs_review_reason"] = reason
    active["status"] = "needs_review"


def update_stage(state: Dict[str, Any], stage: str) -> None:
    active = state.get("active_run")
    if active is None:
        raise StateError("cannot update stage: no active run")
    active["stage"] = stage


def mark_paused(
    state: Dict[str, Any],
    reason: str,
    models_tried: List[str],
    stage: str = "",
) -> None:
    """Record a provider-limit pause on the active run.

    The run is *not* finished: ``active_run`` stays set so a later ``--resume``
    continues from the persisted stage checkpoints. The task status remains
    ``running`` so the queue does not quietly move on.
    """
    active = state.get("active_run")
    if active is None:
        raise StateError("cannot pause: no active run")
    active["status"] = "paused_provider_limit"
    active["pause_reason"] = reason
    active["pause_models_tried"] = list(models_tried)
    active["pause_stage"] = stage
    active["paused_at"] = utcnow()


def is_paused(state: Dict[str, Any]) -> bool:
    active = state.get("active_run")
    return bool(active and active.get("status") == "paused_provider_limit")


def record_approval(
    state: Dict[str, Any],
    task_id: str,
    action: str,
    run_id: str,
) -> None:
    """Record an explicit human approval for (task, action) scoped to a run."""
    state.setdefault("approvals", {})
    state["approvals"][task_id] = state["approvals"].get(task_id, {})
    state["approvals"][task_id][action] = {
        "run_id": run_id,
        "approved_at": utcnow(),
    }


def consume_approval(
    state: Dict[str, Any],
    task_id: str,
    action: str,
    run_id: str,
) -> bool:
    """Consume one recorded approval if it matches (task, action, run).

    Approvals are single-use and run-scoped, so they cannot authorize a
    different action or a later run of the same task.
    """
    by_task = state.get("approvals", {}).get(task_id, {})
    entry = by_task.get(action)
    if not entry or entry.get("run_id") != run_id:
        return False
    del by_task[action]
    if not by_task:
        state["approvals"].pop(task_id, None)
    return True


def finish_run(
    state: Dict[str, Any],
    status: str,
    note: str = "",
) -> None:
    active = state.get("active_run")
    if active is None:
        return
    entry = {
        "run_id": active.get("run_id"),
        "task_id": active.get("task_id"),
        "status": status,
        "stage": active.get("stage"),
        "started_at": active.get("started_at"),
        "ended_at": utcnow(),
        "attempts": int(active.get("attempts", 0)),
        "last_exit_code": active.get("last_exit_code"),
        "note": note,
    }
    state["history"].append(entry)
    set_task_status(state, active["task_id"], status, run_id=active.get("run_id"))
    state["active_run"] = None


# --------------------------------------------------------------------------- #
# Single-runner lock
# --------------------------------------------------------------------------- #


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - Windows branch
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return True  # fail closed: assume alive if we cannot check
        return str(pid) in (proc.stdout or "")
    try:  # pragma: no cover - POSIX branch
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


def acquire_lock(lock_path: str | os.PathLike[str]) -> bool:
    """Atomically claim the single-runner lock.

    ``O_CREAT|O_EXCL`` makes the claim atomic. A stale lock left by a dead
    process is reclaimed, but a lock held by a live process returns ``False``
    so two runner instances can never process the queue concurrently.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            holder = int(payload.get("pid", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            holder = 0
        if holder and _pid_alive(holder):
            return False
        try:
            path.unlink()
        except OSError:
            return False
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "created_at": utcnow()}, handle)
        return True
    except FileExistsError:
        return False
    except OSError:
        return False


def release_lock(lock_path: str | os.PathLike[str]) -> None:
    try:
        Path(lock_path).unlink()
    except OSError:
        pass
