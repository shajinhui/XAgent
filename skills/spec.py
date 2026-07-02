"""Skill package 标准定义。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


SKILL_STANDARD_DIRS = ("references", "scripts", "templates", "examples", "assets")
SKILL_ALLOWED_TOP_LEVEL_FILES = ("SKILL.md", "README.md", "LICENSE", "LICENSE.md")
SKILL_PACKAGE_TEMPLATE_BASIC = "basic"
SKILL_PACKAGE_TEMPLATE_STANDARD = "standard"
SKILL_PACKAGE_TEMPLATES = (SKILL_PACKAGE_TEMPLATE_BASIC, SKILL_PACKAGE_TEMPLATE_STANDARD)
SKILL_PACKAGE_FRONTMATTER_KEYS = {
    "name",
    "description",
    "metadata",
    "policy",
    "dependencies",
    "version",
    "author",
    "tags",
}


@dataclass(frozen=True)
class SkillPackageSpec:
    """本地文件型 skill 包的保守上限。"""

    standard_dirs: tuple[str, ...] = SKILL_STANDARD_DIRS
    allowed_top_level_files: tuple[str, ...] = SKILL_ALLOWED_TOP_LEVEL_FILES
    max_files: int = 500
    max_total_bytes: int = 5 * 1024 * 1024
    max_file_bytes: int = 1024 * 1024
    warn_unknown_top_level_entries: bool = True


DEFAULT_SKILL_PACKAGE_SPEC = SkillPackageSpec()


@dataclass(frozen=True)
class SkillPackageIssue:
    """Skill package 校验问题。"""

    severity: Literal["error", "warning"]
    code: str
    message: str
    path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.path is not None:
            payload["path"] = self.path.as_posix()
        return payload


@dataclass(frozen=True)
class SkillPackageValidation:
    """Skill package 校验结果。"""

    package_dir: Path
    skill_path: Path
    issues: tuple[SkillPackageIssue, ...] = ()
    metadata: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> tuple[SkillPackageIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[SkillPackageIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "package_dir": self.package_dir.as_posix(),
            "skill_path": self.skill_path.as_posix(),
            "issues": [issue.as_dict() for issue in self.issues],
        }
        if self.metadata is not None:
            payload["metadata"] = self.metadata
        return payload


def is_standard_skill_dir(name: str, spec: SkillPackageSpec = DEFAULT_SKILL_PACKAGE_SPEC) -> bool:
    """判断顶层目录是否属于标准 skill 资源目录。"""

    return name in spec.standard_dirs
