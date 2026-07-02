"""Skills 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal


class SkillScope(StrEnum):
    """Skill 来源范围。"""

    REPO = "repo"
    USER = "user"
    ADMIN = "admin"
    SYSTEM = "system"
    PLUGIN = "plugin"
    ORCHESTRATOR = "orchestrator"


class SkillSource(StrEnum):
    """Skill 资源定位类型，第一版只实现本地文件。"""

    FILE = "file"
    ENVIRONMENT_RESOURCE = "environment_resource"
    ORCHESTRATOR_RESOURCE = "orchestrator_resource"
    CUSTOM_RESOURCE = "custom_resource"


@dataclass(frozen=True)
class SkillPolicy:
    """Skill 调用策略。"""

    allow_implicit_invocation: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"allow_implicit_invocation": self.allow_implicit_invocation}


@dataclass(frozen=True)
class SkillResourceRef:
    """已加载 skill 下的资源引用。"""

    skill_path: Path
    resource: str

    def as_dict(self) -> dict[str, str]:
        return {
            "skill_path": self.skill_path.as_posix(),
            "resource": self.resource,
        }


@dataclass(frozen=True)
class SkillMetadata:
    """模型可见的 skill 摘要。"""

    name: str
    description: str
    path: Path
    scope: SkillScope
    root: Path
    source: SkillSource = SkillSource.FILE
    short_description: str | None = None
    icon: str | None = None
    version: str | None = None
    policy: SkillPolicy = field(default_factory=SkillPolicy)
    dependencies: dict[str, Any] | None = None
    install: dict[str, Any] | None = None

    @property
    def enabled(self) -> bool:
        return True

    @property
    def directory(self) -> Path:
        return self.path.parent

    def allows_implicit_invocation(self) -> bool:
        return self.policy.allow_implicit_invocation

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "path": self.path.as_posix(),
            "scope": self.scope.value,
            "source": self.source.value,
            "enabled": True,
            "policy": self.policy.as_dict(),
        }
        if self.short_description:
            payload["short_description"] = self.short_description
        if self.icon:
            payload["icon"] = self.icon
        if self.version:
            payload["version"] = self.version
        if self.dependencies:
            payload["dependencies"] = self.dependencies
        if self.install:
            payload["install"] = self.install
        return payload


@dataclass(frozen=True)
class SkillLoadError:
    """加载 skill 时发现的非致命错误。"""

    path: Path
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path.as_posix(),
            "message": self.message,
        }


@dataclass(frozen=True)
class SkillLoadOutcome:
    """一次 workspace/current_dir 下的 skills 加载结果。"""

    skills: tuple[SkillMetadata, ...] = ()
    errors: tuple[SkillLoadError, ...] = ()

    def visible_skills(self) -> tuple[SkillMetadata, ...]:
        return tuple(skill for skill in self.skills if skill.allows_implicit_invocation())

    def find_by_path(self, path: str | Path) -> SkillMetadata | None:
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            return None
        for skill in self.skills:
            if skill.path == resolved:
                return skill
        return None

    def unique_by_name(self, name: str) -> SkillMetadata | None:
        matches = [skill for skill in self.skills if skill.name == name]
        if len(matches) == 1:
            return matches[0]
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "skills": [skill.as_dict() for skill in self.skills],
            "errors": [error.as_dict() for error in self.errors],
        }


@dataclass(frozen=True)
class SkillInjection:
    """单轮模型请求中临时注入的完整 skill 提示。"""

    name: str
    path: Path
    contents: str
    invocation_type: Literal["explicit", "implicit"]

    def as_message(self) -> dict[str, str]:
        return {
            "role": "user",
            "content": (
                "<skill>\n"
                f"<name>{self.name}</name>\n"
                f"<path>{self.path.as_posix()}</path>\n"
                f"{self.contents}\n"
                "</skill>"
            ),
        }


@dataclass
class TurnSkills:
    """单轮 user_input 的 skill 状态。"""

    outcome: SkillLoadOutcome = field(default_factory=SkillLoadOutcome)
    injections: list[SkillInjection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def injection_messages(self) -> list[dict[str, str]]:
        return [injection.as_message() for injection in self.injections]
