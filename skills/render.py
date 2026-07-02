"""渲染模型可见的 skills catalog。"""

from __future__ import annotations

from skills.models import SkillLoadOutcome, SkillMetadata


DEFAULT_SKILLS_BUDGET_CHARS = 8_000


def render_available_skills(
    outcome: SkillLoadOutcome,
    *,
    budget_chars: int = DEFAULT_SKILLS_BUDGET_CHARS,
) -> tuple[str, str | None]:
    """把可隐式调用的 skills 渲染为系统提示片段。"""

    visible_skills = outcome.visible_skills()
    if not visible_skills:
        return "", None

    lines = [
        "<skills_instructions>",
        "## Skills",
        (
            "A skill is a reusable workflow stored in a SKILL.md file. "
            "The list below contains only metadata; read a skill before following it."
        ),
        "### Available skills",
    ]

    omitted = 0
    used = sum(len(line) + 1 for line in lines)
    rendered_lines: list[str] = []
    for skill in visible_skills:
        line = _skill_line(skill)
        if used + len(line) + 1 > budget_chars:
            omitted += 1
            continue
        rendered_lines.append(line)
        used += len(line) + 1

    if not rendered_lines:
        return "", "skills catalog exceeded the context budget and was omitted"
    lines.extend(rendered_lines)
    if omitted:
        lines.append(f"- {omitted} additional skills omitted from this bounded skills list.")

    lines.extend(
        [
            "### How to use skills",
            "- If the user explicitly selects or names a skill, use it for this turn.",
            "- If the task clearly matches a skill description, call read_skill before taking task actions.",
            "- When a SKILL.md references relative files, use read_skill_resource with that skill path.",
            "- Skills do not grant extra filesystem, command, or network permissions.",
            "</skills_instructions>",
        ]
    )
    warning = None
    if omitted:
        warning = "Some skills were omitted from the bounded skills catalog."
    return "\n".join(lines), warning


def _skill_line(skill: SkillMetadata) -> str:
    description = skill.short_description or skill.description
    return f"- {skill.name}: {description} (file: {skill.path.as_posix()})"
