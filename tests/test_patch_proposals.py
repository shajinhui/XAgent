from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from patch import (
    PatchChangeType,
    PatchStatus,
    PatchStore,
    build_file_change,
    build_patch_proposal,
)


class PatchProposalTests(unittest.TestCase):
    def test_build_update_change_contains_unified_diff_and_counts(self) -> None:
        change = build_file_change(
            "src/example.py",
            "print('old')\nkeep\n",
            "print('new')\nkeep\nadded\n",
        )

        self.assertEqual(change.change_type, PatchChangeType.UPDATE)
        self.assertIn("--- a/src/example.py", change.unified_diff)
        self.assertIn("+++ b/src/example.py", change.unified_diff)
        self.assertIn("-print('old')", change.unified_diff)
        self.assertIn("+print('new')", change.unified_diff)
        self.assertEqual(change.additions, 2)
        self.assertEqual(change.deletions, 1)

    def test_build_add_and_delete_changes_use_dev_null_labels(self) -> None:
        added = build_file_change(
            "docs/new.md",
            "",
            "hello\n",
            existed_before=False,
            exists_after=True,
        )
        deleted = build_file_change(
            "docs/old.md",
            "bye\n",
            "",
            existed_before=True,
            exists_after=False,
        )

        self.assertEqual(added.change_type, PatchChangeType.ADD)
        self.assertIn("--- /dev/null", added.unified_diff)
        self.assertIn("+++ b/docs/new.md", added.unified_diff)
        self.assertEqual(added.additions, 1)
        self.assertEqual(added.deletions, 0)
        self.assertEqual(deleted.change_type, PatchChangeType.DELETE)
        self.assertIn("--- a/docs/old.md", deleted.unified_diff)
        self.assertIn("+++ /dev/null", deleted.unified_diff)
        self.assertEqual(deleted.additions, 0)
        self.assertEqual(deleted.deletions, 1)

    def test_patch_proposal_round_trips_through_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            change = build_file_change("app.py", "a = 1\n", "a = 2\n")
            proposal = build_patch_proposal(
                patch_id="patch-1",
                session_id="session-1",
                turn_id="turn-1",
                cwd=Path(tmp),
                changes=[change],
                summary="更新 app.py",
                created_at=123.0,
            )
            store = PatchStore(Path(tmp) / "patches")

            path = store.save(proposal)
            loaded = store.load("patch-1")

            self.assertTrue(path.exists())
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.patch_id, "patch-1")
            self.assertEqual(loaded.changed_paths, ["app.py"])
            self.assertEqual(loaded.unified_diff, proposal.unified_diff)
            self.assertEqual(loaded.summary, "更新 app.py")

    def test_patch_store_filters_and_updates_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PatchStore(Path(tmp) / "patches")
            first = build_patch_proposal(
                patch_id="patch-a",
                session_id="session-a",
                turn_id="turn-a",
                cwd=tmp,
                changes=[build_file_change("a.txt", "1\n", "2\n")],
                created_at=2.0,
            )
            second = build_patch_proposal(
                patch_id="patch-b",
                session_id="session-b",
                turn_id="turn-b",
                cwd=tmp,
                changes=[build_file_change("b.txt", "1\n", "2\n")],
                created_at=1.0,
            ).with_status(PatchStatus.APPLIED, applied_at=3.0)
            store.save(first)
            store.save(second)

            self.assertEqual([item.patch_id for item in store.list()], ["patch-b", "patch-a"])
            self.assertEqual([item.patch_id for item in store.list(session_id="session-a")], ["patch-a"])
            self.assertEqual([item.patch_id for item in store.list(status=PatchStatus.APPLIED)], ["patch-b"])
            loaded_second = store.load("patch-b")
            self.assertIsNotNone(loaded_second)
            assert loaded_second is not None
            self.assertEqual(loaded_second.metadata["applied_at"], 3.0)

    def test_patch_store_rejects_path_traversal_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PatchStore(Path(tmp) / "patches")

            with self.assertRaises(ValueError):
                store.load("../escape")

    def test_empty_patch_proposal_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_patch_proposal(
                session_id="session",
                turn_id="turn",
                cwd="/tmp",
                changes=[],
            )


if __name__ == "__main__":
    unittest.main()
