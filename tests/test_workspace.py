from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server.app import build_system_prompt, create_websocket_session
from workspace import WorkspaceManager, WorkspaceValidationError, validate_workspace_path


class WorkspaceValidationTests(unittest.TestCase):
    def test_validate_workspace_accepts_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = validate_workspace_path(tmp)

            self.assertEqual(root, Path(tmp).resolve())

    def test_validate_workspace_rejects_missing_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"

            with self.assertRaises(WorkspaceValidationError):
                validate_workspace_path(missing)

    def test_validate_workspace_rejects_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "notes.txt"
            file_path.write_text("hello", encoding="utf-8")

            with self.assertRaises(WorkspaceValidationError):
                validate_workspace_path(file_path)

    def test_validate_workspace_rejects_root_directory(self) -> None:
        with self.assertRaises(WorkspaceValidationError):
            validate_workspace_path("/")

    def test_validate_workspace_rejects_home_root(self) -> None:
        with self.assertRaises(WorkspaceValidationError):
            validate_workspace_path(Path.home())

    def test_validate_workspace_rejects_git_internal_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            git_objects = Path(tmp) / ".git" / "objects"
            git_objects.mkdir(parents=True)

            with self.assertRaises(WorkspaceValidationError):
                validate_workspace_path(git_objects)

    def test_workspace_manager_detects_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "packages" / "app"
            nested.mkdir(parents=True)
            (root / ".git").mkdir()

            workspace = WorkspaceManager(nested).open()

            self.assertEqual(workspace.root, nested.resolve())
            self.assertEqual(workspace.current_dir, nested.resolve())
            self.assertEqual(workspace.git_root, root.resolve())
            self.assertIsNotNone(workspace.session_store)

    def test_create_websocket_session_binds_registry_to_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceManager(Path(tmp)).open()

            session_id, session_state, registry, messages = create_websocket_session(
                workspace,
                build_system_prompt(),
            )

            self.assertEqual(session_state.session_id, session_id)
            self.assertEqual(registry.ctx.project_root, workspace.root)
            self.assertEqual(messages[0]["role"], "system")
            self.assertIsNotNone(workspace.session_store)
            assert workspace.session_store is not None
            record = workspace.session_store.get_session(session_id)
            self.assertEqual(record.metadata["workspace"]["root"], workspace.root.as_posix())


if __name__ == "__main__":
    unittest.main()
