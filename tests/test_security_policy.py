from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from security.circuit_breaker import CircuitBreaker
from security.policy import SecurityPolicy


class SecurityPolicyTests(unittest.TestCase):
    def test_resolve_path_blocks_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = SecurityPolicy(Path(tmp))

            with self.assertRaises(ValueError):
                policy.resolve_path("/etc/passwd")

    def test_ensure_writable_path_blocks_protected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = SecurityPolicy(root)

            with self.assertRaises(PermissionError):
                policy.ensure_writable_path(root / ".env")

            with self.assertRaises(PermissionError):
                policy.ensure_writable_path(root / ".git" / "config")

    def test_resolve_command_cwd_accepts_workspace_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "desktop"
            nested.mkdir()
            policy = SecurityPolicy(root)

            self.assertEqual(policy.resolve_command_cwd("desktop"), nested.resolve())
            self.assertEqual(policy.resolve_command_cwd(nested.as_posix()), nested.resolve())
            self.assertEqual(policy.resolve_command_cwd(None), root.resolve())

    def test_resolve_command_cwd_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = SecurityPolicy(root)

            with self.assertRaises(ValueError):
                policy.resolve_command_cwd("..")

    def test_resolve_command_cwd_rejects_file_and_protected_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = SecurityPolicy(root)
            file_path = root / "notes.txt"
            file_path.write_text("hello", encoding="utf-8")
            (root / ".git").mkdir()

            with self.assertRaises(ValueError):
                policy.resolve_command_cwd("notes.txt")

            with self.assertRaises(PermissionError):
                policy.resolve_command_cwd(".git")

    def test_check_command_denies_dangerous_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = SecurityPolicy(Path(tmp))

            decision = policy.check_command("rm -rf /")

            self.assertEqual(decision.action, "deny")
            self.assertEqual(decision.category, "dangerous_shell")

    def test_check_command_asks_for_non_allowlisted_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = SecurityPolicy(Path(tmp))

            decision = policy.check_command("ruff check .")

            self.assertEqual(decision.action, "ask")
            self.assertTrue(decision.requires_approval)

    def test_check_command_allows_approved_non_allowlisted_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = SecurityPolicy(Path(tmp))

            decision = policy.check_command("ruff check .", approved=True)

            self.assertEqual(decision.action, "allow")
            self.assertTrue(decision.allowed)

    def test_check_command_denies_protected_path_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = SecurityPolicy(Path(tmp))

            decision = policy.check_command("cat .env")

            self.assertEqual(decision.action, "deny")
            self.assertEqual(decision.category, "protected_path")

    def test_circuit_breaker_can_reset_suspended_session(self) -> None:
        breaker = CircuitBreaker(threshold=2)

        self.assertFalse(breaker.record_rejection("session-1", "dangerous_shell"))
        self.assertTrue(breaker.record_rejection("session-1", "dangerous_shell"))
        self.assertEqual(breaker.count("session-1", "dangerous_shell"), 2)

        breaker.reset("session-1", "dangerous_shell")

        self.assertEqual(breaker.count("session-1", "dangerous_shell"), 0)


if __name__ == "__main__":
    unittest.main()
