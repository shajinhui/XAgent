"""扫描并解析本地文件型 Skills。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from skills.installer import read_install_record
from skills.models import SkillLoadError, SkillLoadOutcome, SkillMetadata, SkillPolicy, SkillScope
from workspace import WorkspaceContext


SKILL_FILE_NAME = "SKILL.md"
AGENTS_DIR_NAME = ".agents"
SKILLS_DIR_NAME = "skills"
MAX_SCAN_DEPTH = 6
MAX_SKILLS_DIRS_PER_ROOT = 2000
MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024


@dataclass(frozen=True)
class SkillRoot:
    """一个可扫描的 skill 根目录。"""

    path: Path
    scope: SkillScope


class SkillParseError(ValueError):
    """单个 SKILL.md 无法解析。"""


def load_skills_for_workspace(
    workspace: WorkspaceContext,
    *,
    user_skill_root: Path | None = None,
) -> SkillLoadOutcome:
    """按当前 workspace/current_dir 加载 repo 与 user skills。"""

    return load_skills_from_roots(skill_roots_for_workspace(workspace, user_skill_root=user_skill_root))


def skill_roots_for_workspace(
    workspace: WorkspaceContext,
    *,
    user_skill_root: Path | None = None,
) -> tuple[SkillRoot, ...]:
    """生成当前 workspace 可见的 skill roots。"""

    roots: list[SkillRoot] = []
    for directory in _workspace_skill_search_dirs(workspace.project_root, workspace.current_dir):
        roots.append(
            SkillRoot(
                path=(directory / AGENTS_DIR_NAME / SKILLS_DIR_NAME).resolve(),
                scope=SkillScope.REPO,
            )
        )

    user_root = (user_skill_root or (Path.home() / AGENTS_DIR_NAME / SKILLS_DIR_NAME)).expanduser()
    roots.append(SkillRoot(path=user_root.resolve(), scope=SkillScope.USER))

    seen: set[Path] = set()
    deduped: list[SkillRoot] = []
    for root in roots:
        if root.path in seen:
            continue
        seen.add(root.path)
        deduped.append(root)
    return tuple(deduped)


def load_skills_from_roots(roots: tuple[SkillRoot, ...] | list[SkillRoot]) -> SkillLoadOutcome:
    """从多个 root 加载 skills，并保留非致命错误。"""

    skills: list[SkillMetadata] = []
    errors: list[SkillLoadError] = []
    seen_paths: set[Path] = set()

    for root in roots:
        if not root.path.exists():
            continue
        if not root.path.is_dir() or root.path.is_symlink():
            continue
        for path in _iter_skill_files(root.path):
            if path in seen_paths:
                continue
            seen_paths.add(path)
            try:
                skills.append(parse_skill_file(path, root))
            except SkillParseError as exc:
                errors.append(SkillLoadError(path=path, message=str(exc)))

    return SkillLoadOutcome(
        skills=tuple(sorted(skills, key=_skill_sort_key)),
        errors=tuple(errors),
    )


def parse_skill_file(path: Path, root: SkillRoot) -> SkillMetadata:
    """解析一个 SKILL.md。"""

    original_path = path.expanduser()
    if original_path.is_symlink():
        raise SkillParseError("symlinked SKILL.md is not allowed")
    resolved_path = original_path.resolve()
    resolved_root = root.path.expanduser().resolve()
    if not _is_relative_to(resolved_path, resolved_root):
        raise SkillParseError("skill path escaped its root")

    contents = resolved_path.read_text(encoding="utf-8", errors="replace")
    frontmatter = _extract_frontmatter(contents)
    if frontmatter is None:
        raise SkillParseError("missing YAML frontmatter delimited by ---")

    try:
        parsed = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as exc:
        raise SkillParseError(f"invalid YAML frontmatter: {exc}") from exc

    if not isinstance(parsed, dict):
        raise SkillParseError("frontmatter must be a mapping")

    name = _required_text(parsed.get("name"), "name", MAX_NAME_LEN)
    description = _required_text(parsed.get("description"), "description", MAX_DESCRIPTION_LEN)
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    short_description = _optional_text(metadata.get("short-description"), MAX_DESCRIPTION_LEN)
    icon = _optional_text(parsed.get("icon") or metadata.get("icon"), 64)
    version = _optional_text(parsed.get("version") or metadata.get("version"), 64)
    policy = _parse_policy(parsed.get("policy"))
    dependencies = parsed.get("dependencies") if isinstance(parsed.get("dependencies"), dict) else None
    install = read_install_record(resolved_path.parent)

    return SkillMetadata(
        name=name,
        description=description,
        short_description=short_description,
        icon=icon,
        path=resolved_path,
        root=resolved_root,
        scope=root.scope,
        version=version,
        policy=policy,
        dependencies=dependencies,
        install=install,
    )


def _iter_skill_files(root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    visited_dirs = 0
    root_depth = len(root.parts)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        if current.is_symlink():
            dirnames[:] = []
            continue
        depth = len(current.parts) - root_depth
        if depth >= MAX_SCAN_DEPTH:
            dirnames[:] = []
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if not name.startswith(".") and not (current / name).is_symlink()
        ]
        visited_dirs += 1
        if visited_dirs > MAX_SKILLS_DIRS_PER_ROOT:
            break
        if SKILL_FILE_NAME in filenames:
            candidate = current / SKILL_FILE_NAME
            if candidate.is_symlink():
                paths.append(candidate)
                continue
            resolved_candidate = candidate.resolve()
            if _is_relative_to(resolved_candidate, root):
                paths.append(candidate)
    return tuple(sorted(paths))


def _workspace_skill_search_dirs(project_root: Path, current_dir: Path) -> tuple[Path, ...]:
    project_root = project_root.resolve()
    current_dir = current_dir.resolve()
    if current_dir != project_root and project_root not in current_dir.parents:
        return (project_root,)

    dirs = [project_root]
    current = project_root
    for part in current_dir.relative_to(project_root).parts:
        current = current / part
        dirs.append(current)
    return tuple(dirs)


def _extract_frontmatter(contents: str) -> str | None:
    if not contents.startswith("---"):
        return None
    lines = contents.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index])
    return None


def _required_text(value: Any, field: str, max_len: int) -> str:
    text = _optional_text(value, max_len)
    if not text:
        raise SkillParseError(f"missing field `{field}`")
    return text


def _optional_text(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    if len(text) > max_len:
        raise SkillParseError(f"field is too long: max {max_len} chars")
    return text


def _parse_policy(value: Any) -> SkillPolicy:
    if not isinstance(value, dict):
        return SkillPolicy()
    raw = value.get("allow_implicit_invocation")
    if raw is None:
        return SkillPolicy()
    return SkillPolicy(allow_implicit_invocation=bool(raw))


def _skill_sort_key(skill: SkillMetadata) -> tuple[int, str, str]:
    scope_rank = {
        SkillScope.REPO: 0,
        SkillScope.USER: 1,
        SkillScope.ADMIN: 2,
        SkillScope.SYSTEM: 3,
        SkillScope.PLUGIN: 4,
        SkillScope.ORCHESTRATOR: 5,
    }
    return (scope_rank.get(skill.scope, 99), skill.name, skill.path.as_posix())


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
