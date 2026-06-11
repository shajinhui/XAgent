from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from security import ApprovalPolicy, ExecPolicy, ExecPolicyRule, FileSystemPolicy, PermissionProfile
from security.circuit_breaker import CircuitBreaker
from security.policy import SecurityPolicy
from workspace import AdditionalRoot


class SecurityPolicyTests(unittest.TestCase):
    def test_resolve_path_blocks_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = SecurityPolicy(Path(tmp))

            with self.assertRaises(ValueError):
                policy.resolve_path("/etc/passwd")

    def test_resolve_path_uses_current_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "pkg"
            nested.mkdir()
            policy = SecurityPolicy(root, current_dir=nested)

            self.assertEqual(policy.resolve_path("module.py"), (nested / "module.py").resolve())
            self.assertEqual(policy.resolve_command_cwd(None), nested.resolve())

    def test_file_system_policy_exposes_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = SecurityPolicy(root)

            self.assertEqual(policy.permission_profile, PermissionProfile.WORKSPACE_WRITE)
            self.assertEqual(policy.approval_policy, ApprovalPolicy.ASK_BEFORE_MUTATING)
            self.assertIsInstance(policy.filesystem_policy, FileSystemPolicy)

    def test_ensure_writable_path_blocks_protected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = SecurityPolicy(root)

            with self.assertRaises(PermissionError):
                policy.ensure_writable_path(root / ".env")

            with self.assertRaises(PermissionError):
                policy.ensure_writable_path(root / ".git" / "config")

            with self.assertRaises(PermissionError):
                policy.ensure_writable_path(root / ".codex-mini" / "sessions" / "index.sqlite")

    def test_resolve_read_path_blocks_secret_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            env_path.write_text("API_KEY=secret", encoding="utf-8")
            policy = SecurityPolicy(root)

            with self.assertRaises(PermissionError):
                policy.resolve_read_path(".env")

    def test_read_only_profile_denies_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = SecurityPolicy(root, permission_profile=PermissionProfile.READ_ONLY)

            self.assertEqual(policy.resolve_read_path(".").resolve(), root.resolve())
            with self.assertRaises(PermissionError):
                policy.resolve_write_path("created.txt")

    def test_additional_read_root_does_not_expand_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            external = Path(tmp) / "external"
            workspace.mkdir()
            external.mkdir()
            (external / "notes.txt").write_text("hello", encoding="utf-8")
            filesystem_policy = FileSystemPolicy.workspace_write(
                workspace,
                additional_roots=[AdditionalRoot(external, "read")],
            )
            policy = SecurityPolicy(workspace, filesystem_policy=filesystem_policy)

            self.assertEqual(
                policy.resolve_read_path((external / "notes.txt").as_posix()),
                (external / "notes.txt").resolve(),
            )
            with self.assertRaises(PermissionError):
                policy.resolve_write_path((external / "created.txt").as_posix())

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

    def test_auto_approval_policy_allows_non_dangerous_command_without_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = SecurityPolicy(Path(tmp), approval_policy=ApprovalPolicy.AUTO)

            decision = policy.check_command("ruff check .")

        self.assertEqual(decision.action, "allow")
        self.assertEqual(decision.category, "auto_approved_command")
        self.assertFalse(decision.approval_required)

    def test_auto_approval_policy_keeps_explicit_ask_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = SecurityPolicy(
                Path(tmp),
                approval_policy=ApprovalPolicy.AUTO,
                exec_policy=ExecPolicy([ExecPolicyRule.ask("npm", "publish")]),
            )

            decision = policy.check_command("npm publish")

        self.assertTrue(decision.requires_approval)

    def test_check_command_allows_safe_git_after_validated_cd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = SecurityPolicy(root)

            decision = policy.check_command(f"cd {root.as_posix()} && git log --oneline -10")

        self.assertEqual(decision.category, "safe_read_only")
        self.assertFalse(decision.approval_required)

    def test_check_command_does_not_auto_allow_cd_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            policy = SecurityPolicy(root)

            decision = policy.check_command(f"cd {outside.as_posix()} && git log --oneline -10")

        self.assertTrue(decision.requires_approval)
        self.assertNotEqual(decision.category, "safe_read_only")

    def test_check_command_does_not_auto_allow_mutating_git_after_cd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = SecurityPolicy(root)

            decision = policy.check_command(f"cd {root.as_posix()} && git checkout main")

        self.assertTrue(decision.approval_required)
        self.assertNotEqual(decision.category, "safe_read_only")

    def test_session_prefix_allow_skips_future_command_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = SecurityPolicy(Path(tmp))

            policy.allow_prefix_for_session(("ruff", "check"))
            decision = policy.check_command("ruff check .")

            self.assertEqual(decision.action, "allow")
            self.assertEqual(decision.category, "session_allow")
            self.assertFalse(decision.approval_required)

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
