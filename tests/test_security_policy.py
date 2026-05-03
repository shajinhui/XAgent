from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
