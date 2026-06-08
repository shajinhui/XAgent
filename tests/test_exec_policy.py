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
