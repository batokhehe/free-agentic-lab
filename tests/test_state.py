import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT  # noqa: F401

from orchestrator import state as state_mod


class StatePersistenceTests(unittest.TestCase):
    def test_missing_file_returns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = state_mod.load_state(Path(tmp) / "nope.json")
            self.assertIsNone(state["active_run"])
            self.assertEqual(state["tasks"], {})

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = state_mod.default_state()
            state_mod.begin_run(state, "run-1", "task-1", "audit")
            state_mod.update_stage(state, "plan")
            state_mod.save_state(path, state)

            loaded = state_mod.load_state(path)
            self.assertEqual(loaded["active_run"]["run_id"], "run-1")
            self.assertEqual(loaded["active_run"]["stage"], "plan")
            self.assertEqual(loaded["tasks"]["task-1"]["status"], "running")

    def test_corrupt_state_raises_and_does_not_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(state_mod.StateError):
                state_mod.load_state(path)
            # File left untouched (not silently reset).
            self.assertEqual(path.read_text(encoding="utf-8"), "{not valid json")

    def test_finish_run_moves_active_to_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = state_mod.default_state()
            state_mod.begin_run(state, "run-1", "task-1", "audit")
            state_mod.finish_run(state, "completed")
            self.assertIsNone(state["active_run"])
            self.assertEqual(state["history"][0]["status"], "completed")
            self.assertEqual(state["tasks"]["task-1"]["status"], "completed")
            state_mod.save_state(path, state)
            self.assertEqual(json.loads(path.read_text())["history"][0]["run_id"], "run-1")

    def test_invalid_status_rejected(self):
        state = state_mod.default_state()
        with self.assertRaises(state_mod.StateError):
            state_mod.set_task_status(state, "t", "bogus")


if __name__ == "__main__":
    unittest.main()
