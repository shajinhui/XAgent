from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from context_manager import ContextManager
from server.runtime.session_state import SessionRuntimeState
from server.runtime.turn_runner import run_turn
from server.runtime.model_config import ModelRequestConfig
from session import SessionStore
from session.turn_context import TurnContext
from skills.loader import SkillRoot, load_skills_from_roots, load_skills_for_workspace
from skills.management import SkillManagementError
from skills.manager import SkillManager
from skills.models import SkillInjection, SkillScope, TurnSkills
from skills.render import render_available_skills
from skills.registry import load_github_skill_registry
from skills.resources import SkillResourceError
from skills.selection import build_skill_injections, collect_selected_skills
from skills.spec import SkillPackageSpec
from skills.validator import validate_skill_package
from tools.core.catalog import build_default_registry
from tools.core.runner import ToolRunner, create_tool_context
from tools.core.types import ToolResult
from workspace import WorkspaceManager


def _write_skill(
    directory: Path,
    name: str = "demo-skill",
    description: str = "Use when testing the skill layer.",
    body: str = "Follow the repo convention.",
    *,
    allow_implicit: bool | None = None,
    version: str | None = None,
    icon: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    policy = ""
    if allow_implicit is not None:
        value = "true" if allow_implicit else "false"
        policy = f"policy:\n  allow_implicit_invocation: {value}\n"
    version_line = f"version: {version}\n" if version else ""
    icon_line = f"  icon: {icon}\n" if icon else ""
    path = directory / "SKILL.md"
    path.write_text(
        (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"{version_line}"
            "metadata:\n"
            f"  short-description: {description[:60]}\n"
            f"{icon_line}"
            f"{policy}"
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


def _github_skill_zip(
    package_path: str = "demo",
    *,
    name: str = "github-skill",
    body: str = "Use it.",
) -> bytes:
    buffer = io.BytesIO()
    skill_body = "\n".join(
        [
            "---",
            f"name: {name}",
            "description: Use installed GitHub skills.",
            "---",
            "",
            body,
        ]
    )
    with zipfile.ZipFile(buffer, "w") as archive:
        prefix = "repo-main/" + package_path.strip("/")
        archive.writestr(prefix + "/SKILL.md", skill_body + "\n")
        archive.writestr(prefix + "/references/guide.md", "GitHub guide")
    return buffer.getvalue()


def _github_api_payload(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def _github_skill_content_payload(contents: str) -> bytes:
    encoded = base64.b64encode(contents.encode("utf-8")).decode("ascii")
    return _github_api_payload({"encoding": "base64", "content": encoded})


class SkillLoaderTests(unittest.TestCase):
    def test_valid_skill_md_parses_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill_path = _write_skill(
                root / "demo",
                name="demo",
                description="Use for demos.",
                version="1.2.3",
                icon="pdf",
            )

            outcome = load_skills_from_roots([SkillRoot(path=root, scope=SkillScope.REPO)])

            self.assertEqual(len(outcome.skills), 1)
            self.assertEqual(outcome.errors, ())
            self.assertEqual(outcome.skills[0].name, "demo")
            self.assertEqual(outcome.skills[0].version, "1.2.3")
            self.assertEqual(outcome.skills[0].as_dict()["version"], "1.2.3")
            self.assertEqual(outcome.skills[0].icon, "pdf")
            self.assertEqual(outcome.skills[0].as_dict()["icon"], "pdf")
            self.assertEqual(outcome.skills[0].path, skill_path.resolve())

    def test_invalid_frontmatter_returns_load_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            bad_dir = root / "bad"
            bad_dir.mkdir(parents=True)
            (bad_dir / "SKILL.md").write_text("# Missing frontmatter\n", encoding="utf-8")

            outcome = load_skills_from_roots([SkillRoot(path=root, scope=SkillScope.REPO)])

            self.assertEqual(outcome.skills, ())
            self.assertEqual(len(outcome.errors), 1)
            self.assertIn("missing YAML frontmatter", outcome.errors[0].message)

    def test_repo_scan_follows_project_to_current_dir_chain_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            nested = project / "packages" / "app"
            sibling = project / "packages" / "other"
            nested.mkdir(parents=True)
            sibling.mkdir(parents=True)
            (project / ".git").mkdir()
            _write_skill(project / ".agents" / "skills" / "root", name="root-skill")
            _write_skill(project / "packages" / ".agents" / "skills" / "pkg", name="pkg-skill")
            _write_skill(nested / ".agents" / "skills" / "app", name="app-skill")
            _write_skill(sibling / ".agents" / "skills" / "other", name="other-skill")

            workspace = WorkspaceManager(nested).open()
            with patch("pathlib.Path.home", return_value=Path(tmp) / "home"):
                outcome = load_skills_for_workspace(workspace)

            self.assertEqual(
                [skill.name for skill in outcome.skills],
                ["app-skill", "pkg-skill", "root-skill"],
            )

    def test_hidden_skill_is_not_rendered_but_can_be_selected_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            _write_skill(root / "visible", name="visible")
            _write_skill(root / "hidden", name="hidden", allow_implicit=False)
            outcome = load_skills_from_roots([SkillRoot(path=root, scope=SkillScope.REPO)])

            rendered, warning = render_available_skills(outcome)
            selected, warnings = collect_selected_skills(outcome=outcome, user_input="$hidden")

            self.assertIsNone(warning)
            self.assertIn("visible", rendered)
            self.assertNotIn("hidden", rendered)
            self.assertEqual([skill.name for skill in selected], ["hidden"])
            self.assertEqual(warnings, [])

    def test_ambiguous_name_is_not_selected_by_dollar_mention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            _write_skill(root / "first", name="demo")
            _write_skill(root / "second", name="demo")
            outcome = load_skills_from_roots([SkillRoot(path=root, scope=SkillScope.REPO)])

            selected, warnings = collect_selected_skills(outcome=outcome, user_input="use $demo")

            self.assertEqual(selected, [])
            self.assertEqual(warnings, ["skill name is ambiguous: demo"])


class SkillPackageValidatorTests(unittest.TestCase):
    def test_valid_package_reports_metadata_and_standard_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill_path = _write_skill(root / "demo", name="demo")
            for dirname in ("references", "scripts", "templates", "examples", "assets"):
                (skill_path.parent / dirname).mkdir()
            (skill_path.parent / "references" / "guide.md").write_text("guide", encoding="utf-8")

            validation = validate_skill_package(skill_path, root=root, scope=SkillScope.REPO)

            self.assertTrue(validation.ok)
            self.assertEqual(validation.errors, ())
            self.assertEqual(validation.metadata["name"], "demo")

    def test_validator_reports_symlink_resource_as_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill_path = _write_skill(root / "demo", name="demo")
            references = skill_path.parent / "references"
            references.mkdir()
            (references / "real.md").write_text("real", encoding="utf-8")
            (references / "link.md").symlink_to(references / "real.md")

            validation = validate_skill_package(skill_path.parent, root=root, scope=SkillScope.REPO)

            self.assertFalse(validation.ok)
            self.assertIn("symlink", [issue.code for issue in validation.errors])

    def test_validator_warns_unknown_top_level_and_dependency_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            package = root / "demo"
            package.mkdir(parents=True)
            (package / "SKILL.md").write_text(
                "\n".join(
                    [
                        "---",
                        "name: demo",
                        "description: Use for demos.",
                        "dependencies:",
                        "  tools: read_file",
                        "x-extra: true",
                        "---",
                        "",
                        "Use it.",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (package / "notes").mkdir()

            validation = validate_skill_package(package, root=root, scope=SkillScope.REPO)

            self.assertTrue(validation.ok)
            warning_codes = [issue.code for issue in validation.warnings]
            self.assertIn("unknown_top_level_entry", warning_codes)
            self.assertIn("unknown_frontmatter_key", warning_codes)
            self.assertIn("invalid_dependencies", warning_codes)

    def test_validator_applies_package_file_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill_path = _write_skill(root / "demo", name="demo")
            (skill_path.parent / "references").mkdir()
            (skill_path.parent / "references" / "guide.md").write_text("guide", encoding="utf-8")

            validation = validate_skill_package(
                skill_path.parent,
                root=root,
                scope=SkillScope.REPO,
                spec=SkillPackageSpec(max_files=1),
            )

            self.assertFalse(validation.ok)
            self.assertIn("too_many_files", [issue.code for issue in validation.errors])

    def test_authoring_guide_example_packages_validate(self) -> None:
        examples_root = Path(__file__).resolve().parents[1] / "docs" / "examples" / "skills"
        expected = {
            "standard-package": "release-note-drafter",
            "rotate-pdf": "rotate-pdf",
        }

        for package_name, skill_name in expected.items():
            with self.subTest(package=package_name):
                package = examples_root / package_name
                validation = validate_skill_package(package, root=package.parent, scope=SkillScope.REPO)

                self.assertTrue(validation.ok, [issue.message for issue in validation.errors])
                self.assertEqual(validation.metadata["name"], skill_name)


class SkillResourceAndToolTests(unittest.TestCase):
    def test_resolver_denies_parent_escape_and_symlink_resource(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill_path = _write_skill(root / "demo", name="demo")
            references = skill_path.parent / "references"
            references.mkdir()
            (references / "ok.md").write_text("ok", encoding="utf-8")
            (references / "link.md").symlink_to(references / "ok.md")
            outcome = load_skills_from_roots([SkillRoot(path=root, scope=SkillScope.REPO)])
            manager = SkillManager(user_skill_root=Path(tmp) / "empty-user")
            manager.resolver.update(outcome)

            _skill, path, content = manager.resolver.read_resource(
                skill_path=skill_path.resolve().as_posix(),
                resource="references/ok.md",
            )

            self.assertEqual(path, (references / "ok.md").resolve())
            self.assertEqual(content, "ok")
            with self.assertRaises(SkillResourceError):
                manager.resolver.read_resource(
                    skill_path=skill_path.resolve().as_posix(),
                    resource="../outside.md",
                )
            with self.assertRaises(SkillResourceError):
                manager.resolver.read_resource(
                    skill_path=skill_path.resolve().as_posix(),
                    resource="references/link.md",
                )

    def test_loader_reports_symlinked_skill_md_as_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            real = root / "real.md"
            real.parent.mkdir(parents=True)
            real.write_text("---\nname: demo\ndescription: demo\n---\n", encoding="utf-8")
            skill_dir = root / "demo"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").symlink_to(real)

            outcome = load_skills_from_roots([SkillRoot(path=root, scope=SkillScope.REPO)])

            self.assertEqual(outcome.skills, ())
            self.assertEqual(len(outcome.errors), 1)
            self.assertIn("symlinked SKILL.md", outcome.errors[0].message)

    def test_skill_tools_read_catalog_without_expanding_workspace_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            user_root = Path(tmp) / "user-skills"
            skill_path = _write_skill(user_root / "demo", name="demo")
            ref = skill_path.parent / "references" / "guide.md"
            ref.parent.mkdir()
            ref.write_text("user guide", encoding="utf-8")
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=user_root)
            outcome = manager.load_for_workspace(workspace)
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    workspace.selected_root,
                    project_root=workspace.project_root,
                    current_dir=workspace.current_dir,
                    skill_resource_resolver=manager.resolver,
                ),
            )

            read_user_file = runner.execute("read_file", json.dumps({"path": ref.resolve().as_posix()}))
            read_skill = runner.execute("read_skill", json.dumps({"name": "demo"}))
            read_resource = runner.execute(
                "read_skill_resource",
                json.dumps({"skill_path": skill_path.resolve().as_posix(), "resource": "references/guide.md"}),
            )

            self.assertEqual([skill.name for skill in outcome.skills], ["demo"])
            self.assertFalse(read_user_file.ok)
            self.assertTrue(read_skill.ok)
            self.assertIn("<skill>", read_skill.content)
            self.assertTrue(read_resource.ok)
            self.assertEqual(read_resource.content, "user guide")
            self.assertTrue(read_resource.metadata["skill_resource"])
            self.assertNotIn(user_root.resolve(), runner.ctx.filesystem_policy.accessible_roots)

    def test_repo_skill_resource_can_use_read_file_or_skill_resource(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            ref = project / ".agents" / "skills" / "demo" / "references" / "guide.md"
            _write_skill(ref.parent.parent, name="demo")
            ref.parent.mkdir()
            ref.write_text("repo guide", encoding="utf-8")
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "empty-user")
            manager.load_for_workspace(workspace)
            registry = build_default_registry()
            runner = ToolRunner(
                registry,
                create_tool_context(
                    workspace.selected_root,
                    project_root=workspace.project_root,
                    current_dir=workspace.current_dir,
                    skill_resource_resolver=manager.resolver,
                ),
            )

            read_file = runner.execute("read_file", json.dumps({"path": ref.resolve().as_posix()}))
            read_resource = runner.execute(
                "read_skill_resource",
                json.dumps(
                    {
                        "skill_path": (ref.parent.parent / "SKILL.md").resolve().as_posix(),
                        "resource": "references/guide.md",
                    }
                ),
            )

            self.assertTrue(read_file.ok)
            self.assertEqual(read_file.content, "repo guide")
            self.assertTrue(read_resource.ok)
            self.assertEqual(read_resource.content, "repo guide")


class SkillContextTests(unittest.TestCase):
    def test_turn_context_injects_skill_before_current_user_without_mutating_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceManager(Path(tmp)).open()
            registry = build_default_registry()
            runner = ToolRunner(registry, create_tool_context(workspace.selected_root))
            history = ContextManager.with_system_prompt("system")
            history.append_user_message("hello")
            injection = SkillInjection(
                name="demo",
                path=Path(tmp) / "SKILL.md",
                contents="# Demo\nUse it.",
                invocation_type="explicit",
            )
            turn = TurnContext.from_runtime(
                session_id="session-1",
                turn_id="turn-1",
                workspace=workspace,
                session_state=object(),
                registry=registry,
                runner=runner,
                history=history,
                system_prompt="system",
                user_input="hello",
                model_config=ModelRequestConfig("openai/gpt-4o-mini", "off"),
                turn_skills=TurnSkills(injections=[injection]),
                current_user_message_index=1,
            )

            model_messages = turn.model_messages()

            self.assertEqual([message["role"] for message in model_messages], ["system", "user", "user"])
            self.assertIn("<skill>", model_messages[1]["content"])
            self.assertEqual(history.messages, [{"role": "system", "content": "system"}, {"role": "user", "content": "hello"}])

    def test_build_skill_injections_reads_selected_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill_path = _write_skill(root / "demo", name="demo", body="Use demo steps.")
            outcome = load_skills_from_roots([SkillRoot(path=root, scope=SkillScope.REPO)])
            manager = SkillManager(user_skill_root=Path(tmp) / "empty-user")
            manager.resolver.update(outcome)

            selected, warnings = collect_selected_skills(
                outcome=outcome,
                user_input="",
                selected_skills=[{"name": "demo", "path": skill_path.resolve().as_posix()}],
            )
            injections, injection_warnings = build_skill_injections(
                selected=selected,
                resolver=manager.resolver,
            )

            self.assertEqual(warnings, [])
            self.assertEqual(injection_warnings, [])
            self.assertEqual(len(injections), 1)
            self.assertEqual(injections[0].invocation_type, "explicit")
            self.assertIn("Use demo steps.", injections[0].contents)


class SkillRegistryTests(unittest.TestCase):
    def test_registry_uses_github_token_header(self) -> None:
        with patch.dict("os.environ", {"GH_TOKEN": "registry-token"}, clear=True):
            with patch(
                "skills.registry.urlopen",
                return_value=_FakeUrlResponse(_github_api_payload([])),
            ) as mocked_urlopen:
                outcome = load_github_skill_registry(
                    owner="openai",
                    repo="skills",
                    ref="main",
                    path="skills/.curated",
                )

        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(outcome.skills, ())
        self.assertEqual(request.get_header("Authorization"), "Bearer registry-token")
        self.assertEqual(request.get_header("Accept"), "application/vnd.github+json")

    def test_registry_lists_github_curated_skills_and_reports_bad_entries(self) -> None:
        listing = [
            {"name": "demo", "path": "skills/.curated/demo", "type": "dir"},
            {"name": "bad", "path": "skills/.curated/bad", "type": "dir"},
        ]
        skill_md = "\n".join(
            [
                "---",
                "name: registry-demo",
                "description: Use when installing curated skills.",
                "metadata:",
                "  icon: github",
                "tags:",
                "  - curated",
                "version: 1.0.0",
                "---",
                "",
                "Use it.",
            ]
        )

        with patch(
            "skills.registry.urlopen",
            side_effect=[
                _FakeUrlResponse(_github_api_payload(listing)),
                _FakeUrlResponse(_github_skill_content_payload(skill_md)),
                _FakeUrlResponse(_github_skill_content_payload("# Missing frontmatter\n")),
            ],
        ):
            outcome = load_github_skill_registry(
                owner="openai",
                repo="skills",
                ref="main",
                path="skills/.curated",
            )

        self.assertEqual([skill.id for skill in outcome.skills], ["demo"])
        self.assertEqual(outcome.skills[0].name, "registry-demo")
        self.assertEqual(outcome.skills[0].icon, "github")
        self.assertEqual(outcome.skills[0].as_dict()["icon"], "github")
        self.assertEqual(outcome.skills[0].tags, ("curated",))
        self.assertEqual(
            outcome.skills[0].source,
            "https://github.com/openai/skills/tree/main/skills/.curated/demo",
        )
        self.assertEqual(len(outcome.errors), 1)
        self.assertIn("frontmatter", outcome.errors[0].message)

    def test_manager_marks_registry_skill_installed_and_update_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            (project / ".git").mkdir()
            source = "https://github.com/openai/skills/tree/main/skills/.curated/demo"
            skill_path = _write_skill(
                project / ".agents" / "skills" / "demo",
                name="registry-demo",
                version="1.0.0",
            )
            (skill_path.parent / ".skill-install.json").write_text(
                json.dumps(
                    {
                        "source_type": "github",
                        "source": source,
                        "installed_at": "2026-06-22T00:00:00+00:00",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "user-skills")
            listing = [{"name": "demo", "path": "skills/.curated/demo", "type": "dir"}]
            skill_md = "\n".join(
                [
                    "---",
                    "name: registry-demo",
                    "description: Use when installing curated skills.",
                    "version: 1.1.0",
                    "---",
                    "",
                    "Use it.",
                ]
            )

            with patch(
                "skills.registry.urlopen",
                side_effect=[
                    _FakeUrlResponse(_github_api_payload(listing)),
                    _FakeUrlResponse(_github_skill_content_payload(skill_md)),
                ],
            ):
                payloads, outcome = manager.list_installable_skill_payloads(
                    workspace,
                    force_reload=True,
                )

            self.assertEqual(outcome.errors, ())
            self.assertEqual(payloads[0]["id"], "demo")
            self.assertTrue(payloads[0]["installed"])
            self.assertTrue(payloads[0]["update_available"])
            self.assertEqual(payloads[0]["installed_version"], "1.0.0")
            self.assertEqual(payloads[0]["installed_path"], skill_path.resolve().as_posix())


class SkillManagementTests(unittest.TestCase):
    def test_manager_creates_updates_reads_and_deletes_repo_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            (project / ".git").mkdir()
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "user-skills")

            created, outcome = manager.create_skill(
                workspace,
                scope="repo",
                name="demo-skill",
                description="Use when managing skills.",
                short_description="Manage skills.",
                allow_implicit_invocation=True,
                content="## Instructions\n\n- Follow managed steps.",
            )

            skill_path = Path(created["path"])
            self.assertTrue(skill_path.exists())
            self.assertEqual(skill_path.parent.name, "demo-skill")
            self.assertEqual([skill.name for skill in outcome.skills], ["demo-skill"])

            loaded, body = manager.read_skill_for_management(
                workspace,
                path=skill_path.as_posix(),
            )
            self.assertEqual(loaded["name"], "demo-skill")
            self.assertIn("Follow managed steps.", body)

            updated, refreshed = manager.update_skill(
                workspace,
                path=skill_path.as_posix(),
                name="demo-skill",
                description="Use after updating skills.",
                short_description="Updated skill.",
                allow_implicit_invocation=False,
                content="## Instructions\n\n- Follow updated steps.",
            )

            self.assertEqual(updated["description"], "Use after updating skills.")
            self.assertFalse(updated["policy"]["allow_implicit_invocation"])
            self.assertEqual([skill.name for skill in refreshed.skills], ["demo-skill"])

            deleted, final_outcome = manager.delete_skill(
                workspace,
                path=skill_path.as_posix(),
            )

            self.assertEqual(deleted["name"], "demo-skill")
            self.assertFalse(skill_path.exists())
            self.assertEqual(final_outcome.skills, ())

    def test_manager_creates_standard_skill_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            (project / ".git").mkdir()
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "user-skills")

            created, outcome = manager.create_skill(
                workspace,
                scope="repo",
                name="package-skill",
                description="Use when creating package skills.",
                allow_implicit_invocation=True,
                content=None,
                package_template="standard",
            )

            skill_path = Path(created["path"])
            skill_dir = skill_path.parent
            for dirname in ("references", "scripts", "templates", "examples", "assets"):
                self.assertTrue((skill_dir / dirname).is_dir())
            self.assertTrue((skill_dir / "references" / "README.md").exists())
            self.assertTrue((skill_dir / "examples" / "README.md").exists())
            self.assertIn("references/README.md", skill_path.read_text(encoding="utf-8"))
            self.assertEqual([skill.name for skill in outcome.skills], ["package-skill"])

            validation = validate_skill_package(
                skill_dir,
                root=project / ".agents" / "skills",
                scope=SkillScope.REPO,
            )
            self.assertTrue(validation.ok)

    def test_manager_manages_standard_skill_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            (project / ".git").mkdir()
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "user-skills")

            created, _outcome = manager.create_skill(
                workspace,
                scope="repo",
                name="resource-skill",
                description="Use when managing skill resources.",
                content=None,
                package_template="standard",
            )
            skill_path = Path(created["path"]).as_posix()

            _skill, resources = manager.list_skill_resources(workspace, path=skill_path)
            self.assertIn("references/README.md", [item["resource"] for item in resources])

            _skill, resource, content = manager.read_skill_resource_for_management(
                workspace,
                path=skill_path,
                resource="references/README.md",
            )
            self.assertEqual(resource["resource"], "references/README.md")
            self.assertIn("Add detailed guidance", content)

            _skill, saved, resources = manager.write_skill_resource(
                workspace,
                path=skill_path,
                resource="templates/prompt.md",
                content="Prompt template",
            )
            self.assertEqual(saved["resource"], "templates/prompt.md")
            self.assertIn("templates/prompt.md", [item["resource"] for item in resources])

            _skill, deleted, resources = manager.delete_skill_resource(
                workspace,
                path=skill_path,
                resource="templates/prompt.md",
            )
            self.assertEqual(deleted["resource"], "templates/prompt.md")
            self.assertNotIn("templates/prompt.md", [item["resource"] for item in resources])

            with self.assertRaises(SkillManagementError):
                manager.write_skill_resource(
                    workspace,
                    path=skill_path,
                    resource="../outside.md",
                    content="nope",
                )
            with self.assertRaises(SkillManagementError):
                manager.write_skill_resource(
                    workspace,
                    path=skill_path,
                    resource="misc/outside.md",
                    content="nope",
                )

    def test_usable_skill_flow_creates_selects_injects_and_reads_resource(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            (project / ".git").mkdir()
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "user-skills")
            created, _outcome = manager.create_skill(
                workspace,
                scope="repo",
                name="usable-skill",
                description="Use when testing the usable skill flow.",
                content="## Instructions\n\n- Follow the usable flow.\n",
                package_template="standard",
            )
            skill_path = created["path"]
            manager.write_skill_resource(
                workspace,
                path=skill_path,
                resource="references/guide.md",
                content="Use the guide.",
            )
            outcome = manager.load_for_workspace(workspace, force_reload=True)

            selected, selection_warnings = collect_selected_skills(
                outcome=outcome,
                user_input="$usable-skill",
            )
            injections, injection_warnings = build_skill_injections(
                selected=selected,
                resolver=manager.resolver,
            )
            _skill, resource_path, resource_content = manager.resolver.read_resource(
                skill_path=skill_path,
                resource="references/guide.md",
            )

            self.assertEqual(selection_warnings, [])
            self.assertEqual(injection_warnings, [])
            self.assertEqual(len(injections), 1)
            self.assertIn("Follow the usable flow.", injections[0].contents)
            self.assertEqual(resource_path.name, "guide.md")
            self.assertEqual(resource_content, "Use the guide.")

    def test_create_rejects_symlinked_skill_root_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            (project / ".git").mkdir()
            outside = Path(tmp) / "outside-agents"
            outside.mkdir()
            (project / ".agents").symlink_to(outside, target_is_directory=True)
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "user-skills")

            with self.assertRaises(SkillManagementError):
                manager.create_skill(
                    workspace,
                    scope="repo",
                    name="blocked-skill",
                    description="Use when testing symlink rejection.",
                    content="## Instructions\n",
                )

    def test_manager_imports_skill_directory_with_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            (project / ".git").mkdir()
            source_dir = project / "imports" / "source-skill"
            source_path = _write_skill(
                source_dir,
                name="imported-skill",
                description="Use when importing skills.",
                body="Read references/imported.md.",
            )
            references = source_path.parent / "references"
            references.mkdir()
            (references / "imported.md").write_text("Imported reference.", encoding="utf-8")
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "user-skills")

            imported, outcome = manager.import_skill(
                workspace,
                scope="repo",
                source_path=source_dir.as_posix(),
            )

            imported_path = Path(imported["path"])
            self.assertTrue(imported_path.exists())
            self.assertEqual(imported_path.parent.name, "imported-skill")
            self.assertTrue((imported_path.parent / "references" / "imported.md").exists())
            self.assertTrue((imported_path.parent / ".skill-install.json").exists())
            self.assertEqual(imported["install"]["source_type"], "local")
            self.assertEqual(imported["install"]["source"], source_dir.resolve().as_posix())
            self.assertEqual([skill.name for skill in outcome.skills], ["imported-skill"])
            self.assertEqual(outcome.skills[0].install["source_type"], "local")

    def test_manager_imports_explicit_local_source_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            (project / ".git").mkdir()
            source_dir = Path(tmp) / "outside-source"
            _write_skill(
                source_dir,
                name="outside-import",
                description="Use when importing explicit local sources.",
            )
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "user-skills")

            imported, outcome = manager.import_skill(
                workspace,
                scope="repo",
                source_path=source_dir.as_posix(),
            )

            self.assertEqual(imported["name"], "outside-import")
            self.assertEqual([skill.name for skill in outcome.skills], ["outside-import"])

            symlink_source = Path(tmp) / "outside-link"
            symlink_source.symlink_to(source_dir, target_is_directory=True)
            with self.assertRaisesRegex(SkillManagementError, "symlink"):
                manager.import_skill(
                    workspace,
                    scope="repo",
                    source_path=symlink_source.as_posix(),
                )

    def test_import_rejects_invalid_skill_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            (project / ".git").mkdir()
            source_dir = project / "imports" / "source-skill"
            source_path = _write_skill(
                source_dir,
                name="invalid-import",
                description="Use when rejecting imports.",
            )
            references = source_path.parent / "references"
            references.mkdir()
            (references / "real.md").write_text("real", encoding="utf-8")
            (references / "link.md").symlink_to(references / "real.md")
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "user-skills")

            with self.assertRaisesRegex(SkillManagementError, "invalid skill package"):
                manager.import_skill(
                    workspace,
                    scope="repo",
                    source_path=source_dir.as_posix(),
                )

    def test_manager_installs_github_skill_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            (project / ".git").mkdir()
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "user-skills")

            with patch("skills.installer.urlopen", return_value=_FakeUrlResponse(_github_skill_zip("skills/demo"))):
                installed, outcome = manager.install_skill(
                    workspace,
                    scope="repo",
                    source_type="github",
                    source="https://github.com/acme/repo/tree/main/skills/demo",
                )

            installed_path = Path(installed["path"])
            self.assertTrue(installed_path.exists())
            self.assertEqual(installed["name"], "github-skill")
            self.assertEqual(installed["install"]["source_type"], "github")
            self.assertEqual(
                installed["install"]["source"],
                "https://github.com/acme/repo/tree/main/skills/demo",
            )
            self.assertTrue((installed_path.parent / "references" / "guide.md").exists())
            self.assertEqual([skill.name for skill in outcome.skills], ["github-skill"])

            with patch(
                "skills.installer.urlopen",
                return_value=_FakeUrlResponse(
                    _github_skill_zip("skills/demo", name="github-skill", body="Updated body.")
                ),
            ):
                reinstalled, refreshed = manager.reinstall_skill(
                    workspace,
                    path=installed_path.as_posix(),
                )

            self.assertEqual(reinstalled["install"]["source_type"], "github")
            self.assertIn("Updated body.", installed_path.read_text(encoding="utf-8"))
            self.assertEqual([skill.name for skill in refreshed.skills], ["github-skill"])

    def test_manager_github_install_uses_token_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            (project / ".git").mkdir()
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "user-skills")

            with patch.dict("os.environ", {"GITHUB_TOKEN": "installer-token"}, clear=True):
                with patch(
                    "skills.installer.urlopen",
                    return_value=_FakeUrlResponse(_github_skill_zip("skills/demo")),
                ) as mocked_urlopen:
                    manager.install_skill(
                        workspace,
                        scope="repo",
                        source_type="github",
                        source="https://github.com/acme/repo/tree/main/skills/demo",
                    )

            request = mocked_urlopen.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer installer-token")

    def test_manager_installs_registry_skill_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            project.mkdir()
            (project / ".git").mkdir()
            workspace = WorkspaceManager(project).open()
            manager = SkillManager(user_skill_root=Path(tmp) / "user-skills")
            listing = [{"name": "demo", "path": "skills/.curated/demo", "type": "dir"}]
            skill_md = "\n".join(
                [
                    "---",
                    "name: registry-demo",
                    "description: Use when installing curated skills.",
                    "---",
                    "",
                    "Use it.",
                ]
            )

            with patch(
                "skills.registry.urlopen",
                side_effect=[
                    _FakeUrlResponse(_github_api_payload(listing)),
                    _FakeUrlResponse(_github_skill_content_payload(skill_md)),
                ],
            ):
                with patch(
                    "skills.installer.urlopen",
                    return_value=_FakeUrlResponse(_github_skill_zip("skills/.curated/demo")),
                ):
                    installed, outcome = manager.install_registry_skill(
                        workspace,
                        scope="repo",
                        registry_id="demo",
                    )

            installed_path = Path(installed["path"])
            self.assertTrue(installed_path.exists())
            self.assertEqual(installed["name"], "github-skill")
            self.assertEqual(
                installed["install"]["source"],
                "https://github.com/openai/skills/tree/main/skills/.curated/demo",
            )
            self.assertEqual([skill.name for skill in outcome.skills], ["github-skill"])


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, event: dict) -> None:
        self.sent.append(event)


class FakeSkillRunner:
    def execute_invocation(self, _invocation) -> ToolResult:
        return ToolResult(
            ok=True,
            content="<skill>\n# Demo\nFull skill body.\n</skill>",
            metadata={
                "skill_used": True,
                "skill_name": "demo",
                "skill_path": "/tmp/demo/SKILL.md",
                "skill_scope": "repo",
                "invocation_type": "implicit",
            },
        )


class SkillTurnRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_skill_tool_result_is_visible_in_turn_but_stripped_after_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceManager(Path(tmp)).open()
            session_store = SessionStore(Path(tmp))
            session_store.create_session(session_id="session-1")
            registry = build_default_registry()
            history = ContextManager.with_system_prompt("system")
            history.append_user_message("use skill")
            state = SessionRuntimeState("session-1")
            state.start_turn("turn-1")
            observed_model_messages: list[list[dict]] = []

            async def fake_stream_model_message(
                _ws,
                _registry,
                messages,
                _session_id,
                _turn_id,
                _model_config,
            ):
                observed_model_messages.append([dict(message) for message in messages])
                if len(observed_model_messages) == 1:
                    return {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-skill",
                                "type": "function",
                                "function": {
                                    "name": "read_skill",
                                    "arguments": json.dumps({"name": "demo"}),
                                },
                            }
                        ],
                    }
                return {"role": "assistant", "content": "done"}

            turn = TurnContext.from_runtime(
                session_id="session-1",
                turn_id="turn-1",
                workspace=workspace,
                session_state=state,
                registry=registry,
                runner=FakeSkillRunner(),
                history=history,
                system_prompt="system",
                user_input="use skill",
                model_config=ModelRequestConfig("openai/gpt-4o-mini", "off"),
            )

            with patch("server.runtime.turn_runner.stream_model_message", side_effect=fake_stream_model_message):
                result_history = await run_turn(FakeWebSocket(), session_store, turn)

            second_request_tool_messages = [
                message
                for message in observed_model_messages[1]
                if message.get("role") == "tool" and message.get("name") == "read_skill"
            ]
            stored_tool_messages = [
                message
                for message in result_history.messages
                if message.get("role") == "tool" and message.get("name") == "read_skill"
            ]
            transcript_events = session_store.load_events("session-1")
            tool_result_events = [event for event in transcript_events if event.type == "tool_call_result"]

            self.assertEqual(len(observed_model_messages), 2)
            self.assertEqual(second_request_tool_messages[0]["content"], "<skill>\n# Demo\nFull skill body.\n</skill>")
            self.assertEqual(
                stored_tool_messages[0]["content"],
                "[skill content omitted from history after this turn]",
            )
            self.assertEqual(tool_result_events[0].payload["content"], "[skill content omitted from transcript]")
            self.assertIn("skill_used", [event.type for event in transcript_events])


if __name__ == "__main__":
    unittest.main()
