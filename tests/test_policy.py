import unittest

from tests.helpers import ROOT  # noqa: F401  (ensures orchestrator importable)

from orchestrator import policy
from orchestrator.config import DEFAULT_CONFIG


class CommandSafetyTests(unittest.TestCase):
    def test_destructive_git_denied(self):
        for command in (
            "git commit -m 'x'",
            "git push origin main",
            "git reset --hard HEAD",
            "git stash",
            "git clean -fd",
            "git checkout -- .",
            "git add .",
            "git restore file.txt",
        ):
            with self.subTest(command=command):
                self.assertEqual(policy.check_command_safety(command)["category"], "deny")

    def test_destructive_fs_denied(self):
        for command in (
            "rm -rf /tmp/x",
            "Remove-Item -Recurse -Force .\\foo",
            "del /f /s /q C:\\x",
        ):
            with self.subTest(command=command):
                self.assertEqual(policy.check_command_safety(command)["category"], "deny")

    def test_scanners_denied(self):
        for command in ("nmap -sV target", "sqlmap -u http://x", "nc -lvnp 4444"):
            with self.subTest(command=command):
                self.assertEqual(policy.check_command_safety(command)["category"], "deny")

    def test_db_and_deploy_denied(self):
        for command in ("DROP TABLE users", "docker push image", "npm publish"):
            with self.subTest(command=command):
                self.assertEqual(policy.check_command_safety(command)["category"], "deny")

    def test_dependency_install_requires_approval(self):
        for command in ("pnpm add left-pad", "pip install requests", "go get example.com/x"):
            with self.subTest(command=command):
                self.assertEqual(
                    policy.check_command_safety(command)["category"], "approval"
                )

    def test_safe_commands_allowed(self):
        for command in (
            "pnpm run lint",
            "pnpm run typecheck",
            "node --version",
            "git status --porcelain",
            "git rev-parse HEAD",
        ):
            with self.subTest(command=command):
                self.assertEqual(policy.check_command_safety(command)["category"], "ok")


class BoundaryTests(unittest.TestCase):
    def test_is_within(self):
        self.assertTrue(policy.is_within("D:/repo", "D:/repo/sub/file.txt"))
        self.assertFalse(policy.is_within("D:/repo", "D:/other/file.txt"))

    def test_classify_change(self):
        allowed = ["src/**"]
        forbidden = [".env"]
        self.assertEqual(policy.classify_change("src/a.py", allowed, forbidden), "allowed")
        self.assertEqual(policy.classify_change(".env", allowed, forbidden), "protected")
        self.assertEqual(policy.classify_change("deps/x", allowed, forbidden), "out-of-scope")
        self.assertEqual(policy.classify_change("anything", [], forbidden), "out-of-scope")

    def test_matches_any_directory_glob(self):
        self.assertTrue(policy.matches_any("src/deep/file.py", ["src/**"]))
        self.assertTrue(policy.matches_any("src/file.py", ["src"]))


class RedactionTests(unittest.TestCase):
    def test_redact_key_like_values(self):
        text = "api_key=abcdefghijklmno and password: hunter2hunter2"
        redacted = policy.redact(text)
        self.assertNotIn("abcdefghijklmno", redacted)
        self.assertNotIn("hunter2hunter2", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_redact_private_key(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMII...\n"
        self.assertIn("[REDACTED]", policy.redact(text))


class ValidateTaskTests(unittest.TestCase):
    def test_read_only_with_allowed_paths_is_invalid(self):
        task = {"id": "x", "title": "x", "goal": "g", "mode": "read_only", "allowed_paths": ["a/**"]}
        errors = policy.validate_task(task, DEFAULT_CONFIG)
        self.assertTrue(any("must not declare allowed_paths" in e for e in errors))

    def test_path_escape_is_invalid(self):
        task = {
            "id": "x",
            "title": "x",
            "goal": "g",
            "mode": "implement",
            "allowed_paths": ["../outside/**"],
        }
        errors = policy.validate_task(task, DEFAULT_CONFIG)
        self.assertTrue(any("repository-relative" in e for e in errors))

    def test_forbidden_test_command_is_invalid(self):
        task = {
            "id": "x",
            "title": "x",
            "goal": "g",
            "mode": "implement",
            "tests": [{"name": "bad", "command": "git push"}],
        }
        errors = policy.validate_task(task, DEFAULT_CONFIG)
        self.assertTrue(any("forbidden" in e for e in errors))

    def test_secret_like_key_is_invalid(self):
        task = {
            "id": "x",
            "title": "x",
            "goal": "g",
            "mode": "read_only",
            "api_key": "AKIAIOSFODNN7EXAMPLE",
        }
        errors = policy.validate_task(task, DEFAULT_CONFIG)
        self.assertTrue(any("secret-like" in e for e in errors))

    def test_valid_task(self):
        task = {
            "id": "x",
            "title": "x",
            "goal": "g",
            "mode": "implement",
            "allowed_paths": ["src/**"],
            "tests": [{"name": "t", "command": "pnpm run lint"}],
        }
        self.assertEqual(policy.validate_task(task, DEFAULT_CONFIG), [])


if __name__ == "__main__":
    unittest.main()
