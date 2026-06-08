from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from server.app import build_system_prompt
from server.runtime.session_state import create_websocket_session, persist_websocket_session
from security import PermissionProfile
from workspace import (
    AdditionalRoot,
    TrustLevel,
    WorkspaceContext,
    WorkspaceManager,
    WorkspaceTrust,
    WorkspaceValidationError,
    load_project_instructions,
    render_system_prompt_with_project_instructions,
    validate_workspace_path,
)
from workspace.trust import WorkspaceTrustStore


def _trust_payload(project_root: Path, level: str = "session_only") -> dict[str, object]:
    return {
        "level": level,
        "trust_key": project_root.resolve().as_posix(),
        "source": "user_config" if level == "trusted" else "session",
        "project_config_enabled": level == "trusted",
    }


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

    def test_workspace_payload_contains_v2_snapshot_fields(self) -> None:
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

    def test_change_current_dir_accepts_authorized_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "pkg"
            nested.mkdir()
            workspace = WorkspaceManager(root).open()

            changed = workspace.change_current_dir("pkg")

            self.assertEqual(changed, nested.resolve())
            self.assertEqual(workspace.as_dict()["current_dir"], nested.resolve().as_posix())

    def test_change_current_dir_rejects_unopened_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            external = Path(tmp) / "external"
            root.mkdir()
            external.mkdir()
            workspace = WorkspaceManager(root).open()

            with self.assertRaises(WorkspaceValidationError):
                workspace.change_current_dir(external)

    def test_additional_root_can_be_added_and_serialized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            external = Path(tmp) / "external"
            root.mkdir()
            external.mkdir()
            workspace = WorkspaceManager(root).open()

            added = workspace.add_additional_root(external, "read")
            upgraded = workspace.add_additional_root(external, "write")
            payload = workspace.as_dict()

            self.assertEqual(added.path, external.resolve())
            self.assertEqual(upgraded.access, "write")
            self.assertEqual(len(workspace.additional_roots), 1)
            self.assertEqual(payload["additional_roots"][0]["path"], external.resolve().as_posix())
            self.assertEqual(payload["additional_roots"][0]["access"], "write")

    def test_current_dir_can_enter_explicit_additional_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            external = Path(tmp) / "external"
            nested = external / "docs"
            root.mkdir()
            nested.mkdir(parents=True)
            workspace = WorkspaceManager(root).open()
            workspace.add_additional_root(external, "read")

            changed = workspace.change_current_dir(nested)

            self.assertEqual(changed, nested.resolve())

    def test_additional_root_rejects_workspace_internal_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "pkg"
            nested.mkdir()
            workspace = WorkspaceManager(root).open()

            with self.assertRaises(WorkspaceValidationError):
                workspace.add_additional_root(nested, "read")

    def test_workspace_manager_restores_snapshot_with_revalidated_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "workspace"
            nested = selected / "pkg"
            external = Path(tmp) / "external"
            selected.mkdir()
            nested.mkdir()
            external.mkdir()
            snapshot = {
                "selected_root": selected.as_posix(),
                "project_root": selected.as_posix(),
                "current_dir": nested.as_posix(),
                "trust": _trust_payload(selected),
                "additional_roots": [{"path": external.as_posix(), "access": "write"}],
            }

            workspace = WorkspaceManager(selected).restore_from_snapshot(snapshot)

            self.assertEqual(workspace.selected_root, selected.resolve())
            self.assertEqual(workspace.current_dir, nested.resolve())
            self.assertEqual(workspace.additional_roots[0].path, external.resolve())
            self.assertEqual(workspace.additional_roots[0].access, "write")

    def test_workspace_manager_restore_rejects_missing_current_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "workspace"
            selected.mkdir()
            snapshot = {
                "selected_root": selected.as_posix(),
                "project_root": selected.as_posix(),
                "trust": _trust_payload(selected),
                "additional_roots": [],
            }

            with self.assertRaisesRegex(WorkspaceValidationError, "current_dir"):
                WorkspaceManager(selected).restore_from_snapshot(snapshot)

    def test_workspace_manager_restore_rejects_missing_additional_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "workspace"
            selected.mkdir()
            snapshot = {
                "selected_root": selected.as_posix(),
                "project_root": selected.as_posix(),
                "current_dir": selected.as_posix(),
                "trust": _trust_payload(selected),
            }

            with self.assertRaisesRegex(WorkspaceValidationError, "additional_roots"):
                WorkspaceManager(selected).restore_from_snapshot(snapshot)

    def test_workspace_manager_restore_rejects_invalid_additional_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "workspace"
            missing = Path(tmp) / "missing"
            selected.mkdir()
            snapshot = {
                "selected_root": selected.as_posix(),
                "project_root": selected.as_posix(),
                "current_dir": selected.as_posix(),
                "trust": _trust_payload(selected),
                "additional_roots": [{"path": missing.as_posix(), "access": "read"}],
            }

            with self.assertRaisesRegex(WorkspaceValidationError, "additional root 不可用"):
                WorkspaceManager(selected).restore_from_snapshot(snapshot)

    def test_workspace_manager_ignores_project_config_until_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / ".codex-mini"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                "[permissions]\nprofile = \"read_only\"\n",
                encoding="utf-8",
            )

            workspace = WorkspaceManager(root).open()

            self.assertEqual(workspace.trust.level, TrustLevel.SESSION_ONLY)
            assert workspace.project_policy is not None
            self.assertEqual(workspace.project_policy.source, "defaults")
            self.assertEqual(workspace.project_policy.permission_profile, PermissionProfile.WORKSPACE_WRITE)

    def test_trusted_workspace_loads_project_policy_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            trust_path = Path(tmp) / "trust.json"
            root.mkdir()
            config_dir = root / ".codex-mini"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                """
[permissions]
profile = "read_only"
approval_policy = "never"

[[exec.rules]]
action = "deny"
prefix = ["npm", "publish"]
category = "publish_blocked"
""".strip(),
                encoding="utf-8",
            )
            trust_store = WorkspaceTrustStore(trust_path)
            trust_store.mark_trusted(root)

            workspace = WorkspaceManager(root, trust_store=trust_store).open()
            _session_id, _state, _registry, runner, _history = create_websocket_session(
                workspace,
                build_system_prompt(),
            )

            self.assertEqual(workspace.trust.level, TrustLevel.TRUSTED)
            assert workspace.project_policy is not None
            self.assertEqual(workspace.project_policy.source, "project_config")
            self.assertEqual(runner.ctx.permission_profile, PermissionProfile.READ_ONLY)
            self.assertEqual(runner.ctx.policy.check_command("npm publish").category, "publish_blocked")

    def test_trusted_project_config_rejects_sensitive_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            trust_path = Path(tmp) / "trust.json"
            root.mkdir()
            config_dir = root / ".codex-mini"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                "[model]\nprovider = \"deepseek\"\n",
                encoding="utf-8",
            )
            trust_store = WorkspaceTrustStore(trust_path)
            trust_store.mark_trusted(root)

            with self.assertRaisesRegex(WorkspaceValidationError, "不支持的顶层字段|敏感字段"):
                WorkspaceManager(root, trust_store=trust_store).open()

    def test_trusted_project_config_rejects_broad_exec_allow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            trust_path = Path(tmp) / "trust.json"
            root.mkdir()
            config_dir = root / ".codex-mini"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                """
[[exec.rules]]
action = "allow"
prefix = ["python"]
""".strip(),
                encoding="utf-8",
            )
            trust_store = WorkspaceTrustStore(trust_path)
            trust_store.mark_trusted(root)

            with self.assertRaisesRegex(WorkspaceValidationError, "过宽 exec allow"):
                WorkspaceManager(root, trust_store=trust_store).open()

    def test_resume_does_not_silently_upgrade_session_only_trust(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            trust_path = Path(tmp) / "trust.json"
            root.mkdir()
            config_dir = root / ".codex-mini"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                "[permissions]\nprofile = \"read_only\"\n",
                encoding="utf-8",
            )
            trust_store = WorkspaceTrustStore(trust_path)
            trust_store.mark_trusted(root)
            snapshot = {
                "selected_root": root.as_posix(),
                "project_root": root.as_posix(),
                "current_dir": root.as_posix(),
                "trust": _trust_payload(root, "session_only"),
                "additional_roots": [],
            }

            workspace = WorkspaceManager(root, trust_store=trust_store).restore_from_snapshot(snapshot)

            self.assertEqual(workspace.trust.level, TrustLevel.SESSION_ONLY)
            assert workspace.project_policy is not None
            self.assertEqual(workspace.project_policy.source, "defaults")

    def test_resume_trusted_snapshot_requires_current_trust_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            snapshot = {
                "selected_root": root.as_posix(),
                "project_root": root.as_posix(),
                "current_dir": root.as_posix(),
                "trust": _trust_payload(root, "trusted"),
                "additional_roots": [],
            }

            with self.assertRaisesRegex(WorkspaceValidationError, "未被信任"):
                WorkspaceManager(root, trust_store=WorkspaceTrustStore(Path(tmp) / "trust.json")).restore_from_snapshot(
                    snapshot
                )

    def test_project_instructions_load_from_project_root_to_current_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "pkg" / "app"
            nested.mkdir(parents=True)
            (root / "AGENTS.md").write_text("root rules", encoding="utf-8")
            (root / "pkg" / "AGENTS.md").write_text("pkg rules", encoding="utf-8")
            workspace = WorkspaceManager(root).open()
            workspace.change_current_dir(nested)

            instructions = load_project_instructions(workspace)
            prompt, _instructions = render_system_prompt_with_project_instructions(
                "base prompt",
                workspace,
            )

            self.assertEqual(
                [path.relative_to(root.resolve()).as_posix() for path in instructions.paths],
                ["AGENTS.md", "pkg/AGENTS.md"],
            )
            self.assertIn("base prompt", prompt)
            self.assertIn("root rules", prompt)
            self.assertIn("pkg rules", prompt)

    def test_project_instructions_do_not_cross_project_root_for_external_current_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "workspace"
            external = Path(tmp) / "external"
            selected.mkdir()
            external.mkdir()
            (selected / "AGENTS.md").write_text("workspace rules", encoding="utf-8")
            (external / "AGENTS.md").write_text("external rules", encoding="utf-8")
            workspace = WorkspaceManager(selected).open()
            workspace.add_additional_root(external, "read")
            workspace.change_current_dir(external)

            prompt, instructions = render_system_prompt_with_project_instructions(
                "base prompt",
                workspace,
            )

            self.assertEqual(instructions.paths, ((selected / "AGENTS.md").resolve(),))
            self.assertIn("workspace rules", prompt)
            self.assertNotIn("external rules", prompt)

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

    def test_create_websocket_session_applies_additional_roots_to_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "workspace"
            external = Path(tmp) / "external"
            selected.mkdir()
            external.mkdir()
            (external / "notes.txt").write_text("hello", encoding="utf-8")
            workspace = WorkspaceContext(
                selected_root=selected,
                project_root=selected,
                current_dir=selected,
                display_name="workspace",
                trust=WorkspaceTrust.session_only(selected),
                additional_roots=[AdditionalRoot(external, "read")],
                session_store=WorkspaceManager(selected).open().session_store,
            )

            _session_id, _session_state, _registry, runner, _history = create_websocket_session(
                workspace,
                build_system_prompt(),
            )

            read_result = runner.execute(
                "read_file",
                json.dumps({"path": (external / "notes.txt").as_posix()}),
            )
            write_result = runner.execute(
                "write_file",
                json.dumps({"path": (external / "created.txt").as_posix(), "content": "x"}),
                approved=True,
            )

            self.assertTrue(read_result.ok)
            self.assertEqual(read_result.content, "hello")
            self.assertFalse(write_result.ok)


if __name__ == "__main__":
    unittest.main()
