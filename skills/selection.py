"""解析本轮显式选择的 skills。"""

from __future__ import annotations

import re
from typing import Any

from skills.models import SkillInjection, SkillLoadOutcome, SkillMetadata
from skills.resources import SkillResourceError, SkillResourceResolver


MENTION_RE = re.compile(r"(?<![\w$])\$([A-Za-z0-9][A-Za-z0-9_-]{0,63})")


def collect_selected_skills(
    *,
    outcome: SkillLoadOutcome,
    user_input: str,
    selected_skills: Any = None,
) -> tuple[list[SkillMetadata], list[str]]:
    """收集 selected_skills 和文本 `$skill-name` 中显式选中的 skills。"""

    selected: list[SkillMetadata] = []
    warnings: list[str] = []
    seen_paths: set[str] = set()

    for raw in selected_skills if isinstance(selected_skills, list) else []:
        if not isinstance(raw, dict):
            continue
        skill = _skill_from_selected_item(outcome, raw)
        if skill is None:
            warnings.append("selected skill is not available in the current catalog")
            continue
        if skill.path.as_posix() in seen_paths:
            continue
        selected.append(skill)
        seen_paths.add(skill.path.as_posix())

    for name in MENTION_RE.findall(user_input or ""):
        if any(skill.name == name for skill in selected):
            continue
        skill = outcome.unique_by_name(name)
        if skill is None:
            if any(candidate.name == name for candidate in outcome.skills):
                warnings.append(f"skill name is ambiguous: {name}")
            continue
        if skill.path.as_posix() in seen_paths:
            continue
        selected.append(skill)
        seen_paths.add(skill.path.as_posix())

    return selected, warnings


def build_skill_injections(
    *,
    selected: list[SkillMetadata],
    resolver: SkillResourceResolver,
) -> tuple[list[SkillInjection], list[str]]:
    """读取显式选择的完整 SKILL.md，生成本轮临时注入。"""

    injections: list[SkillInjection] = []
    warnings: list[str] = []
    for skill in selected:
        try:
            resolved_skill, contents = resolver.read_skill(path=skill.path.as_posix())
        except SkillResourceError as exc:
            warnings.append(f"failed to load skill {skill.name}: {exc}")
            continue
        injections.append(
            SkillInjection(
                name=resolved_skill.name,
                path=resolved_skill.path,
                contents=contents,
                invocation_type="explicit",
            )
        )
    return injections, warnings


def _skill_from_selected_item(outcome: SkillLoadOutcome, raw: dict[str, Any]) -> SkillMetadata | None:
    path = str(raw.get("path") or "").strip()
    if path:
        return outcome.find_by_path(path)
    name = str(raw.get("name") or "").strip()
    if name:
        return outcome.unique_by_name(name)
    return None
