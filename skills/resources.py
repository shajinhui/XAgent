"""Skill 只读资源解析。"""

from __future__ import annotations

from pathlib import Path

from skills.models import SkillLoadOutcome, SkillMetadata


MAX_SKILL_RESOURCE_CHARS = 40_000


class SkillResourceError(PermissionError):
    """skill 资源无法读取。"""


class SkillResourceResolver:
    """只允许读取已加载 skill 目录内的资源。"""

    def __init__(self, outcome: SkillLoadOutcome | None = None) -> None:
        self._outcome = outcome or SkillLoadOutcome()

    @property
    def outcome(self) -> SkillLoadOutcome:
        return self._outcome

    def update(self, outcome: SkillLoadOutcome) -> None:
        """替换当前已加载 catalog。"""

        self._outcome = outcome

    def read_skill(self, *, name: str | None = None, path: str | None = None) -> tuple[SkillMetadata, str]:
        """读取 catalog 中某个 skill 的 SKILL.md。"""

        skill = self._resolve_skill(name=name, path=path)
        return skill, _read_text(skill.path)

    def read_resource(self, *, skill_path: str, resource: str) -> tuple[SkillMetadata, Path, str]:
        """读取指定 skill 目录内的相对资源。"""

        skill = self._resolve_skill(path=skill_path)
        resource_text = str(resource or "").strip()
        if not resource_text:
            raise SkillResourceError("resource is empty")
        resource_path = Path(resource_text)
        if resource_path.is_absolute():
            raise SkillResourceError("resource must be relative to the skill directory")
        if ".." in resource_path.parts:
            raise SkillResourceError("resource escaped the skill directory")
        candidate = skill.directory / resource_path
        if _has_symlink_component(candidate, skill.directory):
            raise SkillResourceError("symlinked skill resources are not allowed")
        resolved = candidate.resolve()
        if not _is_relative_to(resolved, skill.directory):
            raise SkillResourceError("resource escaped the skill directory")
        if resolved == skill.path:
            return skill, resolved, _read_text(resolved)
        if resolved.is_dir():
            raise SkillResourceError("resource is a directory")
        if not resolved.exists():
            raise SkillResourceError(f"resource not found: {resource_text}")
        return skill, resolved, _read_text(resolved)

    def _resolve_skill(self, *, name: str | None = None, path: str | None = None) -> SkillMetadata:
        clean_path = str(path or "").strip()
        if clean_path:
            skill = self._outcome.find_by_path(clean_path)
            if skill is None:
                raise SkillResourceError("skill path is not available in the current catalog")
            return skill

        clean_name = str(name or "").strip()
        if not clean_name:
            raise SkillResourceError("skill name or path is required")
        matches = [skill for skill in self._outcome.skills if skill.name == clean_name]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise SkillResourceError(f"skill not found: {clean_name}")
        raise SkillResourceError(f"skill name is ambiguous: {clean_name}")


def _read_text(path: Path) -> str:
    if path.is_symlink():
        raise SkillResourceError("symlinked skill resources are not allowed")
    if path.is_dir():
        raise SkillResourceError("resource is a directory")
    if not path.exists():
        raise SkillResourceError(f"resource not found: {path.as_posix()}")
    contents = path.read_text(encoding="utf-8", errors="replace")
    if len(contents) > MAX_SKILL_RESOURCE_CHARS:
        return contents[:MAX_SKILL_RESOURCE_CHARS] + "\n\n[内容已截断]"
    return contents


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _has_symlink_component(path: Path, root: Path) -> bool:
    """检查 root 到 path 的路径组件中是否存在 symlink。"""

    try:
        relative = path.relative_to(root)
    except ValueError:
        return True

    current = root
    for part in relative.parts:
        if part in {"", "."}:
            continue
        current = current / part
        if current.is_symlink():
            return True
    return False
