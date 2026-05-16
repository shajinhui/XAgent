from __future__ import annotations

from typing import List

from tools.core.types import ToolExecutionContext, ToolResult
from tools.interaction.ask_user_spec import AskUserArgs, AskUserOption, META, schema


def run(_ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    args = AskUserArgs(**payload)
    question = _clean_text(args.question)
    options = _normalize_options(args.options)

    if not question:
        raise ValueError("question 不能为空")
    if not options and not args.allow_freeform:
        raise ValueError("没有 options 时必须允许 freeform 回答")

    return ToolResult(
        ok=False,
        content=question,
        metadata={
            "tool": META.name,
            "category": "clarification",
            "user_interaction_action": "ask",
            "question": question,
            "options": options,
            "allow_freeform": args.allow_freeform,
        },
    )


def _normalize_options(options: List[AskUserOption]) -> list[dict]:
    normalized: list[dict] = []
    used_ids: set[str] = set()
    recommended_seen = False

    for index, option in enumerate(options, start=1):
        label = _clean_text(option.label)
        if not label:
            continue

        raw_id = _clean_text(option.id or "") or f"option_{index}"
        option_id = _unique_option_id(raw_id, used_ids)
        used_ids.add(option_id)

        recommended = bool(option.recommended) and not recommended_seen
        recommended_seen = recommended_seen or recommended

        normalized_option = {
            "id": option_id,
            "label": label,
            "recommended": recommended,
        }
        description = _clean_text(option.description or "")
        if description:
            normalized_option["description"] = description
        normalized.append(normalized_option)

    return normalized


def _unique_option_id(raw_id: str, used_ids: set[str]) -> str:
    safe_id = "_".join(raw_id.lower().split())[:64] or "option"
    candidate = safe_id
    suffix = 2
    while candidate in used_ids:
        candidate = f"{safe_id}_{suffix}"
        suffix += 1
    return candidate


def _clean_text(value: str) -> str:
    return " ".join(str(value).strip().split())
