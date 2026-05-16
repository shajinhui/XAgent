from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server.app import build_system_prompt
from server.runtime.session_state import create_websocket_session, persist_websocket_session
from workspace import TrustLevel, WorkspaceManager, WorkspaceValidationError, validate_workspace_path


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

            self.assertEqual(workspace.selected_root, nested.resolve())
            self.assertEqual(workspace.project_root, root.resolve())
            self.assertEqual(workspace.current_dir, nested.resolve())
            self.assertEqual(workspace.git_root, root.resolve())
            self.assertEqual(workspace.trust.level, TrustLevel.SESSION_ONLY)
            self.assertEqual(workspace.trust.trust_key, root.resolve().as_posix())
            self.assertIsNotNone(workspace.session_store)
            assert workspace.session_store is not None
            self.assertEqual(workspace.session_store.project_root, root.resolve())

    def test_workspace_manager_uses_selected_root_for_non_git_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp)

            workspace = WorkspaceManager(selected).open()

            self.assertEqual(workspace.selected_root, selected.resolve())
            self.assertEqual(workspace.project_root, selected.resolve())
            self.assertEqual(workspace.current_dir, selected.resolve())
            self.assertIsNone(workspace.git_root)

    def test_workspace_payload_contains_v2_snapshot_and_compat_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "packages" / "app"
            nested.mkdir(parents=True)
            (root / ".git").mkdir()

            workspace = WorkspaceManager(nested).open()
            payload = workspace.as_dict()

            self.assertEqual(payload["selected_root"], nested.resolve().as_posix())
            self.assertEqual(payload["project_root"], root.resolve().as_posix())
            self.assertEqual(payload["current_dir"], nested.resolve().as_posix())
            self.assertEqual(payload["git_root"], root.resolve().as_posix())
            self.assertEqual(payload["trust"]["level"], "session_only")
            self.assertEqual(payload["trust"]["trust_key"], root.resolve().as_posix())
            self.assertEqual(payload["additional_roots"], [])

    def test_create_websocket_session_binds_registry_to_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceManager(Path(tmp)).open()

            session_id, session_state, registry, runner, history = create_websocket_session(
                workspace,
                build_system_prompt(),
            )

            self.assertEqual(session_state.session_id, session_id)
            self.assertTrue(registry.has_tool("read_file"))
            self.assertEqual(runner.ctx.selected_root, workspace.selected_root)
            self.assertEqual(runner.ctx.project_root, workspace.project_root)
            self.assertEqual(history.messages[0]["role"], "system")
            self.assertIsNotNone(workspace.session_store)
            assert workspace.session_store is not None

            with self.assertRaises(KeyError):
                workspace.session_store.get_session(session_id)

            record = persist_websocket_session(workspace.session_store, session_id, workspace)
            self.assertEqual(
                record.metadata["workspace"]["selected_root"],
                workspace.selected_root.as_posix(),
            )
            self.assertEqual(
                record.metadata["workspace"]["project_root"],
                workspace.project_root.as_posix(),
            )
            self.assertEqual(
                record.metadata["workspace"]["trust"]["level"],
                TrustLevel.SESSION_ONLY.value,
            )


if __name__ == "__main__":
    unittest.main()
