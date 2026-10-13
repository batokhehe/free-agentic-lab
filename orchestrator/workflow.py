"""Task workflow: audit -> plan -> implement -> test -> review -> bounded fix.

The model is only ever asked to *propose* work; every safety-relevant decision
(scope validation, change attribution, test pass/fail, blocking) is made by this
module using real command output and content hashes.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import gitutil, policy, state as state_mod
from .config import role_agent, role_models
from .opencode import ProcessResult, detect_provider_limit, run_command, run_opencode


class ApprovalRequired(Exception):
    """Raised when a task reaches a human-approval gate."""

    def __init__(self, stage: str, reason: str):
        super().__init__(reason)
        self.stage = stage
        self.reason = reason


class TaskBlocked(Exception):
    """Raised when the task must stop because safety cannot be assured."""

    def __init__(self, stage: str, reason: str):
        super().__init__(reason)
        self.stage = stage
        self.reason = reason


class TaskFailed(Exception):
    """Raised when the task failed after exhausting bounded retries."""

    def __init__(self, stage: str, reason: str):
        super().__init__(reason)
        self.stage = stage
        self.reason = reason


class ProviderLimitReached(Exception):
    """Raised when every configured model for a role hit a provider limit.

    The run is checkpoint-and-paused: nothing is silently retried forever and
    completed stages are preserved for an explicit ``--resume``.
    """

    def __init__(self, stage: str, reason: str, models_tried: List[str]):
        super().__init__(reason)
        self.stage = stage
        self.reason = reason
        self.models_tried = list(models_tried)
        self.model = ", ".join(models_tried)


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #

_COMMON_RULES = """\
Hard rules (enforced by the orchestrator outside this prompt):
- Do not run any destructive or state-changing git command (commit, push, reset,
  checkout, switch, restore, stash, clean, rebase, merge, tag, add/stage).
- Do not delete files. Do not deploy, publish, or start services.
- Do not perform network scanning or touch real pentest targets.
- Do not modify files outside the allowed scope below.
- Do not read or print secrets (.env, keys, credentials).
- If asked, report commands actually run with real output and exit codes.
"""


def _scope_block(task: Dict[str, Any]) -> str:
    allowed = task.get("allowed_paths") or []
    forbidden = task.get("forbidden_paths") or []
    lines = [f"Mode: {task.get('mode')}"]
    lines.append("Allowed paths: " + (", ".join(allowed) if allowed else "(none - read only)"))
    if forbidden:
        lines.append("Forbidden paths: " + ", ".join(forbidden))
    return "\n".join(lines)


def _default_prompt(task: Dict[str, Any], audit_summary: str, stage: str) -> str:
    goal = task.get("goal", "").strip()
    if stage == "analyze":
        return (
            "You are performing a READ-ONLY verification. Do not modify any file.\n\n"
            f"TASK:\n{goal}\n\n"
            f"{_scope_block(task)}\n\n"
            f"AUDIT SNAPSHOT:\n{audit_summary}\n\n"
            "Inspect the repository directly using read-only tools. Produce a "
            "structured report with concrete evidence (file paths, command output, "
            "exit codes). Clearly mark anything you could not verify. Do not claim "
            "a check passed unless you actually ran it and saw its output.\n\n"
            f"{_COMMON_RULES}"
        )
    if stage == "plan":
        return (
            "Produce a short, bounded implementation plan. Do NOT edit files yet.\n\n"
            f"TASK:\n{goal}\n\n"
            f"{_scope_block(task)}\n\n"
            f"AUDIT SNAPSHOT:\n{audit_summary}\n\n"
            "List: files to create/change (within allowed scope), exact commands to "
            f"verify, risks, and rollback notes.\n\n{_COMMON_RULES}"
        )
    if stage == "implement":
        return (
            "Implement the task with the smallest safe change set, following "
            "existing conventions. Only touch allowed paths.\n\n"
            f"TASK:\n{goal}\n\n"
            f"{_scope_block(task)}\n\n"
            f"{_COMMON_RULES}"
        )
    return goal


def _fix_prompt(task: Dict[str, Any], test_report: str) -> str:
    return (
        "A verification command failed. Read the ACTUAL error output below and make "
        "the smallest fix within the allowed scope. Do not change tests to hide "
        "failures. Do not touch files outside scope.\n\n"
        f"TASK:\n{task.get('goal', '').strip()}\n\n"
        f"{_scope_block(task)}\n\n"
        f"FAILED OUTPUT:\n{test_report}\n\n{_COMMON_RULES}"
    )


def _review_prompt(
    task: Dict[str, Any],
    diff_text: str,
    untracked: List[str],
    test_report: str,
    round_no: int,
) -> str:
    return (
        "You are the INDEPENDENT REVIEWER. You are read-only: you MUST NOT edit, "
        "create, delete or move any file, and you MUST NOT run any command that "
        "writes to disk or the network. You only analyze the evidence below.\n\n"
        f"ROUND: {round_no}\n\n"
        f"TASK:\n{task.get('goal', '').strip()}\n\n"
        f"{_scope_block(task)}\n\n"
        "EVIDENCE (attributed by content-hash manifest, not by the coder's claims):\n\n"
        "## Diff\n"
        f"{diff_text if diff_text.strip() else '(no tracked diff; repo may have no commits)'}\n\n"
        "## Untracked changed files\n"
        + ("\n".join(f"- {p}" for p in untracked) if untracked else "- (none)")
        + "\n\n"
        "## Verification results (real, run by the orchestrator)\n"
        f"{test_report}\n\n"
        "Review for correctness, security, and scope. Only review what is actually "
        "present in the evidence; do not invent problems and do not pass anything "
        "you cannot verify from this evidence.\n"
        "Return EXACTLY ONE JSON object, with no surrounding prose, shaped like:\n"
        '{"verdict":"PASS|FAIL|BLOCKED","findings":[{"severity":"blocker|major|minor",'
        '"file":"<repo-relative path>","issue":"<what is wrong>","suggestion":"<fix>"}]}\n'
        "- verdict PASS only if QA evidence passes AND you find no blocker/major issue.\n"
        "- verdict BLOCKED if the evidence is missing, contradictory, or scope was violated.\n"
        "- If QA evidence is missing or failed, that is a FAIL/blocker by itself.\n"
        f"{_COMMON_RULES}"
    )


def _review_fix_prompt(task: Dict[str, Any], findings_report: str, round_no: int) -> str:
    return (
        "An independent reviewer found problems in your previous change. Address "
        "every finding with the smallest safe fix inside the allowed scope. Do not "
        "change tests to hide failures, do not touch files outside scope.\n\n"
        f"TASK:\n{task.get('goal', '').strip()}\n\n"
        f"{_scope_block(task)}\n\n"
        f"FIX ROUND: {round_no}\n\n"
        f"REVIEWER FINDINGS + FAILING QA OUTPUT:\n{findings_report}\n\n{_COMMON_RULES}"
    )


def _parse_review(stdout: str) -> Optional[Dict[str, Any]]:
    """Parse the reviewer's single-JSON-object reply. Fails closed on anything else."""
    if not stdout or not stdout.strip():
        return None
    text = stdout.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else _extract_object(text)
    if not candidate:
        return None
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    verdict = str(data.get("verdict", "")).strip().upper()
    if verdict not in {"PASS", "FAIL", "BLOCKED"}:
        return None
    findings = data.get("findings")
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        return None
    normalized = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        normalized.append(
            {
                "severity": str(f.get("severity") or "minor").lower(),
                "file": str(f.get("file") or ""),
                "issue": str(f.get("issue") or ""),
                "suggestion": str(f.get("suggestion") or ""),
            }
        )
    if verdict == "PASS":
        return {"verdict": verdict, "findings": normalized}
    if not findings or not normalized:
        return None
    return {"verdict": verdict, "findings": normalized}


def _extract_object(text: str) -> Optional[str]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


# --------------------------------------------------------------------------- #
# Run context
# --------------------------------------------------------------------------- #


class RunContext:
    def __init__(
        self,
        *,
        config: Dict[str, Any],
        state: Dict[str, Any],
        task: Dict[str, Any],
        run_id: str,
        run_dir: Path,
        approved: bool = False,
        dry_run: bool = False,
        opencode_runner: Callable[..., ProcessResult] = run_opencode,
        command_runner: Callable[..., ProcessResult] = run_command,
    ) -> None:
        self.config = config
        self.state = state
        self.task = task
        self.run_id = run_id
        self.run_dir = Path(run_dir)
        self.approved = approved
        self.dry_run = dry_run
        self.opencode_runner = opencode_runner
        self.command_runner = command_runner
        self.repo_root = config["repo_root"]

        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.stage_dir = self.run_dir / "stages"
        self.stage_dir.mkdir(parents=True, exist_ok=True)

        self.audit: Dict[str, Any] = {}
        self.baseline_manifest: Dict[str, str] = {}
        self.analysis_text = ""
        self.plan_text = ""
        self.implement_output = ""
        self.test_passed: Optional[bool] = None
        self.test_results: List[Dict[str, Any]] = []
        self.review: Dict[str, Any] = {}
        self.review_verdict: str = ""
        self.final_status = "completed"
        self.last_exit_code: Optional[int] = None
        self.attempts = int(state_mod.get_task_state(state, task["id"]).get("attempts", 0))

    # -- logging / persistence ------------------------------------------------ #

    def log(self, message: str) -> None:
        line = f"[{state_mod.utcnow()}] {policy.redact(message)}"
        with (self.run_dir / "run.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line)

    def write_text(self, name: str, text: str) -> Path:
        path = self.run_dir / name
        path.write_text(policy.redact(text), encoding="utf-8")
        return path

    def write_json(self, name: str, data: Any) -> Path:
        path = self.run_dir / name
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        return path

    def mark_done(self, stage: str, info: Optional[Dict[str, Any]] = None) -> None:
        payload = {"stage": stage, "completed_at": state_mod.utcnow(), "info": info or {}}
        (self.stage_dir / f"{stage}.done").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        # Checkpoint the working tree so a later --resume can detect external
        # drift against the *last completed* state instead of the audit baseline
        # (the baseline legitimately differs once an implement task has run).
        try:
            checkpoint = _snapshot(self)
            (self.run_dir / "manifest-latest.json").write_text(
                json.dumps(checkpoint, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception:
            # A manifest is evidence/telemetry; never let a checkpoint write
            # failure take down the run or leave a half state.
            self.log(f"warning: could not write manifest checkpoint for stage {stage}")

    def is_done(self, stage: str) -> bool:
        return (self.stage_dir / f"{stage}.done").is_file()

    def restore(self) -> None:
        """Reload persisted artifacts so a resumed run does not lose context.

        Without this, resuming after the audit stage would see an empty baseline
        manifest and misclassify every file as newly added.
        """
        audit_path = self.run_dir / "01-audit.json"
        if audit_path.is_file() and not self.audit:
            try:
                self.audit = json.loads(audit_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.audit = {}
        base_path = _manifest_path(self)
        if base_path.is_file() and not self.baseline_manifest:
            try:
                self.baseline_manifest = json.loads(base_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.baseline_manifest = {}
        for filename, attr in (
            ("02-plan.md", "plan_text"),
            ("02-analyze.out.md", "analysis_text"),
            ("04-implement.out.md", "implement_output"),
        ):
            path = self.run_dir / filename
            if path.is_file() and not getattr(self, attr):
                setattr(self, attr, path.read_text(encoding="utf-8"))

    # -- model invocation ----------------------------------------------------- #

    def _effective_opencode_config(
        self, role: str, model: str
    ) -> Dict[str, Any]:
        cfg = copy.deepcopy(self.config["opencode"])
        cfg["model"] = model
        agent = role_agent(self.config, role)
        if agent:
            cfg["agent"] = agent
        return cfg

    def _coder_models(self) -> List[str]:
        return role_models(self.config, "coder")

    def _reviewer_models(self) -> List[str]:
        return role_models(self.config, "reviewer")

    def run_model(
        self,
        prompt: str,
        stage: str,
        role: str = "coder",
        timeout: Optional[float] = None,
    ) -> ProcessResult:
        """Invoke a role model with provider-limit fallback and bounded retries.

        * Coder roles (analyze/plan/implement/fix) try the configured coder
          models primary -> fallbacks.
        * Reviewer role enforces independence: the reviewer model must differ
          from every coder model, else the run is blocked (never silently
          switched to the same model).
        * Provider-limit failures move to the next model after a bounded number
          of per-model retries; if every model is exhausted the run is
          checkpoint-and-paused via :class:`ProviderLimitReached`.
        """
        if self.dry_run:
            return ProcessResult(
                command="<dry-run>",
                returncode=0,
                stdout="[dry-run] model invocation skipped",
                stderr="",
                timed_out=False,
            )
        if role == "reviewer":
            self._assert_reviewer_independent()
            models = self._reviewer_models()
        else:
            models = self._coder_models()
        if not models:
            raise ProviderLimitReached(
                stage,
                f"no models configured for role {role!r}",
                [],
            )

        limits = self.config.get("provider_limits", {})
        transient_retries = int(limits.get("transient_retries", 0))
        backoff = float(limits.get("backoff_seconds", 0.0))
        tried: List[str] = []

        for model in models:
            oc_config = self._effective_opencode_config(role, model)
            for attempt in range(transient_retries + 1):
                self.log(
                    f"stage '{stage}': invoking opencode "
                    f"(role={role} model={model} attempt={attempt + 1})"
                )
                result = self.opencode_runner(
                    prompt,
                    self.repo_root,
                    oc_config,
                    timeout=timeout,
                )
                self.last_exit_code = result.returncode
                state_mod.set_active_exit_code(self.state, self.last_exit_code)
                self.log(
                    f"stage '{stage}': opencode rc={result.returncode} "
                    f"timed_out={result.timed_out} duration={result.duration_seconds:.1f}s"
                )
                if result.ok:
                    return result
                signal = detect_provider_limit(result)
                if signal:
                    tried.append(model)
                    self.log(
                        f"stage '{stage}': model {model} hit provider limit "
                        f"({signal!r}); "
                        + (
                            "retrying"
                            if attempt < transient_retries
                            else "trying next model"
                        )
                    )
                    if attempt < transient_retries:
                        if backoff > 0:
                            time.sleep(backoff)
                        continue
                else:
                    # A genuine non-provider failure (compile error in prompt,
                    # tool failure, etc.). Surface it; no silent model switch.
                    return result
            # All retries for this model exhausted -> move to next model.
            if backoff > 0:
                time.sleep(backoff)

        raise ProviderLimitReached(
            stage,
            f"all models for role {role!r} reached a provider limit: {', '.join(tried)}",
            tried,
        )

    def _assert_reviewer_independent(self) -> None:
        """Fail closed if the reviewer role can use a coder model."""
        coder_models = set(self._coder_models())
        reviewer_models = self._reviewer_models()
        independent = [m for m in reviewer_models if m not in coder_models]
        if not reviewer_models or not independent:
            raise TaskBlocked(
                "review",
                "independent review impossible: reviewer model(s) "
                f"{reviewer_models} overlap coder model(s) {sorted(coder_models)}; "
                "refusing to review with the coder model",
            )


# --------------------------------------------------------------------------- #
# Audit helpers
# --------------------------------------------------------------------------- #


def _manifest_path(ctx: RunContext) -> Path:
    return ctx.run_dir / "manifest-baseline.json"


def _snapshot(ctx: RunContext) -> Dict[str, str]:
    return gitutil.snapshot_manifest(
        ctx.repo_root,
        ignore_dirs=ctx.config.get("ignore_dirs", [".git"]),
        ignore_globs=ctx.config.get("ignore_globs", []),
    )


def _collect_audit(ctx: RunContext) -> Dict[str, Any]:
    root = ctx.repo_root
    status = gitutil.git_status_scoped(root)
    manifest = _snapshot(ctx)
    return {
        "run_id": ctx.run_id,
        "task_id": ctx.task["id"],
        "repo_root": root,
        "git_toplevel": gitutil.git_toplevel(root),
        "branch": gitutil.git_branch(root),
        "head": gitutil.git_head(root),
        "status": status,
        "manifest": manifest,
        "file_count": len(manifest),
        "captured_at": state_mod.utcnow(),
    }


def _audit_summary(audit: Dict[str, Any]) -> str:
    status = audit.get("status", {})
    counts = {k: len(v) for k, v in status.items()}
    return (
        f"branch={audit.get('branch')} head={audit.get('head')} "
        f"git_toplevel={audit.get('git_toplevel')} files={audit.get('file_count')} "
        f"status={json.dumps(counts)}"
    )


def _render_audit_markdown(audit: Dict[str, Any]) -> str:
    status = audit.get("status", {})
    lines = [
        "# Audit Snapshot",
        "",
        f"- run_id: {audit.get('run_id')}",
        f"- task_id: {audit.get('task_id')}",
        f"- repo_root: {audit.get('repo_root')}",
        f"- git_toplevel: {audit.get('git_toplevel')}",
        f"- branch: {audit.get('branch')}",
        f"- head: {audit.get('head')}",
        f"- files (tracked+untracked, hashed): {audit.get('file_count')}",
        f"- captured_at: {audit.get('captured_at')}",
        "",
        "## Pre-existing git status (scoped to repo root)",
        "",
    ]
    for key in ("modified", "deleted", "added", "renamed", "untracked", "other"):
        items = status.get(key) or []
        lines.append(f"### {key} ({len(items)})")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Stage handlers
# --------------------------------------------------------------------------- #


def do_audit(ctx: RunContext) -> None:
    audit = _collect_audit(ctx)
    ctx.audit = audit
    ctx.baseline_manifest = audit["manifest"]
    _manifest_path(ctx).write_text(
        json.dumps(audit["manifest"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    ctx.write_json("01-audit.json", {k: v for k, v in audit.items() if k != "manifest"})
    ctx.write_text("01-audit.md", _render_audit_markdown(audit))
    ctx.log(
        "audit: " + _audit_summary(audit)
    )


def do_analyze(ctx: RunContext) -> None:
    prompt = (ctx.task.get("prompts") or {}).get("analyze") or _default_prompt(
        ctx.task, _audit_summary(ctx.audit), "analyze"
    )
    result = ctx.run_model(prompt, "analyze")
    ctx.write_text("02-analyze.out.md", result.stdout)
    ctx.write_text("02-analyze.err.txt", result.stderr)
    ctx.write_json("02-analyze.meta.json", result.to_dict())
    if not result.ok:
        raise TaskFailed("analyze", _describe_model_failure(result))
    ctx.analysis_text = result.stdout


def do_plan(ctx: RunContext) -> None:
    prompt = (ctx.task.get("prompts") or {}).get("plan") or _default_prompt(
        ctx.task, _audit_summary(ctx.audit), "plan"
    )
    result = ctx.run_model(prompt, "plan")
    ctx.write_text("02-plan.md", result.stdout)
    ctx.write_json("02-plan.meta.json", result.to_dict())
    if not result.ok:
        raise TaskFailed("plan", _describe_model_failure(result))
    ctx.plan_text = result.stdout


def do_validate_plan(ctx: RunContext) -> None:
    problems: List[str] = []
    if len(ctx.plan_text.strip()) < 10:
        problems.append("plan is empty or too short")

    flagged: List[Dict[str, str]] = []
    for line in ctx.plan_text.splitlines():
        stripped = line.strip().lstrip("-*0123456789. )")
        if not stripped:
            continue
        verdict = policy.check_command_safety(stripped)
        if verdict["category"] == "deny":
            flagged.append({"line": line.strip(), "reason": verdict["reason"]})

    verdict_data = {
        "plan_length": len(ctx.plan_text),
        "problems": problems,
        "forbidden_commands_mentioned": flagged,
    }
    ctx.write_json("03-plan-validation.json", verdict_data)
    if problems:
        raise TaskBlocked("validate_plan", "; ".join(problems))
    if flagged:
        # Mentions may be legitimately negative ("do not run git push"), so this is
        # recorded for human review rather than auto-blocked.
        ctx.log(
            f"validate_plan: {len(flagged)} forbidden-command mention(s) recorded for review"
        )


def do_implement(ctx: RunContext) -> None:
    if ctx.task.get("approval_required"):
        granted = state_mod.consume_approval(
            ctx.state, ctx.task["id"], "implement", ctx.run_id
        )
        if not granted:
            raise ApprovalRequired(
                "implement",
                "task requires explicit approval for action 'implement' "
                "before implementation",
            )
    prompt = (ctx.task.get("prompts") or {}).get("implement") or _default_prompt(
        ctx.task, _audit_summary(ctx.audit), "implement"
    )
    if ctx.plan_text:
        prompt = prompt + "\n\nAPPROVED PLAN:\n" + ctx.plan_text
    result = ctx.run_model(prompt, "implement")
    ctx.implement_output = result.stdout
    ctx.write_text("04-implement.out.md", result.stdout)
    ctx.write_text("04-implement.err.txt", result.stderr)
    ctx.write_json("04-implement.meta.json", result.to_dict())
    if not result.ok:
        raise TaskFailed("implement", _describe_model_failure(result))

    # Attribution immediately after implementation.
    after = _snapshot(ctx)
    changes = gitutil.diff_manifests(ctx.baseline_manifest, after)
    classified = _classify(changes, ctx.task, ctx.config)
    ctx.write_json("04-implement-changes.json", {"changes": changes, "classified": classified})
    _enforce_scope(ctx, classified, stage="implement")


def _qa_config_tests(ctx: RunContext) -> List[Dict[str, Any]]:
    """QA commands from the configurable ``roles.qa`` section, as test entries."""
    qa_commands: List[str] = []
    try:
        from .config import qa_commands as qa_helper

        qa_commands = qa_helper(ctx.config)
    except Exception:
        qa_commands = []
    return [
        {"name": f"qa-{index}", "command": command}
        for index, command in enumerate(qa_commands)
        if command
    ]


def _run_tests_once(ctx: RunContext, attempt: Any) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    tests = ctx.task.get("tests") or []
    if not tests:
        tests = _qa_config_tests(ctx)
    if not tests:
        ctx.log("test: no tests configured (nothing to verify)")
        return results
    for index, test in enumerate(tests):
        name = test.get("name") or f"test-{index}"
        command = test["command"]
        raw_cwd = test.get("cwd")
        if raw_cwd:
            cwd = raw_cwd if os.path.isabs(str(raw_cwd)) else os.path.join(ctx.repo_root, str(raw_cwd))
        else:
            cwd = ctx.repo_root
        timeout = float(test.get("timeout_seconds", 600))
        verdict = policy.check_command_safety(
            command if isinstance(command, str) else " ".join(command),
            extra_safe=ctx.config.get("safe_commands"),
        )
        if verdict["category"] == "deny":
            raise TaskBlocked("test", f"configured test {name!r} is forbidden: {verdict['reason']}")
        if verdict["category"] == "approval":
            action = str(verdict.get("action") or "unknown")
            if action not in policy.KNOWN_APPROVAL_ACTIONS:
                raise TaskBlocked(
                    "test",
                    f"configured test {name!r} cannot be classified to a safe action; "
                    "failing closed",
                )
            granted = state_mod.consume_approval(ctx.state, ctx.task["id"], action, ctx.run_id)
            if not granted:
                raise ApprovalRequired(
                    "test",
                    f"test {name!r} requires explicit approval for action {action!r}: "
                    f"{verdict['reason']}",
                )
        ctx.log(f"test attempt {attempt}: running {name}: {command}")
        result = ctx.command_runner(command, cwd=cwd, timeout=timeout)
        ctx.last_exit_code = result.returncode
        state_mod.set_active_exit_code(ctx.state, ctx.last_exit_code)
        record = {"name": name, "cwd": cwd, **result.to_dict()}
        results.append(record)
        ctx.write_json(f"05-test-attempt{attempt}-{index}.json", record)
    return results


def do_test(ctx: RunContext) -> None:
    max_fix = int(ctx.config.get("max_fix_attempts", 3))
    while True:
        results = _run_tests_once(ctx, ctx.attempts)
        ctx.test_results = results
        ctx.test_passed = all(r["returncode"] == 0 and not r["timed_out"] for r in results)
        ctx.write_json(
            f"05-tests-attempt{ctx.attempts}.json",
            {"attempt": ctx.attempts, "passed": ctx.test_passed, "results": results},
        )
        if ctx.test_passed or not results:
            break
        if ctx.task.get("mode") != "implement":
            break
        if ctx.attempts >= max_fix:
            ctx.log(f"test: still failing after {ctx.attempts} fix attempt(s); giving up")
            break

        ctx.attempts += 1
        state_mod.set_task_status(ctx.state, ctx.task["id"], "running", attempts=ctx.attempts)
        state_mod.set_active_attempts(ctx.state, ctx.attempts)
        state_mod.save_state(ctx.config["state_file"], ctx.state)
        ctx.log(f"fix attempt {ctx.attempts}/{max_fix}: invoking opencode")

        report = _format_test_failures(results)
        prompt = (ctx.task.get("prompts") or {}).get("fix") or _fix_prompt(ctx.task, report)
        fix_result = ctx.run_model(prompt, f"fix-{ctx.attempts}")
        ctx.write_text(f"05-fix-attempt{ctx.attempts}.out.md", fix_result.stdout)
        ctx.write_json(f"05-fix-attempt{ctx.attempts}.meta.json", fix_result.to_dict())
        if not fix_result.ok:
            ctx.log(f"fix attempt {ctx.attempts}: opencode failed; stopping fix loop")
            break

        after = _snapshot(ctx)
        changes = gitutil.diff_manifests(ctx.baseline_manifest, after)
        classified = _classify(changes, ctx.task, ctx.config)
        ctx.write_json(f"05-fix-attempt{ctx.attempts}-changes.json", classified)
        _enforce_scope(ctx, classified, stage="fix")

    if not ctx.test_passed and ctx.test_results:
        if ctx.task.get("mode") == "implement":
            raise TaskFailed("test", "configured tests did not pass within the retry limit")
        ctx.log("test: read-only verification commands reported failures; recording (not failing task)")


def do_review(ctx: RunContext) -> None:
    review_ev = _collect_review_evidence(ctx)
    ctx.review = {
        "changes": review_ev["changes"],
        "classified": review_ev["classified"],
        "head_before": ctx.audit.get("head"),
        "head_after": gitutil.git_head(ctx.repo_root),
        "head_changed": gitutil.git_head(ctx.repo_root) != ctx.audit.get("head"),
        "diff_stat": review_ev["diff_stat"],
        "untracked": review_ev["untracked"],
    }
    ctx.write_json("06-review.json", ctx.review)
    ctx.write_text("06-diff.patch", review_ev["diff"])

    if ctx.review["head_changed"]:
        raise TaskBlocked(
            "review",
            f"git HEAD changed during run ({ctx.audit.get('head')} -> "
            f"{ctx.review['head_after']}); refusing to continue",
        )
    _enforce_scope(ctx, review_ev["classified"], stage="review")

    if ctx.dry_run:
        # No real model ran, so no honest independent verdict exists. Record it
        # as explicitly not reviewed instead of fabricating a PASS.
        _finish_review(ctx, "DRY-RUN (not reviewed)", round_no=0, detail="dry-run: no real model invoked")
        return

    if not _review_applicable(ctx):
        # Read-only tasks have no coder diff to independently review; the QA and
        # drift guards above already validate the run as clean.
        _finish_review(ctx, "PASS", round_no=0, detail="no independent review required")
        return

    ctx._assert_reviewer_independent()
    max_rounds = int((ctx.config.get("review") or {}).get("max_rounds", 3))
    final = "BLOCKED"
    final_reason = "independent review could not be completed"
    final_round = 0
    qa_passed = False

    for round_no in range(0, max_rounds + 1):
        final_round = round_no
        qa_passed, qa_report = _qa_state(ctx, round_no)
        ev = _collect_review_evidence(ctx)
        prompt = _review_prompt(
            ctx.task,
            ev["diff"],
            ev["untracked"],
            qa_report,
            round_no,
        )
        before = _snapshot(ctx)
        review_result = ctx.run_model(prompt, f"review-{round_no}", role="reviewer")
        after_review = _snapshot(ctx)
        reviewer_changes = gitutil.diff_manifests(before, after_review)
        reviewer_modified = any(v for v in reviewer_changes.values())
        parsed = _parse_review(review_result.stdout) if review_result.ok else None

        record = {
            "round": round_no,
            "reviewer_ok": review_result.ok,
            "reviewer_rc": review_result.returncode,
            "reviewer_stdout": review_result.stdout,
            "reviewer_stderr": review_result.stderr,
            "qa_passed": qa_passed,
            "parsed_review": parsed,
            "reviewer_workspace_changes": reviewer_changes,
        }
        ctx.write_json(f"06-review-round{round_no}.json", record)
        ctx.write_text(f"06-review-round{round_no}.txt", review_result.stdout or "")
        ctx.write_text(f"06-review-round{round_no}.err.txt", review_result.stderr or "")

        if reviewer_modified:
            final = "BLOCKED"
            final_reason = "reviewer modified the workspace during review (read-only violated): " + _summarize_changes(reviewer_changes)
            break
        if not review_result.ok:
            final = "BLOCKED"
            final_reason = _describe_model_failure(review_result) + " (reviewer)"
            break
        if parsed is None:
            final = "BLOCKED"
            final_reason = (
                "reviewer output could not be parsed as a single JSON object "
                "(fail-closed)"
            )
            break
        findings = parsed["findings"]
        if qa_passed and parsed["verdict"] == "PASS":
            final = "PASS"
            final_reason = "QA passed and independent reviewer approved"
            break
        if round_no == max_rounds:
            if not qa_passed:
                final = "FAIL"
                final_reason = f"QA still failing after {max_rounds} review round(s)"
            elif parsed["verdict"] == "FAIL":
                final = "FAIL"
                final_reason = f"reviewer still reported failures after {max_rounds} round(s)"
            else:
                final = "BLOCKED"
                final_reason = f"reviewer verdict {parsed['verdict']} after {max_rounds} round(s)"
            break

        findings_report = _format_findings_report(findings, qa_report)
        fix_result = ctx.run_model(
            _review_fix_prompt(ctx.task, findings_report, round_no + 1),
            f"review-fix-{round_no + 1}",
            role="coder",
        )
        if not fix_result.ok:
            final = "BLOCKED"
            final_reason = _describe_model_failure(fix_result) + " (review fix)"
            break
        after = _snapshot(ctx)
        changes = gitutil.diff_manifests(ctx.baseline_manifest, after)
        classified = _classify(changes, ctx.task, ctx.config)
        ctx.write_json(f"06-review-fix-{round_no + 1}-changes.json", classified)
        _enforce_scope(ctx, classified, stage=f"review-fix-{round_no + 1}")

    _finish_review(ctx, final, round_no=final_round, detail=final_reason)
    if final == "PASS":
        return
    if final == "FAIL":
        raise TaskFailed("review", final_reason)
    raise TaskBlocked("review", final_reason)


def _review_applicable(ctx: RunContext) -> bool:
    review = ctx.config.get("review") or {}
    return ctx.task.get("mode") == "implement" and bool(review.get("enabled", True))


def _collect_review_evidence(ctx: RunContext) -> Dict[str, Any]:
    after = _snapshot(ctx)
    changes = gitutil.diff_manifests(ctx.baseline_manifest, after)
    classified = _classify(changes, ctx.task, ctx.config)
    rc_stat, diff_stat, _ = gitutil.run_git(ctx.repo_root, ["diff", "--stat"])
    rc_diff, diff_text, _ = gitutil.run_git(ctx.repo_root, ["diff"])
    untracked = gitutil.git_status_scoped(ctx.repo_root).get("untracked", [])
    return {
        "changes": changes,
        "classified": classified,
        "diff_stat": diff_stat if rc_stat == 0 else "",
        "diff": diff_text if rc_diff == 0 else "",
        "untracked": untracked,
    }


def _qa_state(ctx: RunContext, round_no: int):
    """Return (passed, report). Round 0 reuses the tests already run by do_test."""
    if round_no == 0 and ctx.test_results:
        passed = all(
            r["returncode"] == 0 and not r["timed_out"] for r in ctx.test_results
        )
        return passed, _format_qa_report(ctx.test_results)
    results = _run_tests_once(ctx, f"r{round_no}")
    ctx.test_results = results
    passed = all(r["returncode"] == 0 and not r["timed_out"] for r in results)
    ctx.write_json(
        f"05-tests-review-r{round_no}.json",
        {"round": round_no, "passed": passed, "results": results},
    )
    ctx.log(f"review round {round_no}: QA passed={passed}")
    return passed, _format_qa_report(results)


def _summarize_changes(changes: Dict[str, Any]) -> str:
    parts = []
    for kind, items in changes.items():
        if items:
            parts.append(f"{kind}={', '.join(items)}")
    return "; ".join(parts) if parts else "(unchanged)"


def _format_qa_report(results: List[Dict[str, Any]]) -> str:
    if not results:
        return "(no QA commands configured or run)"
    lines = []
    for record in results:
        passed = record["returncode"] == 0 and not record["timed_out"]
        lines.append(
            f"[{'PASS' if passed else 'FAIL'}] $ {record['command']} "
            f"exit={record['returncode']} timed_out={record['timed_out']}"
        )
        if not passed:
            lines.append(f"--- stdout ---\n{record['stdout']}\n--- stderr ---\n{record['stderr']}")
    return "\n".join(lines)


def _format_findings_report(findings: List[Dict[str, Any]], qa_report: str) -> str:
    parts = []
    for f in findings:
        parts.append(
            f"- [{f.get('severity')}] {f.get('file')}: {f.get('issue')}\n"
            f"    suggestion: {f.get('suggestion')}"
        )
    return "FINDINGS:\n" + "\n".join(parts) + "\n\nFAILING QA OUTPUT:\n" + qa_report


def _finish_review(
    ctx: RunContext,
    verdict: str,
    round_no: int,
    detail: str,
) -> None:
    ctx.review_verdict = verdict
    data = {
        "verdict": verdict,
        "round": round_no,
        "detail": detail,
        "qa_passed": bool(ctx.test_results)
        and all(
            r["returncode"] == 0 and not r["timed_out"] for r in (ctx.test_results or [])
        ),
        "coder_models": ctx._coder_models(),
        "reviewer_models": ctx._reviewer_models(),
        "generated_at": state_mod.utcnow(),
    }
    ctx.write_json("07-review-verdict.json", data)
    ctx.log(f"review: verdict={verdict} after round {round_no}: {detail}")


def do_report(ctx: RunContext) -> None:
    report = _build_report(ctx)
    ctx.write_text("report.md", report)
    ctx.write_json(
        "evidence-index.json",
        {
            "run_id": ctx.run_id,
            "task_id": ctx.task["id"],
            "status": ctx.final_status,
            "files": sorted(p.name for p in ctx.run_dir.iterdir() if p.is_file()),
            "generated_at": state_mod.utcnow(),
        },
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _describe_model_failure(result: ProcessResult) -> str:
    if result.error:
        return result.error
    if result.timed_out:
        return "opencode timed out"
    return f"opencode exited with code {result.returncode}"


def _format_test_failures(results: List[Dict[str, Any]]) -> str:
    parts = []
    for record in results:
        if record["returncode"] != 0 or record["timed_out"]:
            parts.append(
                f"$ {record['command']}\nexit={record['returncode']} "
                f"timed_out={record['timed_out']}\n"
                f"--- stdout ---\n{record['stdout']}\n--- stderr ---\n{record['stderr']}"
            )
    return "\n\n".join(parts) or "(no output captured)"


def _classify(
    changes: Dict[str, List[str]],
    task: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Dict[str, str]]:
    allowed = list(task.get("allowed_paths") or [])
    forbidden = list(config.get("protected_paths") or []) + list(task.get("forbidden_paths") or [])
    classified: Dict[str, Dict[str, str]] = {}
    for kind in ("added", "removed", "modified"):
        for rel in changes.get(kind, []):
            verdict = policy.classify_change(rel, allowed, forbidden)
            classified[rel] = {"change": kind, "verdict": verdict}
    return classified


def _enforce_scope(ctx: RunContext, classified: Dict[str, Dict[str, str]], stage: str) -> None:
    violations = {
        rel: info for rel, info in classified.items() if info["verdict"] in {"protected", "out-of-scope"}
    }
    if violations:
        ctx.write_json(f"scope-violations-{stage}.json", violations)
        # Record the violation in state before stopping.
        raise TaskBlocked(
            stage,
            "changes detected outside allowed scope: "
            + ", ".join(f"{rel} ({info['verdict']})" for rel, info in violations.items()),
        )


def _describe_changes(changes: Dict[str, List[str]]) -> str:
    parts = []
    for kind in ("added", "modified", "removed"):
        if changes.get(kind):
            parts.append(f"{kind}={', '.join(changes[kind])}")
    return "; ".join(parts)


def _guard_after_stage(ctx: RunContext, stage: str) -> None:
    """Fail-closed drift check at every stage boundary.

    A manifest is captured immediately after the stage (the baseline was
    captured immediately before the first read-only stage by ``do_audit``) and
    compared before anything else runs. Any unexpected change stops the run:

    * read-only tasks: ANY change (created/modified/deleted/renamed) blocks.
    * implement tasks: only changes classified ``allowed`` inside the task
      scope may remain; protected/out-of-scope changes block.

    The comparison deliberately includes untracked files (the manifest hashes
    every file, so a clean ``git diff`` proves nothing on an unborn branch).
    Nothing is restored or deleted here.
    """
    if stage in ("audit", "report"):
        return
    after = _snapshot(ctx)
    changes = gitutil.diff_manifests(ctx.baseline_manifest, after)
    classified = _classify(changes, ctx.task, ctx.config)
    head_now = gitutil.git_head(ctx.repo_root)
    head_before = ctx.audit.get("head")
    head_changed = head_now != head_before

    record = {
        "after_stage": stage,
        "captured_at": state_mod.utcnow(),
        "head_before": head_before,
        "head_after": head_now,
        "head_changed": head_changed,
        "last_exit_code": ctx.last_exit_code,
        "changes": changes,
        "classified": classified,
    }
    ctx.write_json(f"drift-{stage}.json", record)

    violations = {
        rel: info for rel, info in classified.items() if info["verdict"] in {"protected", "out-of-scope"}
    }
    if ctx.task.get("mode") == "read_only" and any(changes[k] for k in ("added", "removed", "modified")):
        raise TaskBlocked(
            stage,
            f"read-only stage '{stage}' changed the repository (fail-closed): "
            + _describe_changes(changes),
        )
    if violations:
        raise TaskBlocked(
            stage,
            "changes detected outside allowed scope: "
            + ", ".join(f"{rel} ({info['verdict']})" for rel, info in violations.items()),
        )
    if head_changed:
        raise TaskBlocked(
            stage,
            f"git HEAD changed during run ({head_before} -> {head_now}); refusing to continue",
        )


def _build_report(ctx: RunContext) -> str:
    task = ctx.task
    lines = [
        f"# Task Report — {task.get('title', task['id'])}",
        "",
        f"- task_id: {task['id']}",
        f"- run_id: {ctx.run_id}",
        f"- mode: {task.get('mode')}",
        f"- status: {ctx.final_status}",
        f"- repo_root: {ctx.repo_root}",
        f"- fix_attempts_used: {ctx.attempts}",
        f"- generated_at: {state_mod.utcnow()}",
        "",
        "## Goal",
        "",
        task.get("goal", "").strip(),
        "",
        "## Audit",
        "",
        _audit_summary(ctx.audit),
        "",
    ]
    if ctx.plan_text:
        lines += ["## Plan", "", ctx.plan_text.strip(), ""]
    if ctx.analysis_text:
        lines += ["## Analysis", "", ctx.analysis_text.strip(), ""]
    if ctx.implement_output:
        lines += ["## Implementation output", "", ctx.implement_output.strip(), ""]

    lines += ["## Changed files (attributed by SHA-256 manifest)", ""]
    if ctx.review.get("classified"):
        for rel, info in sorted(ctx.review["classified"].items()):
            lines.append(f"- [{info['verdict']}] {info['change']}: {rel}")
    else:
        lines.append("- (no file content changes detected during this run)")
    lines.append("")

    lines += ["## Independent review verdict", ""]
    verdict = getattr(ctx, "review_verdict", None)
    if verdict:
        lines.append(f"- verdict: {verdict}")
        verdict_path = ctx.run_dir / "07-review-verdict.json"
        if verdict_path.is_file():
            try:
                data = json.loads(verdict_path.read_text(encoding="utf-8"))
                lines.append(f"- detail: {data.get('detail', '')}")
                lines.append(f"- reviewer model(s): {', '.join(data.get('reviewer_models') or [])}")
                lines.append(f"- coder model(s): {', '.join(data.get('coder_models') or [])}")
            except (OSError, json.JSONDecodeError):
                pass
    else:
        lines.append("- (not applicable)")
    lines.append("")

    lines += ["## Tests", ""]
    if ctx.test_results:
        for record in ctx.test_results:
            lines.append(
                f"- {record['name']}: exit={record['returncode']} "
                f"timed_out={record['timed_out']} ({record['duration_seconds']}s)"
            )
        lines.append("")
        lines.append("```")
        lines.append(_format_test_failures(ctx.test_results) if not ctx.test_passed else "(all passed)")
        lines.append("```")
    else:
        lines.append("- (no tests configured)")
    lines.append("")

    lines += [
        "## Evidence files",
        "",
    ]
    for path in sorted(ctx.run_dir.rglob("*")):
        if path.is_file():
            lines.append(f"- {path.relative_to(ctx.run_dir).as_posix()}")
    lines.append("")
    lines += [
        "## Safety",
        "",
        "- No git commit/push/reset/stash/clean/checkout was executed by the orchestrator.",
        "- Changes are attributed by content hash, not by trusting the model.",
        "- Model-reported test results are not used as evidence; tests are run by the orchestrator.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

STAGES_READ_ONLY = ["audit", "analyze", "test", "review", "report"]
STAGES_IMPLEMENT = ["audit", "plan", "validate_plan", "implement", "test", "review", "report"]

_HANDLERS: Dict[str, Callable[[RunContext], None]] = {
    "audit": do_audit,
    "analyze": do_analyze,
    "plan": do_plan,
    "validate_plan": do_validate_plan,
    "implement": do_implement,
    "test": do_test,
    "review": do_review,
    "report": do_report,
}


def stages_for_task(task: Dict[str, Any]) -> List[str]:
    return STAGES_IMPLEMENT if task.get("mode") == "implement" else STAGES_READ_ONLY


def run_task(ctx: RunContext) -> str:
    """Execute (or resume) a task. Returns the final status string.

    Raises ApprovalRequired / TaskBlocked / TaskFailed for the runner to record.
    """
    ctx.restore()
    if ctx.state.get("active_run") is None:
        state_mod.begin_run(ctx.state, ctx.run_id, ctx.task["id"], stage="audit")
        state_mod.save_state(ctx.config["state_file"], ctx.state)
    stages = stages_for_task(ctx.task)
    for stage in stages:
        if ctx.is_done(stage):
            ctx.log(f"resume: stage '{stage}' already complete; skipping")
            continue
        state_mod.update_stage(ctx.state, stage)
        state_mod.save_state(ctx.config["state_file"], ctx.state)
        ctx.log(f"== stage '{stage}' ==")
        try:
            _HANDLERS[stage](ctx)
            # Fail-closed drift check immediately after the stage, before any
            # subsequent stage or command runs.
            _guard_after_stage(ctx, stage)
        except ApprovalRequired:
            state_mod.update_stage(ctx.state, stage)
            state_mod.save_state(ctx.config["state_file"], ctx.state)
            raise
        except ProviderLimitReached as exc:
            ctx.final_status = "paused"
            state_mod.update_stage(ctx.state, stage)
            state_mod.mark_paused(
                ctx.state, exc.reason, exc.models_tried, stage=stage
            )
            ctx.write_json(
                "terminal.json",
                {
                    "stage": stage,
                    "status": "paused",
                    "reason": exc.reason,
                    "models_tried": exc.models_tried,
                },
            )
            state_mod.save_state(ctx.config["state_file"], ctx.state)
            raise
        except TaskBlocked:
            ctx.final_status = "blocked"
            ctx.write_json("terminal.json", {"stage": stage, "status": "blocked"})
            state_mod.save_state(ctx.config["state_file"], ctx.state)
            raise
        except TaskFailed:
            ctx.final_status = "failed"
            ctx.write_json("terminal.json", {"stage": stage, "status": "failed"})
            state_mod.save_state(ctx.config["state_file"], ctx.state)
            raise
        ctx.mark_done(stage)
        state_mod.save_state(ctx.config["state_file"], ctx.state)

    # If we reached here, everything completed. Produce the report if it was
    # skipped by resume logic but missing on disk.
    if not (ctx.run_dir / "report.md").is_file():
        ctx.final_status = "completed"
        do_report(ctx)
    ctx.final_status = "completed"
    return ctx.final_status
