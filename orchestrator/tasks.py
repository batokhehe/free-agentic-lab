"""YAML task-definition loading and queue ordering."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover - surfaced clearly at runtime
    raise ImportError(
        "PyYAML is required for YAML task definitions. Install with: "
        "python -m pip install pyyaml"
    ) from exc

REQUIRED_FIELDS = ("id", "title", "goal", "mode")
_TASK_SUFFIXES = (".yaml", ".yml")


class TaskError(Exception):
    """Raised when a task definition cannot be loaded."""


def load_task(path: str | os.PathLike[str]) -> Dict[str, Any]:
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TaskError(f"cannot load task {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise TaskError(f"task {p} must be a YAML mapping")
    for field in REQUIRED_FIELDS:
        if not data.get(field):
            raise TaskError(f"task {p} is missing required field {field!r}")
    data.setdefault("mode", "read_only")
    data.setdefault("priority", 0)
    data.setdefault("allowed_paths", [])
    data.setdefault("forbidden_paths", [])
    data.setdefault("tests", [])
    data.setdefault("approval_required", False)
    data["_path"] = str(p.resolve())
    return data


def discover_tasks(tasks_dir: str | os.PathLike[str]) -> List[Dict[str, Any]]:
    """Load every task file under ``tasks_dir`` (recursively).

    Ordering is by descending ``priority`` then ascending file path so the queue
    is deterministic.
    """
    root = Path(tasks_dir)
    if not root.is_dir():
        return []
    found: List[Dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in _TASK_SUFFIXES:
            found.append(load_task(path))
    found.sort(key=lambda t: (-int(t.get("priority", 0)), t["_path"]))
    return found


def find_task(tasks_dir: str | os.PathLike[str], task_id: str) -> Optional[Dict[str, Any]]:
    for task in discover_tasks(tasks_dir):
        if task.get("id") == task_id:
            return task
    return None
