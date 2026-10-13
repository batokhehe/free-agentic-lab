"""Shared fixtures for orchestrator self-tests."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import state as state_mod  # noqa: E402
from orchestrator import workflow  # noqa: E402
from orchestrator.config import DEFAULT_CONFIG  # noqa: E402
from orchestrator.opencode import ProcessResult  # noqa: E402


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=str(cwd), capture_output=True, text=True, check=True
    )


def make_repo(root: Path) -> Path:
    """Create a small git repository with one commit."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    run("git", "init", "-q", cwd=root)
    run("git", "config", "user.email", "test@example.com", cwd=root)
    run("git", "config", "user.name", "Test", cwd=root)
    (root / "README.md").write_text("# test repo\n", encoding="utf-8")
    run("git", "add", "README.md", cwd=root)
    run("git", "commit", "-q", "-m", "init", cwd=root)
    return root


def make_config_dict(repo: Path, state_file: Path, max_fix: int = 3) -> Dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["repo_root"] = str(repo)
    cfg["state_file"] = str(state_file)
    cfg["max_fix_attempts"] = max_fix
    cfg["opencode"] = {
        "command": "opencode",
        "model": "opencode/big-pickle",
        "timeout_seconds": 30,
        "agent": "",
        "extra_args": [],
    }
    return cfg


def write_config_file(path: Path, repo: Path, tasks_dir: Path, runs_dir: Path, state_file: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "repo_root": str(repo),
        "tasks_dir": str(tasks_dir),
        "runs_dir": str(runs_dir),
        "state_file": str(state_file),
        "max_fix_attempts": 3,
        "opencode": {
            "command": "opencode",
            "model": "opencode/big-pickle",
            "timeout_seconds": 30,
            "extra_args": [],
        },
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


class FakeOpenCode:
    """Callable that mimics orchestrator.opencode.run_opencode.

    ``handler`` applies to coder-role calls; ``reviewer`` (optional) applies to
    reviewer-role calls (detected via the agent/model in ``oc_config``). If no
    reviewer handler is given, the fake returns a valid PASS JSON so implement
    workflows exercise the full review loop without a real model.
    """

    def __init__(
        self,
        handler: Optional[Callable[..., ProcessResult]] = None,
        reviewer: Optional[Callable[..., ProcessResult]] = None,
    ):
        self.calls: list = []
        self.handler = handler
        self.reviewer = reviewer

    @staticmethod
    def _role(oc_config) -> str:
        if not isinstance(oc_config, dict):
            return "coder"
        agent = str(oc_config.get("agent") or "")
        if agent == "orchestrator-reviewer":
            return "reviewer"
        model = str(oc_config.get("model") or "")
        if "reviewer" in agent.lower() or "reviewer" in model.lower():
            return "reviewer"
        return "coder"

    def __call__(self, prompt, repo_root, oc_config, timeout=None, cwd=None) -> ProcessResult:
        role = self._role(oc_config)
        self.calls.append(
            {
                "prompt": prompt,
                "repo_root": str(repo_root),
                "timeout": timeout,
                "model": oc_config.get("model") if isinstance(oc_config, dict) else None,
                "agent": oc_config.get("agent") if isinstance(oc_config, dict) else None,
                "role": role,
            }
        )
        if role == "reviewer":
            if self.reviewer:
                return self.reviewer(prompt, repo_root, oc_config, timeout)
            return ProcessResult(
                command="fake-opencode",
                returncode=0,
                stdout=(
                    '{"verdict":"PASS","findings":[]}'
                ),
                stderr="",
            )
        if self.handler:
            return self.handler(prompt, repo_root, oc_config, timeout)
        return ProcessResult(
            command="fake-opencode",
            returncode=0,
            stdout="[fake] plan: touch src/example.ts, then run the configured tests.",
            stderr="",
        )


class FakeCommand:
    """Callable that mimics orchestrator.opencode.run_command."""

    def __init__(self, result_fn: Optional[Callable[..., ProcessResult]] = None):
        self.calls: list = []
        self.result_fn = result_fn or (
            lambda command, cwd, timeout: ProcessResult(
                command=command if isinstance(command, str) else " ".join(command),
                returncode=0,
                stdout="ok",
                stderr="",
            )
        )

    def __call__(self, command, cwd=None, timeout=None) -> ProcessResult:
        self.calls.append({"command": command, "cwd": cwd, "timeout": timeout})
        return self.result_fn(command, cwd, timeout)


def read_only_task(task_id: str = "t-readonly", tests=None) -> Dict[str, Any]:
    return {
        "id": task_id,
        "title": "Read only task",
        "mode": "read_only",
        "goal": "Inspect only.",
        "allowed_paths": [],
        "forbidden_paths": [],
        "tests": tests or [],
    }


def implement_task(task_id: str = "t-impl", tests=None) -> Dict[str, Any]:
    return {
        "id": task_id,
        "title": "Implement task",
        "mode": "implement",
        "goal": "Make a bounded change.",
        "allowed_paths": ["src/**"],
        "forbidden_paths": [],
        "tests": tests or [],
    }


def make_ctx(
    repo: Path,
    run_dir: Path,
    *,
    task: Optional[Dict[str, Any]] = None,
    state: Optional[Dict[str, Any]] = None,
    approved: bool = False,
    dry_run: bool = False,
    opencode: Optional[FakeOpenCode] = None,
    command: Optional[FakeCommand] = None,
    max_fix: int = 3,
    config: Optional[Dict[str, Any]] = None,
    run_id: str = "RUN-TEST-1",
) -> workflow.RunContext:
    cfg = config if config is not None else make_config_dict(repo, run_dir.parent / "state.json", max_fix=max_fix)
    st = state if state is not None else state_mod.default_state()
    t = task if task is not None else read_only_task()
    return workflow.RunContext(
        config=cfg,
        state=st,
        task=t,
        run_id=run_id,
        run_dir=run_dir,
        approved=approved,
        dry_run=dry_run,
        opencode_runner=opencode or FakeOpenCode(),
        command_runner=command or FakeCommand(),
    )
