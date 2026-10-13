import tempfile
import unittest
from pathlib import Path

from tests.helpers import (
    FakeCommand,
    FakeOpenCode,
    make_ctx,
    make_repo,
    read_only_task,
)

from orchestrator import gitutil, state as state_mod, workflow
from orchestrator.opencode import ProcessResult


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


def _ok(stdout="[fake] plan: no file changes required for this task."):
    return ProcessResult(command="fake", returncode=0, stdout=stdout, stderr="")


class ReadOnlyWorkflowTests(unittest.TestCase):
    def test_dry_run_completes_and_writes_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            ctx = make_ctx(repo, run_dir, dry_run=True)
            head_before = gitutil.git_head(str(repo))

            status = workflow.run_task(ctx)

            self.assertEqual(status, "completed")
            self.assertTrue((run_dir / "report.md").is_file())
            self.assertTrue((run_dir / "01-audit.json").is_file())
            self.assertTrue((run_dir / "manifest-baseline.json").is_file())
            self.assertTrue((run_dir / "evidence-index.json").is_file())
            self.assertEqual(gitutil.git_head(str(repo)), head_before)

    def test_model_failure_fails_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            failing = FakeOpenCode(
                lambda prompt, root, cfg, timeout: ProcessResult(
                    command="fake", returncode=2, stdout="", stderr="boom"
                )
            )
            ctx = make_ctx(repo, run_dir, opencode=failing)
            with self.assertRaises(workflow.TaskFailed):
                workflow.run_task(ctx)

    def test_read_only_change_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"

            def handler(prompt, root, cfg, timeout):
                (Path(root) / "SNEAKY.txt").write_text("changed", encoding="utf-8")
                return _ok()

            ctx = make_ctx(repo, run_dir, opencode=FakeOpenCode(handler))
            with self.assertRaises(workflow.TaskBlocked):
                workflow.run_task(ctx)
            # Fail-closed drift guard fires immediately after the analyze stage.
            self.assertTrue((run_dir / "drift-analyze.json").is_file())
            # The orchestrator never restores or deletes the offending file.
            self.assertTrue((repo / "SNEAKY.txt").is_file())

    def test_failing_read_only_test_does_not_fail_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            task = read_only_task(tests=[{"name": "check", "command": "go vet ./..."}])
            failing_cmd = FakeCommand(
                lambda command, cwd, timeout: ProcessResult(
                    command=str(command), returncode=1, stdout="bad", stderr=""
                )
            )
            ctx = make_ctx(repo, run_dir, task=task, command=failing_cmd)
            status = workflow.run_task(ctx)
            self.assertEqual(status, "completed")
            self.assertFalse(ctx.test_passed)
            self.assertTrue((run_dir / "report.md").is_file())


class ImplementWorkflowTests(unittest.TestCase):
    def test_implement_success_when_tests_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            task = _implement_task(tests=[{"name": "t", "command": "python -m pytest tests"}])
            ctx = make_ctx(repo, run_dir, task=task)
            status = workflow.run_task(ctx)
            self.assertEqual(status, "completed")
            self.assertTrue(ctx.test_passed)

    def test_retry_limit_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            task = _implement_task(tests=[{"name": "t", "command": "python -m pytest tests"}])
            failing_cmd = FakeCommand(
                lambda command, cwd, timeout: ProcessResult(
                    command=str(command), returncode=1, stdout="still bad", stderr=""
                )
            )
            ctx = make_ctx(repo, run_dir, task=task, command=failing_cmd, max_fix=2)
            with self.assertRaises(workflow.TaskFailed):
                workflow.run_task(ctx)
            self.assertEqual(ctx.attempts, 2)
            fixes = sorted(run_dir.glob("05-fix-attempt*.out.md"))
            self.assertEqual(len(fixes), 2)

    def test_out_of_scope_change_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            task = _implement_task(allowed_paths=["src/**"])

            def handler(prompt, root, cfg, timeout):
                if "Implement the task" in prompt:
                    (Path(root) / "OUTSIDE.txt").write_text("nope", encoding="utf-8")
                return _ok()

            ctx = make_ctx(repo, run_dir, task=task, opencode=FakeOpenCode(handler))
            with self.assertRaises(workflow.TaskBlocked):
                workflow.run_task(ctx)
            self.assertTrue((run_dir / "scope-violations-implement.json").is_file())

    def test_protected_change_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            task = _implement_task(allowed_paths=["**", "src/**"])

            def handler(prompt, root, cfg, timeout):
                if "Implement the task" in prompt:
                    (Path(root) / ".env").write_text("SECRET=x", encoding="utf-8")
                return _ok()

            ctx = make_ctx(repo, run_dir, task=task, opencode=FakeOpenCode(handler))
            with self.assertRaises(workflow.TaskBlocked):
                workflow.run_task(ctx)

    def test_no_commit_or_index_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            head_before = gitutil.git_head(str(repo))
            ctx = make_ctx(
                repo,
                run_dir,
                task=_implement_task(tests=[{"name": "t", "command": "python -m pytest tests"}]),
            )
            workflow.run_task(ctx)
            self.assertEqual(gitutil.git_head(str(repo)), head_before)
            _, staged, _ = gitutil.run_git(str(repo), ["diff", "--cached", "--name-only"])
            self.assertEqual(staged.strip(), "")


class ResumeTests(unittest.TestCase):
    def test_resume_skips_completed_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_dir = Path(tmp) / "runs" / "r1"
            state = state_mod.default_state()
            state_mod.begin_run(state, "RUN-TEST-1", "t-readonly", "audit")

            first = make_ctx(repo, run_dir, state=state, dry_run=True)
            workflow.do_audit(first)
            first.mark_done("audit")
            original_audit = (run_dir / "01-audit.json").read_text(encoding="utf-8")

            resumed = make_ctx(repo, run_dir, state=state, dry_run=True)
            status = workflow.run_task(resumed)

            self.assertEqual(status, "completed")
            self.assertEqual(
                (run_dir / "01-audit.json").read_text(encoding="utf-8"), original_audit
            )


if __name__ == "__main__":
    unittest.main()
