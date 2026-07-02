"""Skills 文件型管理操作。"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from skills.loader import (
    AGENTS_DIR_NAME,
    MAX_DESCRIPTION_LEN,
    MAX_NAME_LEN,
    SKILL_FILE_NAME,
    SKILLS_DIR_NAME,
    SkillParseError,
    SkillRoot,
    parse_skill_file,
)
from skills.installer import (
    SkillInstallError,
    fetch_github_skill_package,
    github_install_record,
    local_install_record,
    parse_github_skill_source,
    read_install_record,
    write_install_record,
)
from skills.models import SkillMetadata, SkillScope
from skills.spec import (
    DEFAULT_SKILL_PACKAGE_SPEC,
    SKILL_PACKAGE_TEMPLATE_BASIC,
    SKILL_PACKAGE_TEMPLATE_STANDARD,
    SKILL_PACKAGE_TEMPLATES,
    SKILL_STANDARD_DIRS,
)
from skills.validator import validate_skill_package
from workspace import WorkspaceContext


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
DEFAULT_SKILL_BODY = (
    "## Instructions\n\n"
    "- Keep this file concise and task-focused.\n"
    "- Put detailed reference material in `references/` and read it only when needed.\n"
)
STANDARD_PACKAGE_BODY = (
    "## Instructions\n\n"
    "- Follow the core workflow for this skill.\n"
    "- Read `references/README.md` when the task needs detailed rules or domain context.\n"
    "- Use files in `templates/` or `assets/` as source material instead of recreating them.\n"
    "- Prefer scripts in `scripts/` for repeated or fragile operations.\n"
)
DEFAULT_REFERENCE_README = (
    "# References\n\n"
    "Add detailed guidance that should be loaded only when the task needs it.\n\n"
    "- Keep SKILL.md concise.\n"
    "- Split large domains into focused files and link them from SKILL.md.\n"
)
DEFAULT_EXAMPLE_README = (
    "# Examples\n\n"
    "Add representative prompts, expected outputs, or small artifacts that clarify how the skill should behave.\n"
)


class SkillManagementError(ValueError):
    """用户管理 skill 时的可展示错误。"""


def create_skill(
    *,
    workspace: WorkspaceContext,
    user_skill_root: Path | None,
    scope: str,
    name: Any,
    description: Any,
    short_description: Any = None,
    icon: Any = None,
    allow_implicit_invocation: Any = True,
    content: Any = None,
    package_template: Any = None,
) -> SkillMetadata:
    """在 repo 或 user skill root 下创建一个新的 SKILL.md。"""

    target_scope = _parse_writable_scope(scope)
    template = _parse_package_template(package_template)
    root = _writable_root(workspace, user_skill_root, target_scope)
    skill_name = _clean_name(name)
    skill_description = _clean_text(description, "description", MAX_DESCRIPTION_LEN)
    skill_short_description = _clean_optional_text(
        short_description,
        "short_description",
        MAX_DESCRIPTION_LEN,
    )
    skill_icon = _clean_optional_text(icon, "icon", 64)
    body = _clean_body(content, template=template)
    skill_dir = root / _slugify(skill_name)
    skill_path = skill_dir / SKILL_FILE_NAME

    if _has_symlink_component(skill_dir, root):
        raise SkillManagementError("skill path contains a symlink")
    if skill_dir.exists():
        raise SkillManagementError("skill already exists")

    skill_dir.mkdir(parents=True, exist_ok=False)
    if template == SKILL_PACKAGE_TEMPLATE_STANDARD:
        _create_standard_package_layout(skill_dir)
    skill_path.write_text(
        _render_skill_markdown(
            name=skill_name,
            description=skill_description,
            short_description=skill_short_description,
            icon=skill_icon,
            allow_implicit_invocation=bool(allow_implicit_invocation),
            body=body,
        ),
        encoding="utf-8",
    )
    return parse_skill_file(skill_path, SkillRoot(path=root.resolve(), scope=target_scope))


def import_skill(
    *,
    workspace: WorkspaceContext,
    user_skill_root: Path | None,
    scope: str,
    source_path: Any,
) -> SkillMetadata:
    """从已授权源目录导入一个本地文件型 skill。"""

    target_scope = _parse_writable_scope(scope)
    target_root = _writable_root(workspace, user_skill_root, target_scope)
    source_skill_path = _resolve_import_source(workspace, source_path)
    source_dir = source_skill_path.parent
    validation = validate_skill_package(source_skill_path, root=source_dir, scope=target_scope)
    if not validation.ok:
        raise SkillManagementError(f"invalid skill package: {validation.errors[0].message}")
    imported = parse_skill_file(
        source_skill_path,
        SkillRoot(path=source_dir.resolve(), scope=target_scope),
    )
    target_dir = target_root / _slugify(imported.name)
    if _has_symlink_component(target_dir, target_root):
        raise SkillManagementError("skill path contains a symlink")
    if target_dir.exists():
        raise SkillManagementError("skill already exists")
    _copy_skill_directory(source_dir, target_dir)
    write_install_record(target_dir, local_install_record(source_dir))
    return parse_skill_file(
        target_dir / SKILL_FILE_NAME,
        SkillRoot(path=target_root.resolve(), scope=target_scope),
    )


def install_skill(
    *,
    workspace: WorkspaceContext,
    user_skill_root: Path | None,
    scope: str,
    source_type: Any,
    source: Any,
) -> SkillMetadata:
    """从受支持安装源安装一个 skill package。"""

    target_scope = _parse_writable_scope(scope)
    target_root = _writable_root(workspace, user_skill_root, target_scope)
    clean_source_type = str(source_type or "github").strip().lower()
    if clean_source_type != "github":
        raise SkillManagementError("skill install source_type must be github")

    try:
        github_source = parse_github_skill_source(source)
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = fetch_github_skill_package(github_source, Path(tmp))
            validation = validate_skill_package(source_dir, root=source_dir, scope=target_scope)
            if not validation.ok:
                raise SkillManagementError(f"invalid skill package: {validation.errors[0].message}")
            imported = parse_skill_file(
                source_dir / SKILL_FILE_NAME,
                SkillRoot(path=source_dir.resolve(), scope=target_scope),
            )
            target_dir = target_root / _slugify(imported.name)
            if _has_symlink_component(target_dir, target_root):
                raise SkillManagementError("skill path contains a symlink")
            if target_dir.exists():
                raise SkillManagementError("skill already exists")
            _copy_skill_directory(source_dir, target_dir)
            write_install_record(target_dir, github_install_record(github_source))
    except SkillInstallError as exc:
        raise SkillManagementError(str(exc)) from exc

    return parse_skill_file(
        target_dir / SKILL_FILE_NAME,
        SkillRoot(path=target_root.resolve(), scope=target_scope),
    )


def reinstall_skill(skill: SkillMetadata) -> SkillMetadata:
    """按安装来源重新安装一个 GitHub skill package。"""

    _ensure_writable_skill(skill)
    record = read_install_record(skill.directory)
    if not record or record.get("source_type") != "github":
        raise SkillManagementError("only GitHub-installed skills can be reinstalled")

    try:
        github_source = parse_github_skill_source(record.get("source"))
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = fetch_github_skill_package(github_source, Path(tmp))
            validation = validate_skill_package(source_dir, root=source_dir, scope=skill.scope)
            if not validation.ok:
                raise SkillManagementError(f"invalid skill package: {validation.errors[0].message}")
            parse_skill_file(
                source_dir / SKILL_FILE_NAME,
                SkillRoot(path=source_dir.resolve(), scope=skill.scope),
            )
            replacement_dir = _prepared_replacement_directory(source_dir, skill.directory)
            _replace_skill_directory(skill.directory, replacement_dir)
            write_install_record(skill.directory, github_install_record(github_source))
    except SkillInstallError as exc:
        raise SkillManagementError(str(exc)) from exc

    return parse_skill_file(skill.path, SkillRoot(path=skill.root, scope=skill.scope))


def read_skill_for_management(skill: SkillMetadata) -> tuple[SkillMetadata, str]:
    """读取 catalog 中 skill 的正文部分，供用户编辑。"""

    _ensure_writable_skill(skill)
    contents = skill.path.read_text(encoding="utf-8", errors="replace")
    return skill, _extract_body(contents)


def list_skill_resources(skill: SkillMetadata) -> list[dict[str, Any]]:
    """列出标准资源目录下可编辑的文件。"""

    _ensure_writable_skill(skill)
    resources: list[dict[str, Any]] = []
    for dirname in SKILL_STANDARD_DIRS:
        resource_dir = skill.directory / dirname
        if not resource_dir.exists():
            continue
        if resource_dir.is_symlink():
            raise SkillManagementError("symlinked skill resources cannot be managed")
        if not resource_dir.is_dir():
            continue
        for path in sorted(resource_dir.rglob("*")):
            if path.is_symlink() or _has_symlink_component(path, skill.directory):
                raise SkillManagementError("symlinked skill resources cannot be managed")
            if path.is_file():
                resources.append(_resource_payload(skill, path))
    return resources


def read_skill_resource_for_management(
    skill: SkillMetadata,
    *,
    resource: Any,
) -> tuple[dict[str, Any], str]:
    """读取标准资源目录下的一个文本资源，供用户编辑。"""

    path = _resolve_managed_resource_path(skill, resource, must_exist=True)
    payload = _resource_payload(skill, path)
    return payload, path.read_text(encoding="utf-8", errors="replace")


def write_skill_resource(
    skill: SkillMetadata,
    *,
    resource: Any,
    content: Any,
) -> dict[str, Any]:
    """写入标准资源目录下的一个文本资源。"""

    path = _resolve_managed_resource_path(skill, resource, must_exist=False)
    text = str(content or "")
    if len(text.encode("utf-8")) > DEFAULT_SKILL_PACKAGE_SPEC.max_file_bytes:
        raise SkillManagementError("skill resource is too large")
    path.parent.mkdir(parents=True, exist_ok=True)
    if _has_symlink_component(path.parent, skill.directory):
        raise SkillManagementError("symlinked skill resources cannot be managed")
    path.write_text(text, encoding="utf-8")
    return _resource_payload(skill, path)


def delete_skill_resource(skill: SkillMetadata, *, resource: Any) -> dict[str, Any]:
    """删除标准资源目录下的一个文本资源。"""

    path = _resolve_managed_resource_path(skill, resource, must_exist=True)
    payload = _resource_payload(skill, path)
    path.unlink()
    return payload


def update_skill(
    *,
    skill: SkillMetadata,
    name: Any,
    description: Any,
    short_description: Any = None,
    icon: Any = None,
    allow_implicit_invocation: Any = True,
    content: Any = None,
) -> SkillMetadata:
    """更新一个已在当前 catalog 中的 repo/user skill。"""

    _ensure_writable_skill(skill)
    skill_name = _clean_name(name)
    skill_description = _clean_text(description, "description", MAX_DESCRIPTION_LEN)
    skill_short_description = _clean_optional_text(
        short_description,
        "short_description",
        MAX_DESCRIPTION_LEN,
    )
    skill_icon = _clean_optional_text(icon, "icon", 64)
    body = _clean_body(content)
    skill.path.write_text(
        _render_skill_markdown(
            name=skill_name,
            description=skill_description,
            short_description=skill_short_description,
            icon=skill_icon,
            allow_implicit_invocation=bool(allow_implicit_invocation),
            body=body,
        ),
        encoding="utf-8",
    )
    return parse_skill_file(skill.path, SkillRoot(path=skill.root, scope=skill.scope))


def delete_skill(skill: SkillMetadata) -> None:
    """删除一个已在当前 catalog 中的 repo/user skill 目录。"""

    _ensure_writable_skill(skill)
    if skill.directory == skill.root:
        skill.path.unlink(missing_ok=True)
        return
    shutil.rmtree(skill.directory)


def _parse_writable_scope(scope: str) -> SkillScope:
    clean_scope = str(scope or "repo").strip().lower()
    if clean_scope == SkillScope.REPO.value:
        return SkillScope.REPO
    if clean_scope == SkillScope.USER.value:
        return SkillScope.USER
    raise SkillManagementError("skill scope must be repo or user")


def _parse_package_template(value: Any) -> str:
    template = str(value or SKILL_PACKAGE_TEMPLATE_BASIC).strip().lower()
    if template == "package":
        template = SKILL_PACKAGE_TEMPLATE_STANDARD
    if template not in SKILL_PACKAGE_TEMPLATES:
        raise SkillManagementError("skill package_template must be basic or standard")
    return template


def _writable_root(
    workspace: WorkspaceContext,
    user_skill_root: Path | None,
    scope: SkillScope,
) -> Path:
    if scope == SkillScope.REPO:
        root = workspace.project_root / AGENTS_DIR_NAME / SKILLS_DIR_NAME
    else:
        root = (user_skill_root or (Path.home() / AGENTS_DIR_NAME / SKILLS_DIR_NAME)).expanduser()
    if _has_existing_symlink_parent(
        root,
        root=workspace.project_root if scope == SkillScope.REPO else None,
    ):
        raise SkillManagementError("skill root contains a symlink")
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise SkillManagementError("skill root is not a writable directory")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _resolve_import_source(workspace: WorkspaceContext, source_path: Any) -> Path:
    raw_text = str(source_path or "").strip()
    if not raw_text:
        raise SkillManagementError("skill import source is required")

    candidate = Path(raw_text).expanduser()
    if not candidate.is_absolute():
        candidate = workspace.current_dir / candidate
    if candidate.is_symlink():
        raise SkillManagementError("skill import source contains a symlink")
    resolved = candidate.resolve()

    if resolved.is_dir():
        skill_path = resolved / SKILL_FILE_NAME
    elif resolved.is_file() and resolved.name == SKILL_FILE_NAME:
        skill_path = resolved
    else:
        raise SkillManagementError("skill import source must be a SKILL.md file or a directory containing SKILL.md")

    if skill_path.is_symlink() or not skill_path.exists() or not skill_path.is_file():
        raise SkillManagementError("skill import source does not contain a readable SKILL.md")
    return skill_path


def _copy_skill_directory(source_dir: Path, target_dir: Path) -> None:
    for path in source_dir.rglob("*"):
        if path.is_symlink():
            raise SkillManagementError("skill import source contains a symlink")
    shutil.copytree(source_dir, target_dir, symlinks=False)


def _prepared_replacement_directory(source_dir: Path, target_dir: Path) -> Path:
    replacement_dir = Path(
        tempfile.mkdtemp(
            prefix=target_dir.name + "-reinstall-",
            dir=target_dir.parent,
        )
    )
    shutil.rmtree(replacement_dir)
    _copy_skill_directory(source_dir, replacement_dir)
    return replacement_dir


def _replace_skill_directory(target_dir: Path, replacement_dir: Path) -> None:
    backup_dir = Path(
        tempfile.mkdtemp(
            prefix=target_dir.name + "-backup-",
            dir=target_dir.parent,
        )
    )
    shutil.rmtree(backup_dir)
    target_dir.rename(backup_dir)
    try:
        replacement_dir.rename(target_dir)
    except OSError:
        if target_dir.exists():
            shutil.rmtree(target_dir)
        backup_dir.rename(target_dir)
        raise
    shutil.rmtree(backup_dir)


def _ensure_writable_skill(skill: SkillMetadata) -> None:
    if skill.scope not in {SkillScope.REPO, SkillScope.USER}:
        raise SkillManagementError("only repo and user skills can be managed")
    if skill.path.is_symlink() or _has_symlink_component(skill.path, skill.root):
        raise SkillManagementError("symlinked skill paths cannot be managed")
    if not _is_relative_to(skill.path, skill.root):
        raise SkillManagementError("skill path escaped its root")


def _resolve_managed_resource_path(
    skill: SkillMetadata,
    resource: Any,
    *,
    must_exist: bool,
) -> Path:
    _ensure_writable_skill(skill)
    resource_text = str(resource or "").strip()
    if not resource_text:
        raise SkillManagementError("skill resource is required")
    resource_path = Path(resource_text)
    if resource_path.is_absolute():
        raise SkillManagementError("skill resource must be relative")
    if ".." in resource_path.parts:
        raise SkillManagementError("skill resource escaped its directory")
    if not resource_path.parts or resource_path.parts[0] not in SKILL_STANDARD_DIRS:
        raise SkillManagementError("skill resource must be under a standard resource directory")
    if resource_path.name == SKILL_FILE_NAME:
        raise SkillManagementError("SKILL.md is managed through the skill editor")

    candidate = skill.directory / resource_path
    if _has_symlink_component(candidate, skill.directory):
        raise SkillManagementError("symlinked skill resources cannot be managed")
    if not _is_relative_to(candidate, skill.directory):
        raise SkillManagementError("skill resource escaped its directory")
    if must_exist and (not candidate.exists() or not candidate.is_file()):
        raise SkillManagementError("skill resource is not available")
    if candidate.exists() and (candidate.is_symlink() or candidate.is_dir()):
        raise SkillManagementError("skill resource must be a file")
    return candidate.resolve()


def _resource_payload(skill: SkillMetadata, path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        size = resolved.stat().st_size
    except OSError:
        size = 0
    return {
        "resource": resolved.relative_to(skill.directory.resolve()).as_posix(),
        "path": resolved.as_posix(),
        "size": size,
    }


def _clean_name(value: Any) -> str:
    name = _clean_text(value, "name", MAX_NAME_LEN)
    if not NAME_RE.match(name):
        raise SkillManagementError("skill name must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    return name


def _clean_text(value: Any, field: str, max_len: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise SkillManagementError(f"skill {field} is required")
    if len(text) > max_len:
        raise SkillManagementError(f"skill {field} is too long")
    return text


def _clean_optional_text(value: Any, field: str, max_len: int) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return _clean_text(raw, field, max_len)


def _clean_body(value: Any, *, template: str = SKILL_PACKAGE_TEMPLATE_BASIC) -> str:
    body = str(value or "").strip()
    if body:
        return body
    if template == SKILL_PACKAGE_TEMPLATE_STANDARD:
        return STANDARD_PACKAGE_BODY
    return DEFAULT_SKILL_BODY


def _create_standard_package_layout(skill_dir: Path) -> None:
    for dirname in SKILL_STANDARD_DIRS:
        (skill_dir / dirname).mkdir(parents=True, exist_ok=True)
    (skill_dir / "references" / "README.md").write_text(
        DEFAULT_REFERENCE_README,
        encoding="utf-8",
    )
    (skill_dir / "examples" / "README.md").write_text(
        DEFAULT_EXAMPLE_README,
        encoding="utf-8",
    )


def _slugify(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-").lower() or "skill"


def _render_skill_markdown(
    *,
    name: str,
    description: str,
    short_description: str | None,
    icon: str | None,
    allow_implicit_invocation: bool,
    body: str,
) -> str:
    frontmatter: dict[str, Any] = {
        "name": name,
        "description": description,
    }
    metadata: dict[str, str] = {}
    if short_description:
        metadata["short-description"] = short_description
    if icon:
        metadata["icon"] = icon
    if metadata:
        frontmatter["metadata"] = metadata
    frontmatter["policy"] = {"allow_implicit_invocation": allow_implicit_invocation}
    yaml_text = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return f"---\n{yaml_text}\n---\n\n{body.strip()}\n"


def _extract_body(contents: str) -> str:
    if not contents.startswith("---"):
        return contents
    lines = contents.splitlines()
    if not lines or lines[0].strip() != "---":
        return contents
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :]).strip()
    return contents


def _has_symlink_component(path: Path, root: Path) -> bool:
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


def _has_existing_symlink_parent(path: Path, *, root: Path | None = None) -> bool:
    if root is not None:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return False
        current = root
        parts = relative.parts
    else:
        current = Path(path.anchor) if path.is_absolute() else Path(".")
        parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        current = current / part
        if current.exists() and current.is_symlink():
            return True
    return False


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError, SkillParseError):
        return False
