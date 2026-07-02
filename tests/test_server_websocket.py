from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.app import app
from server.runtime.transcript_events import (
    assistant_transcript_payload,
    record_transcript_event,
)
from server.runtime.websocket_context import WebSocketRuntimeContext


def _write_skill(
    directory: Path,
    name: str = "demo",
    body: str = "Use demo.",
    *,
    dependencies_tools: list[str] | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    dependencies = ""
    if dependencies_tools:
        tools = "\n".join(f"    - {tool}" for tool in dependencies_tools)
        dependencies = f"dependencies:\n  tools:\n{tools}\n"
    path.write_text(
        (
            "---\n"
            f"name: {name}\n"
            "description: Use when testing websocket skills.\n"
            f"{dependencies}"
            "---\n\n"
            f"# {name}\n\n{body}\n"
        ),
        encoding="utf-8",
    )
    return path


class _FakeUrlResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def __enter__(self) -> "_FakeUrlResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return self._data


def _github_skill_zip(body: str = "Use it.", package_path: str = "skills/demo") -> bytes:
    buffer = io.BytesIO()
    skill_body = "\n".join(
        [
            "---",
            "name: websocket-github",
            "description: Use installed websocket skills.",
            "---",
            "",
            body,
        ]
    )
    with zipfile.ZipFile(buffer, "w") as archive:
        prefix = "repo-main/" + package_path.strip("/")
        archive.writestr(prefix + "/SKILL.md", skill_body + "\n")
        archive.writestr(prefix + "/references/guide.md", "GitHub websocket guide")
    return buffer.getvalue()


def _github_api_payload(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def _github_skill_content_payload(contents: str) -> bytes:
    encoded = base64.b64encode(contents.encode("utf-8")).decode("ascii")
    return _github_api_payload({"encoding": "base64", "content": encoded})


class ServerWebSocketTests(unittest.TestCase):
    def test_control_packets_do_not_persist_empty_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(Path(tmp), system_prompt)
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with TestClient(app) as client:
                    with client.websocket_connect("/agent/ws") as ws:
                        ready = ws.receive_json()
                        ws.send_json({"type": "new_session", "request_id": "new-1"})
                        created = ws.receive_json()
                        ws.send_text("{")
                        error = ws.receive_json()

            self.assertEqual(ready["type"], "ready")
            self.assertEqual(created["type"], "session_created")
            self.assertEqual(error["type"], "error")
            self.assertEqual(error["received_type"], "invalid_json")
            self.assertEqual(contexts[-1].session_store.list_sessions(), [])

    def test_ready_workspace_payload_contains_v2_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(Path(tmp), system_prompt)
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with TestClient(app) as client:
                    with client.websocket_connect("/agent/ws") as ws:
                        ready = ws.receive_json()

            workspace = ready["workspace"]
            self.assertEqual(workspace["selected_root"], Path(tmp).resolve().as_posix())
            self.assertEqual(workspace["project_root"], Path(tmp).resolve().as_posix())
            self.assertEqual(workspace["trust"]["level"], "session_only")
            self.assertEqual(workspace["additional_roots"], [])
            self.assertEqual(contexts[-1].workspace.project_root, Path(tmp).resolve())

    def test_open_workspace_payload_separates_selected_and_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            default_root = Path(tmp) / "default"
            default_root.mkdir()
            repo_root = Path(tmp) / "repo"
            nested = repo_root / "packages" / "app"
            nested.mkdir(parents=True)
            (repo_root / ".git").mkdir()

            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(default_root, system_prompt)
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with TestClient(app) as client:
                    with client.websocket_connect("/agent/ws") as ws:
                        ready = ws.receive_json()
                        ws.send_json(
                            {
                                "type": "open_workspace",
                                "path": nested.as_posix(),
                                "request_id": "workspace-1",
                            }
                        )
                        changed = ws.receive_json()

            self.assertEqual(ready["type"], "ready")
            self.assertEqual(changed["type"], "workspace_changed")
            self.assertEqual(changed["workspace"]["selected_root"], nested.resolve().as_posix())
            self.assertEqual(changed["workspace"]["project_root"], repo_root.resolve().as_posix())
            self.assertEqual(changed["workspace"]["git_root"], repo_root.resolve().as_posix())
            self.assertEqual(changed["previous_workspace"]["selected_root"], default_root.resolve().as_posix())
            self.assertEqual(contexts[-1].workspace.project_root, repo_root.resolve())

    def test_first_user_input_persists_session_and_returns_final_answer(self) -> None:
        async def fake_run_turn(
            ws,
            session_store,
            turn_context,
        ):
            assistant = {"role": "assistant", "content": "pong"}
            turn_context.history.append_assistant_message(assistant)
            record_transcript_event(
                session_store,
                turn_context.session_id,
                "assistant_message",
                assistant_transcript_payload(assistant, turn_context.turn_id),
            )
            return turn_context.history

        with tempfile.TemporaryDirectory() as tmp:
            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(Path(tmp), system_prompt)
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with patch("server.app.run_turn", side_effect=fake_run_turn):
                    with TestClient(app) as client:
                        with client.websocket_connect("/agent/ws") as ws:
                            ready = ws.receive_json()
                            ws.send_json(
                                {
                                    "type": "user_input",
                                    "content": "ping",
                                    "model": "openai/gpt-4o-mini",
                                    "reasoning_effort": "off",
                                }
                            )
                            turn_started = ws.receive_json()
                            task_list = ws.receive_json()
                            final_answer = ws.receive_json()

            context = contexts[-1]
            record = context.session_store.get_session(ready["session_id"])
            events = context.session_store.load_events(record.session_id)

            self.assertEqual(turn_started["type"], "turn_started")
            self.assertEqual(task_list["type"], "task_list")
            self.assertEqual(final_answer["type"], "final_answer")
            self.assertEqual(final_answer["content"], "pong")
            self.assertEqual(
                [event.type for event in events],
                [
                    "session_started",
                    "user_message",
                    "turn_started",
                    "task_list",
                    "assistant_message",
                    "final_answer",
                ],
            )

    def test_list_skills_returns_current_workspace_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_path = _write_skill(
                root / ".agents" / "skills" / "demo",
                name="demo",
                dependencies_tools=["read_file", "missing_tool"],
            )
            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(root, system_prompt)
                context.skill_manager.user_skill_root = root / "empty-user-skills"
                context.skill_manager.clear_cache()
                context.refresh_history_system_prompt()
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with TestClient(app) as client:
                    with client.websocket_connect("/agent/ws") as ws:
                        ready = ws.receive_json()
                        ws.send_json(
                            {
                                "type": "list_skills",
                                "force_reload": True,
                                "request_id": "skills-1",
                            }
                        )
                        listed = ws.receive_json()

            self.assertEqual(ready["type"], "ready")
            self.assertEqual(listed["type"], "skills_listed")
            self.assertEqual(listed["request_id"], "skills-1")
            self.assertEqual(listed["workspace"]["selected_root"], root.resolve().as_posix())
            self.assertEqual(
                [(skill["name"], skill["path"]) for skill in listed["skills"]],
                [("demo", skill_path.resolve().as_posix())],
            )
            dependency_status = listed["skills"][0]["dependency_status"]
            self.assertEqual(
                dependency_status["tools"],
                [
                    {"name": "read_file", "available": True},
                    {"name": "missing_tool", "available": False},
                ],
            )
            self.assertEqual(dependency_status["missing_tools"], ["missing_tool"])
            self.assertEqual(listed["errors"], [])
            self.assertEqual(contexts[-1].session_store.list_sessions(), [])

    def test_skill_management_control_events_update_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            import_source = root / "imports" / "websocket-skill"
            _write_skill(import_source, name="imported-websocket", body="Use imported websocket skill.")
            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(root, system_prompt)
                context.skill_manager.user_skill_root = root / "empty-user-skills"
                context.skill_manager.clear_cache()
                context.refresh_history_system_prompt()
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with TestClient(app) as client:
                    with client.websocket_connect("/agent/ws") as ws:
                        ready = ws.receive_json()
                        ws.send_json(
                            {
                                "type": "create_skill",
                                "request_id": "skill-create-1",
                                "scope": "repo",
                                "name": "managed-skill",
                                "description": "Use when testing skill management.",
                                "short_description": "Managed skill.",
                                "allow_implicit_invocation": False,
                                "content": "## Instructions\n\n- Follow managed websocket steps.",
                                "package_template": "standard",
                            }
                        )
                        created = ws.receive_json()
                        skill_path = created["skill"]["path"]
                        self.assertTrue((Path(skill_path).parent / "references" / "README.md").exists())

                        ws.send_json(
                            {
                                "type": "list_skill_resources",
                                "request_id": "skill-resources-1",
                                "path": skill_path,
                            }
                        )
                        resources_listed = ws.receive_json()

                        ws.send_json(
                            {
                                "type": "get_skill_resource",
                                "request_id": "skill-resource-load-1",
                                "path": skill_path,
                                "resource": "references/README.md",
                            }
                        )
                        resource_loaded = ws.receive_json()

                        ws.send_json(
                            {
                                "type": "save_skill_resource",
                                "request_id": "skill-resource-save-1",
                                "path": skill_path,
                                "resource": "templates/websocket.md",
                                "content": "WebSocket template.",
                            }
                        )
                        resource_saved = ws.receive_json()

                        ws.send_json(
                            {
                                "type": "delete_skill_resource",
                                "request_id": "skill-resource-delete-1",
                                "path": skill_path,
                                "resource": "templates/websocket.md",
                            }
                        )
                        resource_deleted = ws.receive_json()

                        ws.send_json(
                            {
                                "type": "get_skill",
                                "request_id": "skill-load-1",
                                "path": skill_path,
                            }
                        )
                        loaded = ws.receive_json()

                        ws.send_json(
                            {
                                "type": "update_skill",
                                "request_id": "skill-update-1",
                                "path": skill_path,
                                "name": "managed-skill",
                                "description": "Use after websocket update.",
                                "short_description": "Updated managed skill.",
                                "allow_implicit_invocation": True,
                                "content": "## Instructions\n\n- Follow updated websocket steps.",
                            }
                        )
                        updated = ws.receive_json()

                        ws.send_json(
                            {
                                "type": "delete_skill",
                                "request_id": "skill-delete-1",
                                "path": skill_path,
                            }
                        )
                        deleted = ws.receive_json()

                        ws.send_json(
                            {
                                "type": "import_skill",
                                "request_id": "skill-import-1",
                                "scope": "repo",
                                "source_path": import_source.as_posix(),
                            }
                        )
                        imported = ws.receive_json()

            self.assertEqual(ready["type"], "ready")
            self.assertEqual(created["type"], "skill_saved")
            self.assertEqual(created["action"], "create")
            self.assertEqual(created["skill"]["name"], "managed-skill")
            self.assertFalse(created["skill"]["policy"]["allow_implicit_invocation"])
            self.assertEqual(resources_listed["type"], "skill_resources_listed")
            self.assertIn(
                "references/README.md",
                [item["resource"] for item in resources_listed["resources"]],
            )
            self.assertEqual(resource_loaded["type"], "skill_resource_loaded")
            self.assertEqual(resource_loaded["resource"]["resource"], "references/README.md")
            self.assertIn("Add detailed guidance", resource_loaded["content"])
            self.assertEqual(resource_saved["type"], "skill_resource_saved")
            self.assertIn(
                "templates/websocket.md",
                [item["resource"] for item in resource_saved["resources"]],
            )
            self.assertEqual(resource_deleted["type"], "skill_resource_deleted")
            self.assertNotIn(
                "templates/websocket.md",
                [item["resource"] for item in resource_deleted["resources"]],
            )
            self.assertEqual(loaded["type"], "skill_loaded")
            self.assertIn("Follow managed websocket steps.", loaded["content"])
            self.assertEqual(updated["type"], "skill_saved")
            self.assertEqual(updated["action"], "update")
            self.assertTrue(updated["skill"]["policy"]["allow_implicit_invocation"])
            self.assertEqual(deleted["type"], "skill_deleted")
            self.assertEqual(deleted["skills"], [])
            self.assertEqual(imported["type"], "skill_saved")
            self.assertEqual(imported["action"], "import")
            self.assertEqual(imported["skill"]["name"], "imported-websocket")
            self.assertEqual(imported["skill"]["install"]["source_type"], "local")
            self.assertEqual(contexts[-1].session_store.list_sessions(), [])

    def test_skill_install_control_event_installs_github_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(root, system_prompt)
                context.skill_manager.user_skill_root = root / "empty-user-skills"
                context.skill_manager.clear_cache()
                context.refresh_history_system_prompt()
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with patch("skills.installer.urlopen", return_value=_FakeUrlResponse(_github_skill_zip())):
                    with TestClient(app) as client:
                        with client.websocket_connect("/agent/ws") as ws:
                            ready = ws.receive_json()
                            ws.send_json(
                                {
                                    "type": "install_skill",
                                    "request_id": "skill-install-1",
                                    "scope": "repo",
                                    "source_type": "github",
                                    "source": "https://github.com/acme/repo/tree/main/skills/demo",
                                }
                            )
                            installed = ws.receive_json()
                            with patch(
                                "skills.installer.urlopen",
                                return_value=_FakeUrlResponse(_github_skill_zip("Updated websocket body.")),
                            ):
                                ws.send_json(
                                    {
                                        "type": "reinstall_skill",
                                        "request_id": "skill-reinstall-1",
                                        "path": installed["skill"]["path"],
                                    }
                                )
                                reinstalled = ws.receive_json()

            self.assertEqual(ready["type"], "ready")
            self.assertEqual(installed["type"], "skill_saved")
            self.assertEqual(installed["action"], "install")
            self.assertEqual(installed["skill"]["name"], "websocket-github")
            self.assertEqual(installed["skill"]["install"]["source_type"], "github")
            self.assertEqual(reinstalled["type"], "skill_saved")
            self.assertEqual(reinstalled["action"], "reinstall")
            self.assertEqual(reinstalled["skill"]["install"]["source_type"], "github")
            self.assertEqual(contexts[-1].session_store.list_sessions(), [])

    def test_skill_registry_control_event_lists_and_installs_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contexts = []
            original_create = WebSocketRuntimeContext.create
            listing = [{"name": "demo", "path": "skills/.curated/demo", "type": "dir"}]
            skill_md = "\n".join(
                [
                    "---",
                    "name: websocket-registry",
                    "description: Use when installing registry skills.",
                    "dependencies:",
                    "  tools:",
                    "    - read_file",
                    "    - missing_registry_tool",
                    "---",
                    "",
                    "Use it.",
                ]
            )

            def create_temp_context(project_root, system_prompt):
                context = original_create(root, system_prompt)
                context.skill_manager.user_skill_root = root / "empty-user-skills"
                context.skill_manager.clear_cache()
                context.refresh_history_system_prompt()
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with patch(
                    "skills.registry.urlopen",
                    side_effect=[
                        _FakeUrlResponse(_github_api_payload(listing)),
                        _FakeUrlResponse(_github_skill_content_payload(skill_md)),
                    ],
                ):
                    with patch(
                        "skills.installer.urlopen",
                        return_value=_FakeUrlResponse(
                            _github_skill_zip(package_path="skills/.curated/demo")
                        ),
                    ):
                        with TestClient(app) as client:
                            with client.websocket_connect("/agent/ws") as ws:
                                ready = ws.receive_json()
                                ws.send_json(
                                    {
                                        "type": "list_installable_skills",
                                        "request_id": "installable-skills-1",
                                    }
                                )
                                listed = ws.receive_json()
                                ws.send_json(
                                    {
                                        "type": "install_registry_skill",
                                        "request_id": "skill-registry-install-1",
                                        "scope": "repo",
                                        "id": "demo",
                                    }
                                )
                                installed = ws.receive_json()
                                ws.send_json(
                                    {
                                        "type": "list_installable_skills",
                                        "request_id": "installable-skills-2",
                                    }
                                )
                                listed_after_install = ws.receive_json()

            self.assertEqual(ready["type"], "ready")
            self.assertEqual(listed["type"], "installable_skills_listed")
            self.assertEqual(listed["installable_skills"][0]["id"], "demo")
            self.assertFalse(listed["installable_skills"][0]["installed"])
            self.assertEqual(
                listed["installable_skills"][0]["dependency_status"]["missing_tools"],
                ["missing_registry_tool"],
            )
            self.assertEqual(listed["errors"], [])
            self.assertEqual(installed["type"], "skill_saved")
            self.assertEqual(installed["action"], "install_registry")
            self.assertEqual(installed["skill"]["name"], "websocket-github")
            self.assertEqual(
                installed["skill"]["install"]["source"],
                "https://github.com/openai/skills/tree/main/skills/.curated/demo",
            )
            self.assertEqual(listed_after_install["type"], "installable_skills_listed")
            self.assertTrue(listed_after_install["installable_skills"][0]["installed"])
            self.assertFalse(listed_after_install["installable_skills"][0]["update_available"])
            self.assertEqual(contexts[-1].session_store.list_sessions(), [])

    def test_explicit_skill_injection_is_temporary_for_current_turn(self) -> None:
        observed_contexts = []

        async def fake_run_turn(
            ws,
            session_store,
            turn_context,
        ):
            observed_contexts.append(turn_context)
            assistant = {"role": "assistant", "content": "used skill"}
            turn_context.history.append_assistant_message(assistant)
            record_transcript_event(
                session_store,
                turn_context.session_id,
                "assistant_message",
                assistant_transcript_payload(assistant, turn_context.turn_id),
            )
            return turn_context.history

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_path = _write_skill(
                root / ".agents" / "skills" / "demo",
                name="demo",
                body="Follow websocket skill steps.",
            )
            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(root, system_prompt)
                context.skill_manager.user_skill_root = root / "empty-user-skills"
                context.skill_manager.clear_cache()
                context.refresh_history_system_prompt()
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with patch("server.app.run_turn", side_effect=fake_run_turn):
                    with patch("server.app.generate_task_list", return_value=([{"step": "使用 skill", "status": "in_progress"}], "test")):
                        with TestClient(app) as client:
                            with client.websocket_connect("/agent/ws") as ws:
                                ready = ws.receive_json()
                                ws.send_json(
                                    {
                                        "type": "user_input",
                                        "content": "请用 $demo 处理",
                                        "model": "openai/gpt-4o-mini",
                                        "reasoning_effort": "off",
                                    }
                                )
                                turn_started = ws.receive_json()
                                skill_used = ws.receive_json()
                                task_list = ws.receive_json()
                                final_answer = ws.receive_json()

            turn_context = observed_contexts[-1]
            model_messages = turn_context.model_messages()
            history_messages = turn_context.history.messages
            events = contexts[-1].session_store.load_events(ready["session_id"])

            self.assertEqual(turn_started["type"], "turn_started")
            self.assertEqual(skill_used["type"], "skill_used")
            self.assertEqual(skill_used["name"], "demo")
            self.assertEqual(skill_used["path"], skill_path.resolve().as_posix())
            self.assertEqual(skill_used["invocation_type"], "explicit")
            self.assertEqual(task_list["type"], "task_list")
            self.assertEqual(final_answer["type"], "final_answer")
            self.assertIn("<skill>", model_messages[1]["content"])
            self.assertIn("Follow websocket skill steps.", model_messages[1]["content"])
            self.assertEqual(history_messages[1]["role"], "user")
            self.assertNotIn("<skill>", history_messages[1]["content"])
            self.assertIn("skill_used", [event.type for event in events])

    def test_plan_confirm_executes_original_user_input(self) -> None:
        observed_contexts = []

        async def fake_run_turn(
            ws,
            session_store,
            turn_context,
        ):
            observed_contexts.append(turn_context)
            assistant = {"role": "assistant", "content": f"done: {turn_context.user.user_input}"}
            turn_context.history.append_assistant_message(assistant)
            record_transcript_event(
                session_store,
                turn_context.session_id,
                "assistant_message",
                assistant_transcript_payload(assistant, turn_context.turn_id),
            )
            return turn_context.history

        plan_items = [
            {"step": "分析需求", "status": "in_progress"},
            {"step": "实现改动", "status": "pending"},
        ]

        with tempfile.TemporaryDirectory() as tmp:
            contexts = []
            original_create = WebSocketRuntimeContext.create

            def create_temp_context(project_root, system_prompt):
                context = original_create(Path(tmp), system_prompt)
                contexts.append(context)
                return context

            with patch("server.app.WebSocketRuntimeContext.create", side_effect=create_temp_context):
                with patch("server.app.run_turn", side_effect=fake_run_turn):
                    with patch(
                        "server.processors.request_dispatcher.generate_task_list",
                        return_value=(plan_items, "test-plan-model"),
                    ):
                        with patch(
                            "server.app.generate_task_list",
                            side_effect=AssertionError("confirmed plan should reuse pending task list"),
                        ):
                            with TestClient(app) as client:
                                with client.websocket_connect("/agent/ws") as ws:
                                    ready = ws.receive_json()
                                    ws.send_json(
                                        {
                                            "type": "plan_request",
                                            "content": "实现 Plan Mode",
                                            "request_id": "plan-1",
                                        }
                                    )
                                    pending = ws.receive_json()
                                    ws.send_json(
                                        {
                                            "type": "plan_confirm",
                                            "plan_id": pending["plan_id"],
                                            "request_id": "confirm-1",
                                            "model": "openai/gpt-4o-mini",
                                            "reasoning_effort": "off",
                                        }
                                    )
                                    turn_started = ws.receive_json()
                                    task_list = ws.receive_json()
                                    final_answer = ws.receive_json()

            context = contexts[-1]
            record = context.session_store.get_session(ready["session_id"])
            events = context.session_store.load_events(record.session_id)

            self.assertEqual(pending["type"], "plan_pending")
            self.assertEqual(pending["items"], plan_items)
            self.assertIn("计划书", pending["plan_markdown"])
            self.assertEqual(turn_started["type"], "turn_started")
            self.assertEqual(task_list["type"], "task_list")
            self.assertEqual(task_list["items"], plan_items)
            self.assertEqual(task_list["source"], "plan_confirmed")
            self.assertEqual(task_list["plan_id"], pending["plan_id"])
            self.assertEqual(final_answer["type"], "final_answer")
            self.assertEqual(final_answer["content"], "done: 实现 Plan Mode")
            self.assertEqual(observed_contexts[-1].user.user_input, "实现 Plan Mode")
            model_user_message = observed_contexts[-1].history.messages[1]["content"]
            self.assertIn("## 用户原始请求", model_user_message)
            self.assertIn("## 已确认计划", model_user_message)
            self.assertIn("# 计划书", model_user_message)
            self.assertIn("1. 分析需求", model_user_message)
            self.assertIn("2. 实现改动", model_user_message)
            self.assertEqual(
                [event.type for event in events],
                [
                    "session_started",
                    "plan_confirmed",
                    "user_message",
                    "turn_started",
                    "task_list",
                    "assistant_message",
                    "final_answer",
                ],
            )
            self.assertEqual(events[1].payload["plan_id"], pending["plan_id"])
            self.assertEqual(events[2].payload["accepted_plan"]["items"], plan_items)
            self.assertIn("计划书", events[2].payload["accepted_plan"]["plan_markdown"])


if __name__ == "__main__":
    unittest.main()
