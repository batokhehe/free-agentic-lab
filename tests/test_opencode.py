import sys
import unittest

from tests.helpers import ROOT  # noqa: F401

from orchestrator import opencode


class CommandBuildTests(unittest.TestCase):
    def test_command_is_non_interactive_and_no_auto(self):
        cfg = {"command": "opencode", "model": "opencode/big-pickle", "extra_args": []}
        args = opencode.build_opencode_command("do the thing", "D:/repo", cfg)
        self.assertTrue(args[0].lower().startswith("opencode") or "opencode" in args[0].lower())
        self.assertEqual(args[1], "run")
        self.assertIn("--model", args)
        self.assertIn("opencode/big-pickle", args)
        self.assertIn("--dir", args)
        self.assertIn("D:/repo", args)
        self.assertNotIn("--auto", args)
        self.assertEqual(args[-1], "do the thing")


class ProcessTests(unittest.TestCase):
    def test_captures_stdout_and_exit_code(self):
        result = opencode.run_command(
            [sys.executable, "-c", "print('hello'); import sys; sys.exit(0)"], timeout=30
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)
        self.assertFalse(result.timed_out)

    def test_failure_exit_code_surfaced(self):
        result = opencode.run_command(
            [sys.executable, "-c", "import sys; sys.exit(3)"], timeout=30
        )
        self.assertEqual(result.returncode, 3)
        self.assertFalse(result.ok)

    def test_timeout_kills_process_tree(self):
        result = opencode.run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=1
        )
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timeout", result.stderr.lower())

    def test_missing_executable(self):
        result = opencode.run_command(
            ["definitely-not-a-real-binary-xyz", "--version"], timeout=5
        )
        self.assertEqual(result.returncode, 127)
        self.assertTrue(result.error)


if __name__ == "__main__":
    unittest.main()
