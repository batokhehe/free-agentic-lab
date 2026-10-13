import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_repo, write_config_file

from orchestrator import gitutil, state as state_mod
from orchestrator.runner import main


def _write_task(tasks_dir: Path, name: str, task_id: str, priority: int) -> None:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / name).write_text(
        f'id: "{task_id}"\n'
        f'title: "Task {task_id}"\n'
        f'mode: read_only\n'
        f'priority: {priority}\n'
        f'goal: "Inspect the repository read-only."\n'
        f'tests: []\n',
        encoding="utf-8",
    )


class QueueAndStateTests(unittest.TestCase):
    def _setup(self, tmp):
        ws = Path(tmp) / "ws"
        repo = make_repo(ws / "repo")
        tasks_dir = ws / "tasks"
        runs_dir = ws / "runs"
        state_file = ws / "state" / "state.json"
        _write_task(tasks_dir, "a.yaml", "task-a", 20)
        _write_task(tasks_dir, "b.yaml", "task-b", 10)
        config = write_config_file(ws / "orchestrator.config.json", repo, tasks_dir, runs_dir, state_file)
        return ws, repo, config, state_file

    def test_priority_order_and_state_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, repo, config, state_file = self._setup(tmp)
            rc = main(["--config", str(config), "--once", "--dry-run"])
            self.assertEqual(rc, 0)
            state = state_mod.load_state(state_file)
            self.assertEqual(state["tasks"]["task-a"]["status"], "completed")
            self.assertNotIn("task-b", state["tasks"])

            rc = main(["--config", str(config), "--once", "--dry-run"])
            self.assertEqual(rc, 0)
            state = state_mod.load_state(state_file)
            self.assertEqual(state["tasks"]["task-b"]["status"], "completed")

            rc = main(["--config", str(config), "--once", "--dry-run"])
            self.assertEqual(rc, 0)

    def test_list_does_not_run_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, repo, config, state_file = self._setup(tmp)
            rc = main(["--config", str(config), "--list"])
            self.assertEqual(rc, 0)
            state = state_mod.load_state(state_file)
            self.assertEqual(state["tasks"], {})

    def test_resume_after_interruption_preserves_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, repo, config, state_file = self._setup(tmp)
            # Simulate an interrupted run whose audit stage already finished.
            runs_dir = ws / "runs"
            run_id = "20260101T000000Z-task-a-abcd"
            run_dir = runs_dir / run_id
            (run_dir / "stages").mkdir(parents=True, exist_ok=True)
            audit_path = run_dir / "01-audit.json"
            seeded_audit = json.dumps({"head": gitutil.git_head(str(repo))})
            audit_path.write_text(seeded_audit, encoding="utf-8")
            manifest = gitutil.snapshot_manifest(
                str(repo), ignore_dirs=[".git", "node_modules", ".next", "test-results"]
            )
            (run_dir / "manifest-baseline.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            (run_dir / "stages" / "audit.done").write_text("{}", encoding="utf-8")

            state = state_mod.default_state()
            state_mod.begin_run(state, run_id, "task-a", "analyze")
            state_mod.save_state(state_file, state)

            rc = main(["--config", str(config), "--once", "--dry-run", "--resume"])
            self.assertEqual(rc, 0)
            # Audit stage must not have been re-run.
            self.assertEqual(audit_path.read_text(encoding="utf-8"), seeded_audit)
            state = state_mod.load_state(state_file)
            self.assertEqual(state["tasks"]["task-a"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
