"""Skills catalog 管理器。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skills.loader import load_skills_for_workspace
from skills.management import (
    SkillManagementError,
    create_skill,
    delete_skill,
    delete_skill_resource,
    import_skill,
    install_skill,
    list_skill_resources,
    read_skill_for_management,
    read_skill_resource_for_management,
    reinstall_skill,
    update_skill,
    write_skill_resource,
)
from skills.models import SkillLoadOutcome, SkillMetadata
from skills.registry import SkillRegistryOutcome, load_installable_skills
from skills.resources import SkillResourceResolver
from workspace import WorkspaceContext


VERSION_NUMBER_RE = re.compile(r"\d+")


@dataclass
class SkillManager:
    """按 workspace/current_dir 缓存已加载 skills。"""

    user_skill_root: Path | None = None
    _cache: dict[tuple[str, str], SkillLoadOutcome] = field(default_factory=dict)
    _registry_cache: SkillRegistryOutcome | None = None
    resolver: SkillResourceResolver = field(default_factory=SkillResourceResolver)

    def load_for_workspace(
        self,
        workspace: WorkspaceContext,
        *,
        force_reload: bool = False,
    ) -> SkillLoadOutcome:
        """加载当前 workspace 可见 skills，并刷新 resolver。"""

        key = (workspace.project_root.resolve().as_posix(), workspace.current_dir.resolve().as_posix())
        if force_reload or key not in self._cache:
            self._cache[key] = load_skills_for_workspace(
                workspace,
                user_skill_root=self.user_skill_root,
            )
        outcome = self._cache[key]
        self.resolver.update(outcome)
        return outcome

    def clear_cache(self) -> None:
        """清空缓存。"""

        self._cache.clear()
        self._registry_cache = None

    def _reload_and_resolve(
        self,
        workspace: WorkspaceContext,
        skill: SkillMetadata,
    ) -> tuple[dict[str, Any], SkillLoadOutcome]:
        """刷新 catalog，并优先返回重新解析后的 skill 元数据。"""

        outcome = self.load_for_workspace(workspace, force_reload=True)
        resolved = outcome.find_by_path(skill.path) or skill
        return resolved.as_dict(), outcome

    def create_skill(
        self,
        workspace: WorkspaceContext,
        *,
        scope: str,
        name: Any,
        description: Any,
        short_description: Any = None,
        icon: Any = None,
        allow_implicit_invocation: Any = True,
        content: Any = None,
        package_template: Any = None,
    ) -> tuple[dict[str, Any], SkillLoadOutcome]:
        """创建 skill 并返回刷新后的 catalog。"""

        skill = create_skill(
            workspace=workspace,
            user_skill_root=self.user_skill_root,
            scope=scope,
            name=name,
            description=description,
            short_description=short_description,
            icon=icon,
            allow_implicit_invocation=allow_implicit_invocation,
            content=content,
            package_template=package_template,
        )
        return self._reload_and_resolve(workspace, skill)

    def read_skill_for_management(
        self,
        workspace: WorkspaceContext,
        *,
        path: str,
    ) -> tuple[dict[str, Any], str]:
        """读取一个 catalog 中的 skill，供用户编辑。"""

        outcome = self.load_for_workspace(workspace, force_reload=True)
        skill = outcome.find_by_path(path)
        if skill is None:
            raise SkillManagementError("skill path is not available in the current catalog")
        resolved, content = read_skill_for_management(skill)
        return resolved.as_dict(), content

    def list_skill_resources(
        self,
        workspace: WorkspaceContext,
        *,
        path: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """列出一个 catalog skill 的标准资源文件。"""

        skill = self._managed_skill(workspace, path)
        return skill.as_dict(), list_skill_resources(skill)

    def read_skill_resource_for_management(
        self,
        workspace: WorkspaceContext,
        *,
        path: str,
        resource: Any,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """读取一个 catalog skill 的标准资源文件。"""

        skill = self._managed_skill(workspace, path)
        resource_payload, content = read_skill_resource_for_management(skill, resource=resource)
        return skill.as_dict(), resource_payload, content

    def write_skill_resource(
        self,
        workspace: WorkspaceContext,
        *,
        path: str,
        resource: Any,
        content: Any,
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        """写入一个 catalog skill 的标准资源文件。"""

        skill = self._managed_skill(workspace, path)
        resource_payload = write_skill_resource(skill, resource=resource, content=content)
        return skill.as_dict(), resource_payload, list_skill_resources(skill)

    def delete_skill_resource(
        self,
        workspace: WorkspaceContext,
        *,
        path: str,
        resource: Any,
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        """删除一个 catalog skill 的标准资源文件。"""

        skill = self._managed_skill(workspace, path)
        resource_payload = delete_skill_resource(skill, resource=resource)
        return skill.as_dict(), resource_payload, list_skill_resources(skill)

    def import_skill(
        self,
        workspace: WorkspaceContext,
        *,
        scope: str,
        source_path: Any,
    ) -> tuple[dict[str, Any], SkillLoadOutcome]:
        """导入本地 skill 目录并返回刷新后的 catalog。"""

        skill = import_skill(
            workspace=workspace,
            user_skill_root=self.user_skill_root,
            scope=scope,
            source_path=source_path,
        )
        return self._reload_and_resolve(workspace, skill)

    def install_skill(
        self,
        workspace: WorkspaceContext,
        *,
        scope: str,
        source_type: Any,
        source: Any,
    ) -> tuple[dict[str, Any], SkillLoadOutcome]:
        """安装 skill package 并返回刷新后的 catalog。"""

        skill = install_skill(
            workspace=workspace,
            user_skill_root=self.user_skill_root,
            scope=scope,
            source_type=source_type,
            source=source,
        )
        return self._reload_and_resolve(workspace, skill)

    def list_installable_skills(self, *, force_reload: bool = False) -> SkillRegistryOutcome:
        """加载可安装 skill registry。"""

        if force_reload or self._registry_cache is None:
            self._registry_cache = load_installable_skills()
        return self._registry_cache

    def list_installable_skill_payloads(
        self,
        workspace: WorkspaceContext,
        *,
        force_reload: bool = False,
    ) -> tuple[list[dict[str, Any]], SkillRegistryOutcome]:
        """返回带本地安装状态的 registry payload。"""

        registry = self.list_installable_skills(force_reload=force_reload)
        catalog = self.load_for_workspace(workspace, force_reload=True)
        installed_by_source = _installed_skills_by_source(catalog.skills)
        payloads: list[dict[str, Any]] = []
        for entry in registry.skills:
            payload = entry.as_dict()
            installed = installed_by_source.get(_normalize_source(entry.source))
            payload["installed"] = installed is not None
            payload["update_available"] = False
            if installed is not None:
                payload["installed_path"] = installed.path.as_posix()
                if installed.version:
                    payload["installed_version"] = installed.version
                payload["update_available"] = _version_is_newer(entry.version, installed.version)
            payloads.append(payload)
        return payloads, registry

    def install_registry_skill(
        self,
        workspace: WorkspaceContext,
        *,
        scope: str,
        registry_id: Any,
    ) -> tuple[dict[str, Any], SkillLoadOutcome]:
        """从 curated registry 安装一个 skill package。"""

        registry = self.list_installable_skills()
        entry = registry.find(registry_id)
        if entry is None:
            raise SkillManagementError("installable skill is not available in the registry")
        skill = install_skill(
            workspace=workspace,
            user_skill_root=self.user_skill_root,
            scope=scope,
            source_type=entry.source_type,
            source=entry.source,
        )
        return self._reload_and_resolve(workspace, skill)

    def reinstall_skill(
        self,
        workspace: WorkspaceContext,
        *,
        path: str,
    ) -> tuple[dict[str, Any], SkillLoadOutcome]:
        """按安装来源重新安装 catalog 中的 skill。"""

        skill = self._managed_skill(workspace, path)
        updated = reinstall_skill(skill)
        return self._reload_and_resolve(workspace, updated)

    def update_skill(
        self,
        workspace: WorkspaceContext,
        *,
        path: str,
        name: Any,
        description: Any,
        short_description: Any = None,
        icon: Any = None,
        allow_implicit_invocation: Any = True,
        content: Any = None,
    ) -> tuple[dict[str, Any], SkillLoadOutcome]:
        """更新 catalog 中的 skill 并刷新 catalog。"""

        outcome = self.load_for_workspace(workspace, force_reload=True)
        skill = outcome.find_by_path(path)
        if skill is None:
            raise SkillManagementError("skill path is not available in the current catalog")
        updated = update_skill(
            skill=skill,
            name=name,
            description=description,
            short_description=short_description,
            icon=icon,
            allow_implicit_invocation=allow_implicit_invocation,
            content=content,
        )
        return self._reload_and_resolve(workspace, updated)

    def delete_skill(
        self,
        workspace: WorkspaceContext,
        *,
        path: str,
    ) -> tuple[dict[str, Any], SkillLoadOutcome]:
        """删除 catalog 中的 skill 并刷新 catalog。"""

        outcome = self.load_for_workspace(workspace, force_reload=True)
        skill = outcome.find_by_path(path)
        if skill is None:
            raise SkillManagementError("skill path is not available in the current catalog")
        deleted = skill.as_dict()
        delete_skill(skill)
        refreshed = self.load_for_workspace(workspace, force_reload=True)
        return deleted, refreshed

    def _managed_skill(self, workspace: WorkspaceContext, path: str) -> SkillMetadata:
        """从当前 catalog 中解析一个可管理 skill。"""

        outcome = self.load_for_workspace(workspace, force_reload=True)
        skill = outcome.find_by_path(path)
        if skill is None:
            raise SkillManagementError("skill path is not available in the current catalog")
        return skill


def _installed_skills_by_source(skills: tuple[SkillMetadata, ...]) -> dict[str, SkillMetadata]:
    """按安装来源索引已安装的 GitHub skills。"""

    installed: dict[str, SkillMetadata] = {}
    for skill in skills:
        record = skill.install or {}
        if record.get("source_type") != "github":
            continue
        source = _normalize_source(record.get("source"))
        if source:
            installed[source] = skill
    return installed


def _normalize_source(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _version_is_newer(available: str | None, installed: str | None) -> bool:
    if not available or not installed or available == installed:
        return False
    available_key = _version_key(available)
    installed_key = _version_key(installed)
    if available_key is None or installed_key is None:
        return False
    return available_key > installed_key


def _version_key(value: str) -> tuple[int, ...] | None:
    parts = VERSION_NUMBER_RE.findall(value)
    if not parts:
        return None
    return tuple(int(part) for part in parts[:6])
