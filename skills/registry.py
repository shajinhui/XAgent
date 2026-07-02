"""可安装 Skills registry。"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen

import yaml

from skills.installer import SkillInstallError, github_request, parse_github_skill_source
from skills.loader import MAX_DESCRIPTION_LEN, MAX_NAME_LEN


DEFAULT_REGISTRY_OWNER = "openai"
DEFAULT_REGISTRY_REPO = "skills"
DEFAULT_REGISTRY_REF = "main"
DEFAULT_REGISTRY_PATH = "skills/.curated"
MAX_REGISTRY_BYTES = 1024 * 1024
MAX_REGISTRY_SKILL_BYTES = 256 * 1024
MAX_REGISTRY_ENTRIES = 200
REGISTRY_USER_AGENT = "codex-mini-skill-registry"


@dataclass(frozen=True)
class SkillRegistryEntry:
    """registry 中一个可安装 skill 条目。"""

    id: str
    name: str
    description: str
    source_type: str
    source: str
    tags: tuple[str, ...] = ()
    icon: str | None = None
    version: str | None = None
    dependencies: dict[str, Any] | None = None
    ref: str | None = None
    path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "source_type": self.source_type,
            "source": self.source,
            "tags": list(self.tags),
        }
        if self.version:
            payload["version"] = self.version
        if self.icon:
            payload["icon"] = self.icon
        if self.dependencies:
            payload["dependencies"] = self.dependencies
        if self.ref:
            payload["ref"] = self.ref
        if self.path:
            payload["path"] = self.path
        return payload


@dataclass(frozen=True)
class SkillRegistryLoadError:
    """registry 加载时的非致命错误。"""

    source: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "message": self.message,
        }


@dataclass(frozen=True)
class SkillRegistryOutcome:
    """registry 加载结果。"""

    skills: tuple[SkillRegistryEntry, ...]
    errors: tuple[SkillRegistryLoadError, ...] = ()

    def find(self, identifier: Any) -> SkillRegistryEntry | None:
        """按 id 精确匹配，或在名称唯一时按 name 匹配。"""

        text = str(identifier or "").strip()
        if not text:
            return None
        for skill in self.skills:
            if skill.id == text:
                return skill
        matches = [skill for skill in self.skills if skill.name == text]
        if len(matches) == 1:
            return matches[0]
        return None


class SkillRegistryError(ValueError):
    """registry 条目解析错误。"""


def load_installable_skills() -> SkillRegistryOutcome:
    """加载默认 curated registry。"""

    return load_github_skill_registry(
        owner=DEFAULT_REGISTRY_OWNER,
        repo=DEFAULT_REGISTRY_REPO,
        ref=DEFAULT_REGISTRY_REF,
        path=DEFAULT_REGISTRY_PATH,
    )


def load_github_skill_registry(
    *,
    owner: str,
    repo: str,
    ref: str,
    path: str,
) -> SkillRegistryOutcome:
    """从 GitHub contents API 加载一个目录型 skill registry。"""

    registry_source = f"https://github.com/{owner}/{repo}/tree/{ref}/{path.strip('/')}"
    errors: list[SkillRegistryLoadError] = []
    try:
        listing = _fetch_github_json(_github_contents_url(owner, repo, path, ref), MAX_REGISTRY_BYTES)
    except SkillRegistryError as exc:
        return SkillRegistryOutcome(
            skills=(),
            errors=(SkillRegistryLoadError(source=registry_source, message=str(exc)),),
        )

    if not isinstance(listing, list):
        return SkillRegistryOutcome(
            skills=(),
            errors=(SkillRegistryLoadError(source=registry_source, message="registry path is not a directory"),),
        )

    skills: list[SkillRegistryEntry] = []
    for item in listing[:MAX_REGISTRY_ENTRIES]:
        if not isinstance(item, dict) or item.get("type") != "dir":
            continue
        entry_source = item.get("html_url") if isinstance(item.get("html_url"), str) else registry_source
        try:
            directory_name = _clean_identifier(item.get("name"), "registry id", 128)
            directory_path = _clean_relative_path(item.get("path"), "registry path")
            skill_source = f"https://github.com/{owner}/{repo}/tree/{ref}/{directory_path}"
            entry_source = skill_source
            skill_markdown = _fetch_github_skill_markdown(owner, repo, ref, directory_path)
            skills.append(
                _parse_registry_entry(
                    registry_id=directory_name,
                    source=skill_source,
                    ref=ref,
                    path=directory_path,
                    contents=skill_markdown,
                )
            )
        except SkillRegistryError as exc:
            errors.append(SkillRegistryLoadError(source=entry_source, message=str(exc)))

    return SkillRegistryOutcome(
        skills=tuple(sorted(skills, key=lambda skill: (skill.name.lower(), skill.id))),
        errors=tuple(errors),
    )


def _fetch_github_skill_markdown(owner: str, repo: str, ref: str, directory_path: str) -> str:
    payload = _fetch_github_json(
        _github_contents_url(owner, repo, f"{directory_path.rstrip('/')}/SKILL.md", ref),
        MAX_REGISTRY_SKILL_BYTES,
    )
    if not isinstance(payload, dict):
        raise SkillRegistryError("SKILL.md response is invalid")
    if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        raise SkillRegistryError("SKILL.md response is not base64 content")
    try:
        decoded = base64.b64decode(payload["content"], validate=False)
    except (ValueError, TypeError) as exc:
        raise SkillRegistryError("SKILL.md content is not valid base64") from exc
    if len(decoded) > MAX_REGISTRY_SKILL_BYTES:
        raise SkillRegistryError("SKILL.md is too large")
    return decoded.decode("utf-8", errors="replace")


def _parse_registry_entry(
    *,
    registry_id: str,
    source: str,
    ref: str,
    path: str,
    contents: str,
) -> SkillRegistryEntry:
    try:
        parse_github_skill_source(source)
    except SkillInstallError as exc:
        raise SkillRegistryError(str(exc)) from exc

    frontmatter = _extract_frontmatter(contents)
    if frontmatter is None:
        raise SkillRegistryError("missing YAML frontmatter delimited by ---")
    try:
        parsed = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as exc:
        raise SkillRegistryError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SkillRegistryError("frontmatter must be a mapping")

    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    name = _clean_text(parsed.get("name"), "name", MAX_NAME_LEN)
    description = _clean_text(parsed.get("description"), "description", MAX_DESCRIPTION_LEN)
    tags = _clean_tags(parsed.get("tags") or metadata.get("tags"))
    icon = _clean_optional_text(parsed.get("icon") or metadata.get("icon"), 64)
    version = _clean_optional_text(parsed.get("version") or metadata.get("version"), 64)
    dependencies = parsed.get("dependencies") if isinstance(parsed.get("dependencies"), dict) else None

    return SkillRegistryEntry(
        id=registry_id,
        name=name,
        description=description,
        source_type="github",
        source=source,
        tags=tags,
        icon=icon,
        version=version,
        dependencies=dependencies,
        ref=ref,
        path=path,
    )


def _fetch_github_json(url: str, max_bytes: int) -> Any:
    request = github_request(
        url,
        accept="application/vnd.github+json",
        user_agent=REGISTRY_USER_AGENT,
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read(max_bytes + 1)
    except OSError as exc:
        raise SkillRegistryError(f"failed to load registry: {exc}") from exc
    if len(payload) > max_bytes:
        raise SkillRegistryError("registry response is too large")
    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SkillRegistryError("registry response is not valid JSON") from exc


def _github_contents_url(owner: str, repo: str, path: str, ref: str) -> str:
    encoded_path = "/".join(quote(part, safe="") for part in path.strip("/").split("/") if part)
    encoded_ref = quote(ref, safe="")
    return f"https://api.github.com/repos/{owner}/{repo}/contents/{encoded_path}?ref={encoded_ref}"


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


def _clean_identifier(value: Any, field: str, max_len: int) -> str:
    text = _clean_text(value, field, max_len)
    if "/" in text or text in {".", ".."}:
        raise SkillRegistryError(f"invalid {field}")
    return text


def _clean_relative_path(value: Any, field: str) -> str:
    text = str(value or "").strip().strip("/")
    parts = [part for part in text.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise SkillRegistryError(f"invalid {field}")
    return "/".join(parts)


def _clean_text(value: Any, field: str, max_len: int) -> str:
    text = _clean_optional_text(value, max_len)
    if not text:
        raise SkillRegistryError(f"missing field {field}")
    return text


def _clean_optional_text(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    if len(text) > max_len:
        raise SkillRegistryError(f"field is too long: max {max_len} chars")
    return text


def _clean_tags(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SkillRegistryError("tags must be a list")
    tags: list[str] = []
    for item in value:
        tag = _clean_optional_text(item, 48)
        if tag and tag not in tags:
            tags.append(tag)
    return tuple(tags[:12])
