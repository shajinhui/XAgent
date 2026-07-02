"""Skill package 校验器。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from skills.loader import SKILL_FILE_NAME, SkillParseError, SkillRoot, parse_skill_file
from skills.models import SkillScope
from skills.spec import (
    DEFAULT_SKILL_PACKAGE_SPEC,
    SKILL_PACKAGE_FRONTMATTER_KEYS,
    SkillPackageIssue,
    SkillPackageSpec,
    SkillPackageValidation,
    is_standard_skill_dir,
)


def validate_skill_package(
    path: str | Path,
    *,
    root: str | Path | None = None,
    scope: SkillScope = SkillScope.REPO,
    spec: SkillPackageSpec = DEFAULT_SKILL_PACKAGE_SPEC,
) -> SkillPackageValidation:
    """校验一个本地文件型 skill package。"""

    raw_path = Path(path).expanduser()
    package_dir, skill_path = _package_paths(raw_path)
    package_dir = _safe_resolve(package_dir)
    skill_path = _safe_resolve(skill_path)
    issues: list[SkillPackageIssue] = []
    metadata: dict[str, Any] | None = None

    resolved_root = _safe_resolve(Path(root).expanduser()) if root is not None else package_dir
    if root is not None and not _is_relative_to(skill_path, resolved_root):
        issues.append(
            _error(
                "path_escaped_root",
                "skill path escaped its root",
                skill_path,
            )
        )

    if raw_path.is_symlink() or package_dir.is_symlink() or skill_path.is_symlink():
        issues.append(_error("symlink", "symlinked skill package paths are not allowed", skill_path))

    if not package_dir.exists() or not package_dir.is_dir():
        issues.append(_error("missing_package_dir", "skill package directory does not exist", package_dir))
    elif package_dir.is_symlink():
        issues.append(_error("symlink", "symlinked skill package directory is not allowed", package_dir))

    if not skill_path.exists() or not skill_path.is_file():
        issues.append(_error("missing_skill_file", "skill package must contain SKILL.md", skill_path))
    elif not skill_path.is_symlink():
        try:
            skill = parse_skill_file(skill_path, SkillRoot(path=resolved_root, scope=scope))
            metadata = skill.as_dict()
        except SkillParseError as exc:
            issues.append(_error("invalid_skill_file", str(exc), skill_path))
        _validate_frontmatter_shape(skill_path, issues)

    if package_dir.exists() and package_dir.is_dir() and not package_dir.is_symlink():
        _validate_package_tree(package_dir, skill_path, issues, spec)

    return SkillPackageValidation(
        package_dir=package_dir,
        skill_path=skill_path,
        issues=tuple(issues),
        metadata=metadata,
    )


def raise_for_skill_package_errors(validation: SkillPackageValidation) -> None:
    """在管理写入路径中把 package error 转成 ValueError。"""

    if validation.ok:
        return
    first = validation.errors[0]
    raise ValueError(first.message)


def _package_paths(path: Path) -> tuple[Path, Path]:
    if path.name == SKILL_FILE_NAME:
        return path.parent, path
    return path, path / SKILL_FILE_NAME


def _validate_package_tree(
    package_dir: Path,
    skill_path: Path,
    issues: list[SkillPackageIssue],
    spec: SkillPackageSpec,
) -> None:
    file_count = 0
    total_bytes = 0
    root_depth = len(package_dir.parts)

    if spec.warn_unknown_top_level_entries:
        for child in sorted(package_dir.iterdir(), key=lambda item: item.name):
            if child.name.startswith("."):
                continue
            if child.is_dir() and not is_standard_skill_dir(child.name, spec):
                issues.append(
                    _warning(
                        "unknown_top_level_entry",
                        f"unknown top-level skill directory: {child.name}",
                        child,
                    )
                )
            elif child.is_file() and child.name not in spec.allowed_top_level_files:
                issues.append(
                    _warning(
                        "unknown_top_level_entry",
                        f"unknown top-level skill file: {child.name}",
                        child,
                    )
                )

    for dirpath, dirnames, filenames in os.walk(package_dir, followlinks=False):
        current = Path(dirpath)
        if current.is_symlink():
            issues.append(_error("symlink", "symlinked skill package paths are not allowed", current))
            dirnames[:] = []
            continue

        dirnames[:] = sorted(dirnames)
        filenames = sorted(filenames)
        for dirname in list(dirnames):
            candidate = current / dirname
            if candidate.is_symlink():
                issues.append(_error("symlink", "symlinked skill package paths are not allowed", candidate))
                dirnames.remove(dirname)

        for filename in filenames:
            candidate = current / filename
            if candidate.is_symlink():
                issues.append(_error("symlink", "symlinked skill package paths are not allowed", candidate))
                continue
            if candidate.name == SKILL_FILE_NAME and _safe_resolve(candidate) != skill_path:
                issues.append(_warning("nested_skill_file", "nested SKILL.md will not be managed as part of this package", candidate))
            file_count += 1
            if file_count > spec.max_files:
                issues.append(_error("too_many_files", f"skill package has more than {spec.max_files} files", package_dir))
                return
            try:
                size = candidate.stat().st_size
            except OSError:
                issues.append(_error("unreadable_file", "skill package file is not readable", candidate))
                continue
            total_bytes += size
            if size > spec.max_file_bytes:
                issues.append(
                    _warning(
                        "large_file",
                        f"skill package file is larger than {spec.max_file_bytes} bytes",
                        candidate,
                    )
                )
            if total_bytes > spec.max_total_bytes:
                issues.append(
                    _error(
                        "package_too_large",
                        f"skill package is larger than {spec.max_total_bytes} bytes",
                        package_dir,
                    )
                )
                return

        if len(current.parts) - root_depth > 8:
            dirnames[:] = []


def _validate_frontmatter_shape(path: Path, issues: list[SkillPackageIssue]) -> None:
    try:
        contents = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        issues.append(_error("unreadable_skill_file", "SKILL.md is not readable", path))
        return

    frontmatter = _extract_frontmatter(contents)
    if frontmatter is None:
        return
    try:
        parsed = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        return
    if not isinstance(parsed, dict):
        return

    for key in sorted(parsed):
        if key not in SKILL_PACKAGE_FRONTMATTER_KEYS:
            issues.append(_warning("unknown_frontmatter_key", f"unknown skill frontmatter key: {key}", path))

    tags = parsed.get("tags")
    if tags is not None and (
        not isinstance(tags, list) or any(not isinstance(item, str) or not item.strip() for item in tags)
    ):
        issues.append(_warning("invalid_tags", "skill tags should be a list of non-empty strings", path))

    for key in ("version", "author"):
        value = parsed.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            issues.append(_warning(f"invalid_{key}", f"skill {key} should be a non-empty string", path))

    dependencies = parsed.get("dependencies")
    if dependencies is not None:
        _validate_dependencies(dependencies, path, issues)


def _validate_dependencies(value: Any, path: Path, issues: list[SkillPackageIssue]) -> None:
    if not isinstance(value, dict):
        issues.append(_warning("invalid_dependencies", "skill dependencies should be a mapping", path))
        return
    for key in ("tools", "mcp", "plugins"):
        items = value.get(key)
        if items is None:
            continue
        if not isinstance(items, list) or any(not isinstance(item, str) or not item.strip() for item in items):
            issues.append(
                _warning(
                    "invalid_dependencies",
                    f"skill dependencies.{key} should be a list of non-empty strings",
                    path,
                )
            )


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


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _error(code: str, message: str, path: Path | None = None) -> SkillPackageIssue:
    return SkillPackageIssue(severity="error", code=code, message=message, path=path)


def _warning(code: str, message: str, path: Path | None = None) -> SkillPackageIssue:
    return SkillPackageIssue(severity="warning", code=code, message=message, path=path)
