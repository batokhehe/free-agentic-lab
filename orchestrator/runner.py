"""Command-line entry point and task-queue loop for the Sentinel orchestrator.

Usage (Windows PowerShell):
    python -m orchestrator.runner --list
    python -m orchestrator.runner --status
    python -m orchestrator.runner --once
    python -m orchestrator.runner --task sentinel-stage0-verification
    python -m orchestrator.runner --task some-impl-task --approve implement
    python -m orchestrator.runner --resume                    # explicit recovery
    python -m orchestrator.runner --task <done-task> --force  # re-run a completed task

Exit codes: 0 ok/queue empty, 1 a task failed, 2 stopped at an approval,
blocked or recovery gate, 3 configuration/state/usage error, 4 paused at a
provider limit (resume with --resume).
"""

from __future__ import annotations

import argparse
import json
import random
import string
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import gitutil, policy, state as state_mod, workflow
from .config import ConfigError, load_config
from .tasks import TaskError, discover_tasks, find_task

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_GATE = 2
EXIT_SETUP_ERROR = 3
EXIT_PAUSED = 4

DEFAULT_CONFIG = "orchestrator.config.json"


def _new_run_id(task_id: str) -> str:
    stamp = state_mod.utcnow().replace(":", "").replace("-", "").replace("+0000", "Z")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    safe_task = "".join(c if c.isalnum() or c in "-_" else "-" for c in task_id)
    return f"{stamp}-{safe_task}-{suffix}"


def _build_context(
    config: Dict[str, Any],
    state: Dict[str, Any],
    task: Dict[str, Any],
    run_id: str,
    dry_run: bool,
) -> workflow.RunContext:
    return workflow.RunContext(
        config=config,
        state=state,
        task=task,
        run_id=run_id,
        run_dir=Path(config["runs_dir"]) / run_id,
        dry_run=dry_run,
    )


def _task_allowed(task: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
    return policy.validate_task(task, config)


def cmd_list(config: Dict[str, Any], state: Dict[str, Any]) -> int:
    tasks = discover_tasks(config["tasks_dir"])
    if not tasks:
        print(f"No tasks found under {config['tasks_dir']}")
        return EXIT_OK
    print(f"{'TASK ID':40} {'MODE':12} {'PRIO':>4}  STATUS")
    for task in tasks:
        entry = state_mod.get_task_state(state, task["id"])
        print(
            f"{task['id']:40} {task.get('mode',''):12} {int(task.get('priority',0)):>4}  "
            f"{entry.get('status','pending')}"
        )
    return EXIT_OK


def cmd_status(config: Dict[str, Any], state: Dict[str, Any]) -> int:
    print(json.dumps(state, indent=2, ensure_ascii=False, default=str))
    return EXIT_OK


def _next_pending(
    tasks: List[Dict[str, Any]], state: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    for task in tasks:
        entry = state_mod.get_task_state(state, task["id"])
        if entry.get("status") in (None, "pending"):
            return task
    return None


def _record_approvals(
    state: Dict[str, Any],
    task_id: str,
    run_id: str,
    actions: List[str],
) -> None:
    for action in actions:
        state_mod.record_approval(state, task_id, action, run_id)


def _resume_manifest_check(config: Dict[str, Any], active: Dict[str, Any]):
    """Compare a fresh manifest with the last known-good checkpoint.

    Returns (ok, reason). For interruption *before* any change, the baseline is
    used; for a paused implement run, the last per-stage checkpoint ends up being
    compared so that already-attributed allowed changes do not count as drift. If
    the working tree drifted since the last checkpoint, resuming would
    misattribute changes, so the run is marked needs_review and the caller stops.
    """
    run_dir = Path(config["runs_dir"]) / active["run_id"]
    base_path = run_dir / "manifest-baseline.json"
    latest_path = run_dir / "manifest-latest.json"
    checkpoint = latest_path if latest_path.is_file() else base_path
    if not checkpoint.is_file():
        return False, "run manifest checkpoint is missing; recovery review required"
    fresh = gitutil.snapshot_manifest(
        config["repo_root"],
        ignore_dirs=config.get("ignore_dirs", [".git"]),
        ignore_globs=config.get("ignore_globs", []),
    )
    try:
        stored = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "run manifest checkpoint is unreadable; recovery review required"
    if fresh != stored:
        drifted = gitutil.diff_manifests(stored, fresh)
        reason = (
            "workspace drifted since the interruption ("
            + "; ".join(
                f"{kind}={', '.join(v)}" for kind, v in drifted.items() if v
            )
            + "); recovery review required before resuming"
        )
        return False, reason
    return True, ""


def _run_one(
    config: Dict[str, Any],
    state: Dict[str, Any],
    task: Dict[str, Any],
    run_id: str,
    dry_run: bool,
) -> str:
    errors = _task_allowed(task, config)
    if errors:
        raise TaskError("task failed policy validation: " + "; ".join(errors))

    if state.get("active_run") is None:
        state_mod.begin_run(state, run_id, task["id"], stage="audit")
    state_mod.save_state(config["state_file"], state)

    ctx = _build_context(config, state, task, run_id, dry_run)
    ctx.log(f"run started: task={task['id']} run_id={run_id} mode={task.get('mode')}")

    try:
        status = workflow.run_task(ctx)
    except workflow.ApprovalRequired as exc:
        state_mod.set_task_status(state, task["id"], "awaiting_approval", run_id=run_id)
        state_mod.update_stage(state, exc.stage)
        state_mod.save_state(config["state_file"], state)
        ctx.log(f"APPROVAL GATE at stage '{exc.stage}': {exc.reason}")
        print(f"Awaiting approval: task={task['id']} stage={exc.stage} reason={exc.reason}")
        return "awaiting_approval"
    except workflow.ProviderLimitReached as exc:
        state_mod.save_state(config["state_file"], state)
        ctx.log(f"PAUSED at stage '{exc.stage}': {exc.reason}")
        print(
            f"Paused: task={task['id']} stage={exc.stage} reason={exc.reason}; "
            f"resume later with --resume"
        )
        return "paused"
    except workflow.TaskBlocked as exc:
        state_mod.finish_run(state, "blocked", note=exc.reason)
        state_mod.save_state(config["state_file"], state)
        ctx.log(f"BLOCKED at stage '{exc.stage}': {exc.reason}")
        print(f"Blocked: task={task['id']} stage={exc.stage} reason={exc.reason}")
        return "blocked"
    except workflow.TaskFailed as exc:
        state_mod.finish_run(state, "failed", note=exc.reason)
        state_mod.save_state(config["state_file"], state)
        ctx.log(f"FAILED at stage '{exc.stage}': {exc.reason}")
        print(f"Failed: task={task['id']} stage={exc.stage} reason={exc.reason}")
        return "failed"

    state_mod.finish_run(state, status)
    state_mod.save_state(config["state_file"], state)
    ctx.log(f"run finished: status={status}")
    print(f"Completed: task={task['id']} status={status} run_id={run_id}")
    return status


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orchestrator.runner",
        description="Sentinel auto-agentic orchestrator (hardened).",
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="path to config JSON")
    parser.add_argument("--repo", default=None, help="override repo_root")
    parser.add_argument("--task", default=None, help="run a specific task id")
    parser.add_argument("--once", action="store_true", help="run at most one task")
    parser.add_argument("--list", action="store_true", help="list tasks and statuses")
    parser.add_argument("--status", action="store_true", help="print orchestrator state")
    parser.add_argument(
        "--approve",
        action="append",
        default=[],
        metavar="ACTION",
        help="record an explicit approval (e.g. implement, dependency-install); "
        "consumed once and scoped to this run",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="explicitly resume an interrupted run (recovery review decision)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow re-running a task whose last result was completed",
    )
    parser.add_argument("--dry-run", action="store_true", help="skip model invocations")
    parser.add_argument(
        "--max-fix-attempts", type=int, default=None, help="override max fix attempts"
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR

    if args.repo:
        config["repo_root"] = str(Path(args.repo).resolve())

    boundary_problems = policy.validate_repo_boundary(config)
    if boundary_problems:
        for problem in boundary_problems:
            print(f"config error: {problem}", file=sys.stderr)
        return EXIT_SETUP_ERROR

    unknown_actions = [a for a in args.approve if a not in policy.KNOWN_APPROVAL_ACTIONS]
    if unknown_actions:
        print(
            f"usage error: unknown approval action(s): {', '.join(unknown_actions)}; "
            f"known actions: {', '.join(sorted(policy.KNOWN_APPROVAL_ACTIONS))}",
            file=sys.stderr,
        )
        return EXIT_SETUP_ERROR
    if args.approve and not args.task:
        print("usage error: --approve requires --task", file=sys.stderr)
        return EXIT_SETUP_ERROR

    try:
        state = state_mod.load_state(config["state_file"])
    except state_mod.StateError as exc:
        print(f"state error: {exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR

    if args.list:
        return cmd_list(config, state)
    if args.status:
        return cmd_status(config, state)

    # Only a running instance may mutate queue state.
    lock_path = str(Path(config["state_file"]).parent / ".orchestrator.lock")
    if not state_mod.acquire_lock(lock_path):
        print(
            "another runner instance is already processing the queue; refusing to run",
            file=sys.stderr,
        )
        return EXIT_SETUP_ERROR
    try:
        return _run_selected(config, state, args)
    finally:
        state_mod.release_lock(lock_path)


def _run_selected(config: Dict[str, Any], state: Dict[str, Any], args: argparse.Namespace) -> int:
    try:
        tasks = discover_tasks(config["tasks_dir"])
    except TaskError as exc:
        print(f"task error: {exc}", file=sys.stderr)
        return EXIT_SETUP_ERROR

    if not tasks:
        print(f"No tasks found under {config['tasks_dir']}")
        return EXIT_OK

    if args.max_fix_attempts is not None:
        config["max_fix_attempts"] = args.max_fix_attempts

    active = state.get("active_run")

    # ---- explicit task targeting ---------------------------------------- #
    if args.task:
        task = find_task(config["tasks_dir"], args.task)
        if task is None:
            print(f"task error: task {args.task!r} not found", file=sys.stderr)
            return EXIT_SETUP_ERROR
        entry = state_mod.get_task_state(state, task["id"])
        if active and active["task_id"] != args.task:
            print(
                f"state error: run {active['run_id']} is active for task "
                f"{active['task_id']!r}; resolve it (--resume) or finish it first",
                file=sys.stderr,
            )
            return EXIT_SETUP_ERROR
        if active:
            if not args.resume:
                run = state_mod.get_active_summary(state)
                print(
                    f"state error: interrupted run {run['run_id']} for task "
                    f"{run['task_id']!r} requires an explicit recovery decision "
                    f"(--resume); not resuming automatically",
                    file=sys.stderr,
                )
                return EXIT_SETUP_ERROR
            ok, reason = _resume_manifest_check(config, active)
            if not ok:
                state_mod.mark_needs_review(state, reason)
                state_mod.save_state(config["state_file"], state)
                print(
                    f"recovery required: {reason}. Run marked needs_review. "
                    "Review the working tree before resuming.",
                    file=sys.stderr,
                )
                return EXIT_GATE
            _record_approvals(state, task["id"], active["run_id"], args.approve)
            result = _run_one(config, state, task, active["run_id"], args.dry_run)
            return _post_process_result(result, args, config)
        if entry.get("status") == "completed" and not args.force:
            print(
                f"task error: task {args.task!r} already completed; "
                "use --force to run it again",
                file=sys.stderr,
            )
            return EXIT_SETUP_ERROR

        run_id = _new_run_id(task["id"])
        _record_approvals(state, task["id"], run_id, args.approve)
        result = _run_one(config, state, task, run_id, args.dry_run)
        return _post_process_result(result, args, config)

    # ---- queued run: resume active work first, then drain the queue -------- #
    if active:
        if not args.resume:
            run = state_mod.get_active_summary(state)
            print(
                f"state error: interrupted run {run['run_id']} for task "
                f"{run['task_id']!r} requires an explicit recovery decision "
                f"(--resume); not resuming automatically",
                file=sys.stderr,
            )
            return EXIT_SETUP_ERROR
        task = find_task(config["tasks_dir"], active["task_id"])
        if task is None:
            print(
                f"state error: active run references unknown task {active['task_id']!r}",
                file=sys.stderr,
            )
            return EXIT_SETUP_ERROR
        ok, reason = _resume_manifest_check(config, active)
        if not ok:
            state_mod.mark_needs_review(state, reason)
            state_mod.save_state(config["state_file"], state)
            print(
                f"recovery required: {reason}. Run marked needs_review. "
                "Review the working tree before resuming.",
                file=sys.stderr,
            )
            return EXIT_GATE
        _record_approvals(state, task["id"], active["run_id"], args.approve)
        result = _run_one(config, state, task, active["run_id"], args.dry_run)
        code = _post_process_result(result, args, config)
        if args.once or args.task:
            return code

    exit_code = EXIT_OK
    processed = 0
    while True:
        task = _next_pending(tasks, state)
        if task is None:
            print("Queue empty.")
            break

        run_id = _new_run_id(task["id"])
        _record_approvals(state, task["id"], run_id, args.approve)
        result = _run_one(config, state, task, run_id, args.dry_run)
        processed += 1

        if result == "awaiting_approval":
            return EXIT_GATE
        if result == "paused":
            return EXIT_PAUSED
        if result == "failed":
            exit_code = EXIT_FAILED
            if args.once:
                break
            continue
        if result == "blocked":
            exit_code = EXIT_FAILED
            if config.get("queue_stop_on_blocked", True) or args.once:
                return EXIT_GATE

        if args.once:
            break

    if processed == 0 and not active:
        print("Nothing to do.")
    return exit_code


def _post_process_result(result: str, args: argparse.Namespace, config: Dict[str, Any]) -> int:
    if result == "awaiting_approval":
        return EXIT_GATE
    if result == "paused":
        return EXIT_PAUSED
    if result == "failed":
        return EXIT_FAILED
    if result == "blocked" and config.get("queue_stop_on_blocked", True):
        return EXIT_GATE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())