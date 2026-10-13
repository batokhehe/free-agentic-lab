import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.helpers import (
    FakeCommand,
    FakeOpenCode,
    make_ctx,
    make_repo,
    write_config_file,
)

from orchestrator import gitutil, policy, state as state_mod, workflow
from orchestrator.opencode import ProcessResult
from orchestrator.runner import main


def _implement_task(**overrides):
    task = {
        "id": "t-impl",
        "title": "Implement task",
        "mode": "implement",
        "goal": "Make a bounded change.",
        "allowed_paths": ["src/**"],
        "forbidden_paths": [],
        "tests": [],
    }
    task.update(overrides)
    return task


def _readonly_task(tests=None):
    task = {
        "id": "t-ro",
        "title": "Read-only task",
        "mode": "read_only",
        "goal": "Inspect only.",
        "allowed_paths": [],
        "forbidden_paths": [],
        "tests": tests or [],
    }
    return task


FAKE_PLAN = "[fake] plan: touch src/example.ts, then run the configured tests."


def _ok(stdout="ok"):
    return ProcessResult(command="fake", returncode=0, stdout=stdout, stderr="")


def _fail(stdout=""):
    return ProcessResult(command="fake", returncode=1, stdout=stdout, stderr="boom")


def _make_runner_env(tmp):
    """A full runner environment in a temporary directory (never Sentinel)."""
    ws = Path(tmp) / "ws"
    repo = make_repo(ws / "repo")
    tasks_dir = ws / "tasks"
    runs_dir = ws / "runs"
    state_file = ws / "state" / "state.json"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for name, task_id, prio in (("a.yaml", "task-a", 20), ("b.yaml", "task-b", 10)):
        (tasks_dir / name).write_text(
            f'id: "{task_id}"\n'
            f'title: "Task {task_id}"\n'
            f'mode: read_only\n'
            f"priority: {prio}\n"
            f'goal: "Inspect the repository read-only."\n'
            f"tests: []\n",
            encoding="utf-8",
        )
    config = write_config_file(
        ws / "orchestrator.config.json", repo, tasks_dir, runs_dir, state_file
    )
    return ws, repo, config, state_file


def _seed_interrupted_run(repo, runs_dir, run_id, manifest):
    run_dir = runs_dir / run_id
    (run_dir / "stages").mkdir(parents=True, exist_ok=True)
    (run_dir / "01-audit.json").write_text(
        json.dumps({"head": gitutil.git_head(str(repo))}), encoding="utf-8"
    )
    (run_dir / "manifest-baseline.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "stages" / "audit.done").write_text("{}", encoding="utf-8")
    state = state_mod.default_state()
    state_mod.begin_run(state, run_id, "task-a", "analyze")
    return state, run_dir


class ReadOnlyDriftTests(unittest.TestCase):
    def _assert_blocked_on_analyze(self, mutator, run_dir, repo):
        def handler(prompt, root, cfg, timeout):
            mutator(Path(root))
            return _ok()

        ctx = make_ctx(repo, run_dir, opencode=FakeOpenCode(handler), task=_readonly_task())
        with self.assertRaises(workflow.TaskBlocked):
            workflow.run_task(ctx)
        self.assertTrue((run_dir / "drift-analyze.json").is_file())

    def test_read_only_modifies_tracked_file_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"

            def mutate(root):
                (root / "README.md").write_text("tampered", encoding="utf-8")

            self._assert_blocked_on_analyze(mutate, run_dir, repo)
            self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "tampered")
            record = json.loads((run_dir / "drift-analyze.json").read_text(encoding="utf-8"))
            self.assertIn("README.md", record["changes"]["modified"])

    def test_read_only_creates_untracked_file_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"

            def mutate(root):
                (root / "NEW.txt").write_text("created", encoding="utf-8")

            self._assert_blocked_on_analyze(mutate, run_dir, repo)
            # Fail-closed, but the orchestrator does NOT delete the created file.
            self.assertTrue((repo / "NEW.txt").is_file())
            record = json.loads((run_dir / "drift-analyze.json").read_text(encoding="utf-8"))
            self.assertIn("NEW.txt", record["changes"]["added"])

    def test_read_only_deletes_file_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"

            def mutate(root):
                (root / "README.md").unlink()

            self._assert_blocked_on_analyze(mutate, run_dir, repo)
            # No auto-restore: the deletion is left untouched for human review.
            self.assertFalse((repo / "README.md").exists())
            record = json.loads((run_dir / "drift-analyze.json").read_text(encoding="utf-8"))
            self.assertIn("README.md", record["changes"]["removed"])


class OutOfScopeTests(unittest.TestCase):
    def test_out_of_scope_implement_change_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            task = _implement_task(allowed_paths=["src/**"])

            def handler(prompt, root, cfg, timeout):
                if "Implement the task" in prompt:
                    (Path(root) / "OUTSIDE.txt").write_text("nope", encoding="utf-8")
                    return _ok()
                return _ok(FAKE_PLAN)

            ctx = make_ctx(repo, run_dir, task=task, opencode=FakeOpenCode(handler))
            with self.assertRaises(workflow.TaskBlocked):
                workflow.run_task(ctx)
            self.assertTrue((run_dir / "scope-violations-implement.json").is_file())


class ApprovalGateTests(unittest.TestCase):
    def test_approval_categorized_command_without_approval_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            task = _implement_task(tests=[{"name": "dep", "command": "pnpm add left-pad"}])
            ctx = make_ctx(repo, run_dir, task=task)
            with self.assertRaises(workflow.ApprovalRequired):
                workflow.run_task(ctx)

    def test_deny_categorized_command_always_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            task = _implement_task(tests=[{"name": "push", "command": "git push origin main"}])
            ctx = make_ctx(repo, run_dir, task=task)
            with self.assertRaises(workflow.TaskBlocked):
                workflow.run_task(ctx)

    def test_unclassifiable_approval_fails_closed(self):
        verdict = policy.check_command_safety("some-tool --weird-install-flag")
        self.assertEqual(verdict["category"], "deny")  # fail-closed, no assume-safe
        self.assertEqual(policy.approval_action("pnpm add x"), "dependency-install")
        self.assertEqual(policy.approval_action("goose up"), "db-migration")
        self.assertEqual(policy.approval_action("docker compose up"), "start-service")

    def test_approval_for_one_action_does_not_authorize_another(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            state = state_mod.default_state()
            state_mod.record_approval(state, "t-impl", "dependency-install", "RUN-TEST-1")
            task = _implement_task(tests=[{"name": "m", "command": "goose up"}])
            ctx = make_ctx(repo, run_dir, task=task, state=state)
            with self.assertRaises(workflow.ApprovalRequired):
                workflow.run_task(ctx)

    def test_approval_is_consumed_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            state = state_mod.default_state()
            state_mod.record_approval(state, "t-impl", "dependency-install", "RUN-TEST-1")
            task = _implement_task(tests=[{"name": "d", "command": "pnpm add left-pad"}])

            first = make_ctx(
                repo,
                Path(tmp) / "runs" / "r1",
                task=task,
                state=state,
                command=FakeCommand(),
            )
            status = workflow.run_task(first)
            self.assertEqual(status, "completed")
            self.assertTrue(first.test_passed)

            second = make_ctx(
                repo,
                Path(tmp) / "runs" / "r2",
                task=task,
                state=state,
                command=FakeCommand(),
            )
            with self.assertRaises(workflow.ApprovalRequired):
                workflow.run_task(second)


class RetryLimitTests(unittest.TestCase):
    def test_fix_attempts_capped_by_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            task = _implement_task(tests=[{"name": "t", "command": "python -m pytest tests"}])
            failing = FakeCommand(lambda command, cwd, timeout: _fail("still failing"))
            ctx = make_ctx(repo, run_dir, task=task, command=failing, max_fix=1)
            with self.assertRaises(workflow.TaskFailed):
                workflow.run_task(ctx)
            self.assertEqual(ctx.attempts, 1)
            self.assertTrue((run_dir / "05-fix-attempt1.out.md").is_file())
            self.assertFalse((run_dir / "05-fix-attempt2.out.md").exists())
            summary = state_mod.get_active_summary(ctx.state)
            self.assertEqual(summary.get("attempts"), 1)


class BoundaryValidationTests(unittest.TestCase):
    def test_boundary_validation_rejects_paths_outside_workspace(self):
        ws = Path(tempfile.mkdtemp())
        repo = make_repo(ws / "repo")
        good = {
            "workspace_root": str(ws),
            "repo_root": str(repo),
            "runs_dir": str(ws / "runs"),
            "tasks_dir": str(ws / "tasks"),
            "state_file": str(ws / "state" / "state.json"),
        }
        problems = policy.validate_repo_boundary(good)
        self.assertEqual(problems, [])

        bad = dict(good)
        bad["state_file"] = str(Path(ws) / ".." / "outside-state.json")
        problems = policy.validate_repo_boundary(bad)
        self.assertTrue(any("resolves outside workspace_root" in p for p in problems))

    def test_boundary_rejects_symlink_escape(self):
        ws = Path(tempfile.mkdtemp())
        repo = make_repo(ws / "repo")
        outside = Path(ws).parent / "escape-target"
        try:
            Path(outside).mkdir(parents=True, exist_ok=True)
        except OSError:
            outside = repo
        cfg = {
            "workspace_root": str(ws),
            "repo_root": str(repo),
            "runs_dir": str(ws / "runs"),
            "tasks_dir": str(ws / "tasks"),
            "state_file": str(outside / "state.json"),
        }
        problems = policy.validate_repo_boundary(cfg)
        self.assertTrue(any("resolves outside workspace_root" in p for p in problems))


class StateAndResumeTests(unittest.TestCase):
    def test_completed_task_not_rerun_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, repo, config, state_file = _make_runner_env(tmp)
            rc = main(["--config", str(config), "--once", "--dry-run"])
            self.assertEqual(rc, 0)
            state = state_mod.load_state(state_file)
            self.assertEqual(state["tasks"]["task-a"]["status"], "completed")

            rc = main(["--config", str(config), "--task", "task-a", "--dry-run"])
            self.assertEqual(rc, 3)
            self.assertEqual(len(state_mod.load_state(state_file)["history"]), 1)

            rc = main(["--config", str(config), "--task", "task-a", "--dry-run", "--force"])
            self.assertEqual(rc, 0)
            self.assertEqual(len(state_mod.load_state(state_file)["history"]), 2)

    def test_ambiguous_interruption_requires_explicit_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, repo, config, state_file = _make_runner_env(tmp)
            manifest = gitutil.snapshot_manifest(str(repo), ignore_dirs=[".git"])
            state, run_dir = _seed_interrupted_run(
                repo, ws / "runs", "20260101T000000Z-task-a-abcd", manifest
            )
            state_mod.save_state(state_file, state)

            rc = main(["--config", str(config), "--once", "--dry-run"])
            self.assertEqual(rc, 3)
            self.assertTrue(state_mod.load_state(state_file)["active_run"])

            rc = main(["--config", str(config), "--once", "--dry-run", "--resume"])
            self.assertEqual(rc, 0)
            state = state_mod.load_state(state_file)
            self.assertEqual(state["tasks"]["task-a"]["status"], "completed")
            self.assertIsNone(state["active_run"])

    def test_interrupted_run_with_drift_requires_recovery_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, repo, config, state_file = _make_runner_env(tmp)
            manifest = gitutil.snapshot_manifest(str(repo), ignore_dirs=[".git"])
            state, run_dir = _seed_interrupted_run(
                repo, ws / "runs", "20260101T000000Z-task-a-abcd", manifest
            )
            state_mod.save_state(state_file, state)

            # The workspace changed while the run was interrupted.
            (repo / "DRIFTED.txt").write_text("changed externally", encoding="utf-8")

            rc = main(["--config", str(config), "--once", "--dry-run", "--resume"])
            self.assertEqual(rc, 2)
            loaded = state_mod.load_state(state_file)
            self.assertTrue(loaded["active_run"]["needs_review"])
            self.assertIn("needs_review", loaded["active_run"]["status"])

    def test_corrupted_state_fails_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, repo, config, state_file = _make_runner_env(tmp)
            Path(state_file).parent.mkdir(parents=True, exist_ok=True)
            Path(state_file).write_text("{corrupt json!!", encoding="utf-8")
            rc = main(["--config", str(config), "--once", "--dry-run"])
            self.assertEqual(rc, 3)
            self.assertEqual(Path(state_file).read_text(encoding="utf-8"), "{corrupt json!!")

    def test_duplicate_concurrent_runner_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, repo, config, state_file = _make_runner_env(tmp)
            lock_path = Path(state_file).parent / ".orchestrator.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text(
                json.dumps({"pid": os.getpid(), "created_at": "now"}), encoding="utf-8"
            )
            rc = main(["--config", str(config), "--once", "--dry-run"])
            self.assertEqual(rc, 3)
            self.assertEqual(state_mod.load_state(state_file)["tasks"], {})

            lock_path.unlink()
            rc = main(["--config", str(config), "--once", "--dry-run"])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()