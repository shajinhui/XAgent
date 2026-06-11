from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from security import ApprovalPolicy, ExecPolicy, ExecPolicyRule
from security.policy import SecurityPolicy


class ExecPolicyTests(unittest.TestCase):
    def test_session_allow_prefix_allows_without_extra_runtime_approval(self) -> None:
        policy = ExecPolicy.with_session_allow([("npm", "run", "test")])

        decision = policy.decide("npm run test -- --watch=false")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.category, "session_allow")
        self.assertFalse(decision.approval_required)
        self.assertEqual(decision.matched_prefix_rule, ("npm", "run", "test"))

    def test_python_c_does_not_suggest_persistent_prefix(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide('python -c "print(1)"')

        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.suggested_prefix_rule)

    def test_non_allowlisted_command_gets_narrow_prefix_suggestion(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("ruff check .")

        self.assertTrue(decision.requires_approval)
        self.assertEqual(decision.suggested_prefix_rule, ("ruff", "check"))

    def test_simple_read_only_command_allows_without_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("ls -la")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.category, "safe_read_only")
        self.assertFalse(decision.approval_required)

    def test_safe_find_sort_pipeline_allows_without_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("find . -maxdepth 1 -not -name '.' -not -name '.git' | sort")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.category, "safe_read_only")
        self.assertFalse(decision.approval_required)

    def test_safe_find_sort_unique_pipeline_allows_without_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("find . -maxdepth 1 -type f | sort -u")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.category, "safe_read_only")
        self.assertFalse(decision.approval_required)

    def test_safe_git_log_allows_without_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("git log --oneline -10")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.category, "safe_read_only")
        self.assertFalse(decision.approval_required)

    def test_safe_git_status_allows_without_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("git status --short")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.category, "safe_read_only")
        self.assertFalse(decision.approval_required)

    def test_safe_git_diff_allows_without_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("git diff --stat")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.category, "safe_read_only")
        self.assertFalse(decision.approval_required)

    def test_compound_read_only_command_still_requires_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("pwd && ls -la")

        self.assertTrue(decision.requires_approval)
        self.assertIsNone(decision.suggested_prefix_rule)

    def test_find_with_delete_still_requires_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("find . -delete")

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.approval_required)
        self.assertNotEqual(decision.category, "safe_read_only")

    def test_find_pipeline_with_parent_path_still_requires_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("find .. -maxdepth 1 | sort")

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.approval_required)
        self.assertNotEqual(decision.category, "safe_read_only")

    def test_find_with_exec_still_requires_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("find . -exec rm {} \\;")

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.approval_required)
        self.assertNotEqual(decision.category, "safe_read_only")

    def test_find_pipeline_with_redirect_still_requires_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("find . -maxdepth 1 | sort > files.txt")

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.approval_required)
        self.assertNotEqual(decision.category, "safe_read_only")

    def test_git_checkout_still_requires_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("git checkout main")

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.approval_required)
        self.assertNotEqual(decision.category, "safe_read_only")

    def test_git_output_still_requires_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("git log --output=log.txt")

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.approval_required)
        self.assertNotEqual(decision.category, "safe_read_only")

    def test_read_only_command_with_external_path_still_requires_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("cat ~/.ssh/id_rsa")

        self.assertTrue(decision.requires_approval)
        self.assertIsNone(decision.suggested_prefix_rule)

    def test_unprotected_exec_policy_does_not_deny_protected_path_reference(self) -> None:
        policy = ExecPolicy(protect_paths=False)

        decision = policy.decide("cat .env")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.category, "safe_read_only")
        self.assertFalse(decision.approval_required)

    def test_common_mutating_runtime_command_still_requires_approval(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("python -m unittest discover -s tests")

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.approval_required)

    def test_dangerous_command_is_denied_even_when_approved(self) -> None:
        policy = ExecPolicy()

        decision = policy.decide("rm -rf /", approved=True)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "deny")
        self.assertEqual(decision.category, "dangerous_shell")

    def test_explicit_deny_rule_wins_before_user_approval(self) -> None:
        policy = ExecPolicy([ExecPolicyRule.deny("npm", "publish", category="publish_blocked")])

        decision = policy.decide("npm publish", approved=True)

        self.assertEqual(decision.action, "deny")
        self.assertEqual(decision.category, "publish_blocked")
        self.assertEqual(decision.matched_prefix_rule, ("npm", "publish"))

    def test_explicit_ask_rule_allows_after_one_time_approval(self) -> None:
        policy = ExecPolicy([ExecPolicyRule.ask("pnpm", "run", "build")])

        initial = policy.decide("pnpm run build")
        approved = policy.decide("pnpm run build", approved=True)

        self.assertTrue(initial.requires_approval)
        self.assertEqual(initial.matched_prefix_rule, ("pnpm", "run", "build"))
        self.assertTrue(approved.allowed)
        self.assertFalse(approved.approval_required)

    def test_approval_policy_never_turns_ask_into_deny(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = SecurityPolicy(Path(tmp), approval_policy=ApprovalPolicy.NEVER)

            decision = policy.check_command("ruff check .")

        self.assertEqual(decision.action, "deny")
        self.assertEqual(decision.category, "approval_unavailable")


if __name__ == "__main__":
    unittest.main()
