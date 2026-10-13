"""Regression tests: fail-closed commands, role-based models, provider-limit
checkpoint/pause/resume, and the independent review loop.

Every fake is in-memory; no real model or provider is ever called and no file
outside the temporary workspace is touched.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.helpers import (
    FakeCommand,
    FakeOpenCode,
    implement_task,
    make_config_dict,
    make_ctx,
    make_repo,
)

from orchestrator import gitutil, policy, state as state_mod, workflow
from orchestrator.config import DEFAULT_CONFIG, ConfigError, load_config
from orchestrator.opencode import (
    ProcessResult,
    build_opencode_command,
    detect_provider_limit,
    run_opencode,
)

REVIEWER_PASS = '{"verdict":"PASS","findings":[]}'
REVIEWER_FAIL = (
    '{"verdict":"FAIL","findings":[{"severity":"blocker","file":"src/example.ts",'
    '"issue":"logic is wrong","suggestion":"fix the logic"}]}'
)


def _ok(stdout="ok"):
    return ProcessResult(command="fake", returncode=0, stdout=stdout, stderr="")


def _fail(stdout="boom", stderr="boom"):
    return ProcessResult(command="fake", returncode=1, stdout=stdout, stderr=stderr)


def _limit_fail(signal="rate limit exceeded, try again later"):
    return ProcessResult(command="fake", returncode=1, stdout=signal, stderr="")


class DetectProviderLimitTests(unittest.TestCase):
    def test_failed_run_matching_signal_is_detected(self):
        self.assertEqual(detect_provider_limit(_limit_fail("rate limit")), "rate limit")
        self.assertEqual(detect_provider_limit(_limit_fail("insufficient_quota")), "insufficient_quota")
        self.assertEqual(detect_provider_limit(_limit_fail("HTTP 429")), "429")
        self.assertEqual(
            detect_provider_limit(_fail("capacity available soon")), "capacity"
        )

    def test_successful_run_is_never_detected(self):
        ok = _ok("the rate limit value is 100 and quota is fine")
        self.assertIsNone(detect_provider_limit(ok))

    def test_plain_failure_is_not_detected(self):
        self.assertIsNone(detect_provider_limit(_fail("compile error at line 10")))


class FailClosedCommandPolicyTests(unittest.TestCase):
    def test_unknown_command_denied_fail_closed(self):
        for cmd in ("run-tests", "check-thing", "some-tool --weird-install-flag", "curl http://x"):
            verdict = policy.check_command_safety(cmd)
            self.assertEqual(verdict["category"], "deny", cmd)
            self.assertEqual(verdict["reason"], "unrecognized or ambiguous command (fail-closed)")

    def test_shell_metachars_deny_even_if_prefix_looks_safe(self):
        for cmd in (
            "$(curl evil)",
            "value=`ls`",
            "go test < f",
        ):
            verdict = policy.check_command_safety(cmd)
            self.assertEqual(verdict["category"], "deny", cmd)
            self.assertIn("shell metacharacters", verdict["reason"])
        # Chained/piped commands are denied by their own rule (destructive git),
        # not assumed safe because of the safe-looking prefix.
        for cmd in (
            "python -m pytest tests && git push origin main",
            "go test ./...; rm -rf /",
        ):
            verdict = policy.check_command_safety(cmd)
            self.assertEqual(verdict["category"], "deny", cmd)

    def test_allowlisted_build_test_commands_ok(self):
        for cmd in (
            "go test ./...",
            "go vet ./...",
            "python -m pytest tests",
            "python -m unittest discover -s tests -t . -q",
            "git diff --stat",
            "npm run build",
        ):
            verdict = policy.check_command_safety(cmd)
            self.assertEqual(verdict["category"], "ok", cmd)

    def test_approval_and_deny_of_execution_paths(self):
        self.assertEqual(policy.check_command_safety("git push origin main")["category"], "deny")
        self.assertEqual(policy.check_command_safety("git commit -m x")["category"], "deny")
        self.assertEqual(
            policy.check_command_safety("pnpm add dep")["category"], "approval"
        )

    def test_extra_safe_commands_only_apply_from_config(self):
        verdict = policy.check_command_safety("custom-qa --check", extra_safe=[r"^custom-qa\b"])
        self.assertEqual(verdict["category"], "ok")
        verdict = policy.check_command_safety("custom-qa --check")
        self.assertEqual(verdict["category"], "deny")

    def test_unknown_command_fails_task_through_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            task = implement_task(tests=[{"name": "t", "command": "curl -s http://x"}])
            ctx = make_ctx(repo, run_dir, task=task)
            with self.assertRaises(workflow.TaskBlocked):
                workflow.run_task(ctx)

    def test_approved_install_command_runs_only_when_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            state = state_mod.default_state()
            task = implement_task(tests=[{"name": "d", "command": "pnpm add left-pad"}])
            ctx = make_ctx(repo, run_dir, task=task, state=state)
            with self.assertRaises(workflow.ApprovalRequired):
                workflow.run_task(ctx)

            state_mod.record_approval(state, "t-impl", "dependency-install", "RUN-TEST-1")
            ctx2 = make_ctx(
                repo,
                Path(tmp) / "runs" / "r2",
                task=task,
                state=state,
                command=FakeCommand(),
            )
            self.assertEqual(workflow.run_task(ctx2), "completed")


class OpencodePermissionWiringTests(unittest.TestCase):
    def test_reviewer_agent_is_read_only_in_agents_config(self):
        from orchestrator.config import module_dir

        path = module_dir() / "opencode.agents.json"
        self.assertTrue(path.is_file())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["agent"]["orchestrator-reviewer"]["permission"]["edit"], "deny")
        self.assertEqual(data["agent"]["orchestrator-reviewer"]["permission"]["bash"], "deny")
        self.assertEqual(data["agent"]["orchestrator-reviewer"]["permission"]["webfetch"], "deny")
        self.assertEqual(data["agent"]["orchestrator-reviewer"]["permission"]["websearch"], "deny")

    def test_run_opencode_uses_agent_command_and_never_auto(self):
        cfg = {
            "command": "opencode",
            "model": "m/reviewer",
            "agent": "orchestrator-reviewer",
            "extra_args": [],
            "agent_config": "C:/nonexistent/agents.json",
        }
        args = build_opencode_command("do review", "C:/repo", cfg)
        self.assertIn("--model", args)
        self.assertEqual(args[args.index("--model") + 1], "m/reviewer")
        self.assertIn("--agent", args)
        self.assertEqual(args[args.index("--agent") + 1], "orchestrator-reviewer")
        self.assertNotIn("--auto", args)

    def test_open_code_config_env_is_set_from_agent_config(self):
        seen = {}

        def fake_popen(cmd, cwd=None, timeout=None, shell=False, env=None):
            seen["env"] = env
            seen["cmd"] = cmd
            return ProcessResult(command=str(cmd[0]), returncode=0, stdout="ok", stderr="")

        import orchestrator.opencode as oc

        original = oc._run_popen
        oc._run_popen = fake_popen
        try:
            import tempfile

            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
                fh.write('{"permission":{"bash":"ask"}}')
                agents = fh.name
            try:
                oc.run_opencode(
                    "hi", "C:/repo", {"command": "opencode", "model": "m", "extra_args": [], "agent_config": agents}
                )
            finally:
                os.unlink(agents)
        finally:
            oc._run_popen = original
        self.assertEqual(seen["env"].get("OPENCODE_CONFIG"), agents)
        self.assertNotIn("--auto", seen["cmd"])


class ReviewerIndependenceTests(unittest.TestCase):
    def test_config_rejects_same_model_for_coder_and_reviewer(self):
        import copy

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            data = copy.deepcopy(DEFAULT_CONFIG)
            data["repo_root"] = str(make_repo(Path(tmp) / "repo"))
            data["roles"]["coder"]["model"] = "m/dupe"
            data["roles"]["reviewer"]["model"] = "m/dupe"
            cfg_path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(str(cfg_path))
            # The module default must not be mutated by this test.
            self.assertEqual(DEFAULT_CONFIG["roles"]["coder"]["model"], "opencode/big-pickle")

    def test_run_model_blocks_overlapping_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            state_file = Path(tmp) / "state.json"
            cfg = make_config_dict(repo, state_file)
            cfg["roles"]["reviewer"]["model"] = cfg["roles"]["coder"]["model"]
            ctx = make_ctx(repo, run_dir, config=cfg)
            with self.assertRaises(workflow.TaskBlocked):
                ctx.run_model("review this", "review-0", role="reviewer")

    def test_empty_reviewer_models_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            cfg = make_config_dict(repo, Path(tmp) / "state.json")
            cfg["roles"]["reviewer"]["model"] = ""
            ctx = make_ctx(repo, run_dir, config=cfg)
            with self.assertRaises(workflow.TaskBlocked):
                ctx.run_model("review this", "review-0", role="reviewer")


class ProviderLimitBehaviorTests(unittest.TestCase):
    def _cfg(self, tmp, coder_models, reviewer_model="m/reviewer"):
        repo = make_repo(Path(tmp) / "repo")
        cfg = make_config_dict(repo, Path(tmp) / "state.json")
        cfg["roles"]["coder"]["model"] = coder_models[0]
        cfg["roles"]["coder"]["fallback_models"] = coder_models[1:]
        cfg["roles"]["reviewer"]["model"] = reviewer_model
        cfg["provider_limits"] = {"transient_retries": 0, "backoff_seconds": 0}
        return repo, cfg

    def test_fallback_to_fallback_model_on_provider_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, cfg = self._cfg(tmp, ["m/primary", "m/fallback"])
            os.makedirs(Path(tmp) / "runs" / "r1", exist_ok=True)
            oc = FakeOpenCode(
                handler=lambda prompt, root, oc_config, timeout: (
                    _limit_fail()
                    if oc_config["model"] == "m/primary"
                    else _ok("[fake] plan: done")
                )
            )
            ctx = make_ctx(repo, Path(tmp) / "runs" / "r1", config=cfg, opencode=oc)
            result = ctx.run_model("make it", "implement", role="coder")
            self.assertTrue(result.ok)
            models = [c["model"] for c in oc.calls]
            self.assertEqual(models, ["m/primary", "m/fallback"])

    def test_transient_retries_are_bounded_per_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, cfg = self._cfg(tmp, ["m/primary", "m/fallback"])
            cfg["provider_limits"] = {"transient_retries": 1, "backoff_seconds": 0}
            os.makedirs(Path(tmp) / "runs" / "r1", exist_ok=True)
            oc = FakeOpenCode(
                handler=lambda prompt, root, oc_config, timeout: (
                    _limit_fail()
                    if oc_config["model"] == "m/primary"
                    else _ok("[fake] plan: done")
                )
            )
            ctx = make_ctx(repo, Path(tmp) / "runs" / "r1", config=cfg, opencode=oc)
            result = ctx.run_model("make it", "implement", role="coder")
            self.assertTrue(result.ok)
            models = [c["model"] for c in oc.calls]
            self.assertEqual(models, ["m/primary", "m/primary", "m/fallback"])

    def test_all_models_exhausted_raises_and_pauses(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            cfg = make_config_dict(repo, Path(tmp) / "state.json")
            cfg["roles"]["coder"]["model"] = "m/primary"
            cfg["roles"]["coder"]["fallback_models"] = ["m/fallback"]
            cfg["provider_limits"] = {"transient_retries": 0, "backoff_seconds": 0}
            oc = FakeOpenCode(handler=lambda prompt, root, oc_config, timeout: _limit_fail())
            task = implement_task()
            ctx = make_ctx(repo, run_dir, config=cfg, task=task, opencode=oc)
            with self.assertRaises(workflow.ProviderLimitReached):
                workflow.run_task(ctx)
            summary = state_mod.get_active_summary(ctx.state)
            self.assertEqual(summary.get("status"), "paused_provider_limit")
            self.assertEqual(summary.get("stage"), "plan")
            self.assertTrue(summary.get("pause_models_tried"))
            self.assertEqual(ctx.final_status, "paused")
            self.assertTrue((run_dir / "terminal.json").is_file())
            terminal = json.loads((run_dir / "terminal.json").read_text(encoding="utf-8"))
            self.assertEqual(terminal["status"], "paused")
            self.assertEqual(terminal["stage"], "plan")

    def test_non_provider_failure_is_not_silently_switched(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, cfg = self._cfg(tmp, ["m/primary", "m/fallback"])
            os.makedirs(Path(tmp) / "runs" / "r1", exist_ok=True)
            oc = FakeOpenCode(handler=lambda prompt, root, oc_config, timeout: _fail("compile error"))
            ctx = make_ctx(repo, Path(tmp) / "runs" / "r1", config=cfg, opencode=oc)
            result = ctx.run_model("make it", "implement", role="coder")
            self.assertFalse(result.ok)
            models = [c["model"] for c in oc.calls]
            self.assertEqual(models, ["m/primary"])


class CheckpointResumeTests(unittest.TestCase):
    def test_runner_exit_code_for_paused_run_is_four(self):
        import argparse

        from orchestrator.runner import EXIT_PAUSED, _post_process_result

        args = argparse.Namespace()
        config = {"queue_stop_on_blocked": True}
        self.assertEqual(_post_process_result("paused", args, config), EXIT_PAUSED)

    def test_checkpoint_manifest_written_after_each_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            state = state_mod.default_state()
            state_mod.begin_run(state, "RUN-TEST-1", "t-readonly", "audit")
            oc = FakeOpenCode()
            ctx = make_ctx(repo, run_dir, state=state, opencode=oc)
            workflow.run_task(ctx)
            latest = run_dir / "manifest-latest.json"
            self.assertTrue(latest.is_file())
            self.assertEqual(
                json.loads(latest.read_text(encoding="utf-8")),
                gitutil.snapshot_manifest(str(repo), ignore_dirs=[".git"]),
            )

    def test_paused_run_resumes_from_latest_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            cfg = make_config_dict(repo, Path(tmp) / "state.json")
            cfg["roles"]["coder"]["model"] = "m/primary"
            cfg["roles"]["coder"]["fallback_models"] = ["m/fallback"]
            cfg["provider_limits"] = {"transient_retries": 0, "backoff_seconds": 0}

            # First attempt: every coder model hits the provider limit at implement.
            limited = FakeOpenCode(handler=lambda p, r, c, t: _limit_fail())
            state = state_mod.default_state()
            first = make_ctx(repo, run_dir, config=cfg, task=implement_task(), opencode=limited, state=state)
            with self.assertRaises(workflow.ProviderLimitReached):
                workflow.run_task(first)
            self.assertEqual(
                state_mod.get_active_summary(first.state)["status"], "paused_provider_limit"
            )

            # Second attempt (same run id, state): limits cleared. Completed stages
            # are skipped; the paused implement stage reruns and review completes.
            ok_oc = FakeOpenCode(
                handler=lambda p, r, c, t: _ok("[fake] plan: make the bounded change")
            )
            resumed = make_ctx(
                repo,
                run_dir,
                config=cfg,
                task=implement_task(),
                opencode=ok_oc,
                state=state,
            )
            self.assertEqual(workflow.run_task(resumed), "completed")
            self.assertTrue((run_dir / "report.md").is_file())
            verdict = json.loads((run_dir / "07-review-verdict.json").read_text(encoding="utf-8"))
            self.assertEqual(verdict["verdict"], "PASS")

    def test_resume_manifest_prefers_last_checkpoint(self):
        from orchestrator.runner import _resume_manifest_check

        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            runs_dir = Path(tmp) / "runs"
            run_id = "RUN-PAUSED-1"
            run_dir = runs_dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)

            base = gitutil.snapshot_manifest(str(repo), ignore_dirs=[".git"])
            (run_dir / "manifest-baseline.json").write_text(json.dumps(base), encoding="utf-8")

            # The run legitimately added an allowed file, then paused at review.
            (repo / "src").mkdir(exist_ok=True)
            (repo / "src" / "example.ts").write_text("export const x = 1;\n", encoding="utf-8")
            latest = gitutil.snapshot_manifest(str(repo), ignore_dirs=[".git"])
            (run_dir / "manifest-latest.json").write_text(json.dumps(latest), encoding="utf-8")

            config = make_config_dict(repo, Path(tmp) / "state.json")
            config["runs_dir"] = str(runs_dir)
            active = {
                "run_id": run_id,
                "task_id": "t-impl",
                "status": "paused",
                "stage": "review",
                "needs_review": False,
            }
            ok, reason = _resume_manifest_check(config, active)
            self.assertTrue(ok, reason)

            # Drift after the checkpoint must still force a recovery review.
            (repo / "DRIFTED.txt").write_text("external", encoding="utf-8")
            ok, reason = _resume_manifest_check(config, active)
            self.assertFalse(ok)
            self.assertIn("DRIFTED.txt", reason)


class IndependentReviewLoopTests(unittest.TestCase):
    def _review_config(self, tmp, max_rounds=3):
        repo = make_repo(Path(tmp) / "repo")
        cfg = make_config_dict(repo, Path(tmp) / "state.json")
        cfg["review"] = {
            "enabled": True,
            "max_rounds": max_rounds,
            "require_independent_model": True,
        }
        return repo, cfg

    def test_review_pass_writes_evidence_with_different_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, cfg = self._review_config(tmp)
            run_dir = Path(tmp) / "runs" / "r1"
            task = implement_task(tests=[{"name": "t", "command": "go test ./..."}])
            oc = FakeOpenCode(
                handler=lambda p, r, c, t: _ok("[fake] plan: implement it"),
                reviewer=lambda p, r, c, t: _ok(REVIEWER_PASS),
            )
            ctx = make_ctx(
                repo, run_dir, config=cfg, task=task, opencode=oc, command=FakeCommand()
            )
            self.assertEqual(workflow.run_task(ctx), "completed")

            reviewer_calls = [c for c in oc.calls if c["role"] == "reviewer"]
            coder_calls = [c for c in oc.calls if c["role"] == "coder"]
            self.assertTrue(reviewer_calls)
            self.assertNotEqual(
                reviewer_calls[0]["model"],
                coder_calls[0]["model"],
                "reviewer must use a different model than the coder",
            )
            self.assertTrue((run_dir / "06-review-round0.json").is_file())
            verdict = json.loads((run_dir / "07-review-verdict.json").read_text(encoding="utf-8"))
            self.assertEqual(verdict["verdict"], "PASS")
            self.assertNotEqual(
                verdict["reviewer_models"][0], verdict["coder_models"][0]
            )

    def test_review_fail_bounded_to_max_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, cfg = self._review_config(tmp, max_rounds=1)
            run_dir = Path(tmp) / "runs" / "r1"
            task = implement_task(tests=[{"name": "t", "command": "go test ./..."}])
            oc = FakeOpenCode(
                handler=lambda p, r, c, t: _ok("[fake] plan: fix it"),
                reviewer=lambda p, r, c, t: _ok(REVIEWER_FAIL),
            )
            ctx = make_ctx(
                repo, run_dir, config=cfg, task=task, opencode=oc, command=FakeCommand()
            )
            with self.assertRaises(workflow.TaskFailed):
                workflow.run_task(ctx)
            reviewer_calls = [c for c in oc.calls if c["role"] == "reviewer"]
            fix_calls = [c for c in oc.calls if c["role"] == "coder" and "FIX ROUND" in c["prompt"]]
            self.assertEqual(len(reviewer_calls), 2)  # rounds 0 and 1 (max_rounds=1)
            self.assertEqual(len(fix_calls), 1)
            verdict = json.loads((run_dir / "07-review-verdict.json").read_text(encoding="utf-8"))
            self.assertEqual(verdict["verdict"], "FAIL")
            self.assertTrue((run_dir / "06-review-round1.json").is_file())

    def test_review_bad_json_blocks_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, cfg = self._review_config(tmp)
            run_dir = Path(tmp) / "runs" / "r1"
            task = implement_task(tests=[{"name": "t", "command": "go test ./..."}])
            oc = FakeOpenCode(
                handler=lambda p, r, c, t: _ok("[fake] plan: ok"),
                reviewer=lambda p, r, c, t: _ok("hmm, it looks fine to me"),
            )
            ctx = make_ctx(
                repo, run_dir, config=cfg, task=task, opencode=oc, command=FakeCommand()
            )
            with self.assertRaises(workflow.TaskBlocked):
                workflow.run_task(ctx)
            verdict = json.loads((run_dir / "07-review-verdict.json").read_text(encoding="utf-8"))
            self.assertEqual(verdict["verdict"], "BLOCKED")

    def test_review_qa_failure_eventually_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, cfg = self._review_config(tmp, max_rounds=1)
            run_dir = Path(tmp) / "runs" / "r1"

            results = iter([ProcessResult(command="go test ./...", returncode=0, stdout="ok", stderr=""),
                            ProcessResult(command="go test ./...", returncode=0, stdout="ok", stderr="")])
            cmd = FakeCommand(lambda command, cwd, timeout: next(results))
            task = implement_task(tests=[{"name": "t", "command": "go test ./..."}])
            oc = FakeOpenCode(
                handler=lambda p, r, c, t: _ok("[fake] plan: fix it"),
                reviewer=lambda p, r, c, t: _ok(REVIEWER_FAIL),
            )
            ctx = make_ctx(repo, run_dir, config=cfg, task=task, opencode=oc, command=cmd)
            with self.assertRaises(workflow.TaskFailed):
                workflow.run_task(ctx)
            verdict = json.loads((run_dir / "07-review-verdict.json").read_text(encoding="utf-8"))
            self.assertEqual(verdict["verdict"], "FAIL")

    def test_reviewer_that_writes_files_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, cfg = self._review_config(tmp)
            run_dir = Path(tmp) / "runs" / "r1"
            task = implement_task(tests=[{"name": "t", "command": "go test ./..."}])

            def sneaky_reviewer(prompt, root, oc_config, timeout):
                (Path(root) / "src" / "sneaky.ts").parent.mkdir(parents=True, exist_ok=True)
                (Path(root) / "src" / "sneaky.ts").write_text("// reviewer wrote this", encoding="utf-8")
                return _ok(REVIEWER_PASS)

            oc = FakeOpenCode(
                handler=lambda p, r, c, t: _ok("[fake] plan: ok"),
                reviewer=sneaky_reviewer,
            )
            ctx = make_ctx(
                repo, run_dir, config=cfg, task=task, opencode=oc, command=FakeCommand()
            )
            with self.assertRaises(workflow.TaskBlocked):
                workflow.run_task(ctx)
            verdict = json.loads((run_dir / "07-review-verdict.json").read_text(encoding="utf-8"))
            self.assertEqual(verdict["verdict"], "BLOCKED")
            record = json.loads((run_dir / "06-review-round0.json").read_text(encoding="utf-8"))
            self.assertTrue(record["reviewer_workspace_changes"]["added"])

    def test_out_of_scope_change_during_review_fix_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, cfg = self._review_config(tmp, max_rounds=2)
            run_dir = Path(tmp) / "runs" / "r1"
            task = implement_task(tests=[{"name": "t", "command": "go test ./..."}])

            def coder(prompt, root, oc_config, timeout):
                if "FIX ROUND" in prompt or "review-fix" in prompt.lower():
                    (Path(root) / "OUTSIDE.txt").write_text("nope", encoding="utf-8")
                return _ok("[fake] plan: fix it")

            oc = FakeOpenCode(
                handler=coder,
                reviewer=lambda p, r, c, t: _ok(REVIEWER_FAIL),
            )
            ctx = make_ctx(
                repo, run_dir, config=cfg, task=task, opencode=oc, command=FakeCommand()
            )
            with self.assertRaises(workflow.TaskBlocked):
                workflow.run_task(ctx)
            self.assertTrue((run_dir / "scope-violations-review-fix-1.json").is_file())


if __name__ == "__main__":
    unittest.main()