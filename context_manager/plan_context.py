"""Plan Mode 的模型可见上下文片段。"""

from __future__ import annotations

from typing import Any, Dict, List

MAX_PLAN_MARKDOWN_LENGTH = 8000


def render_confirmed_plan_user_message(user_input: str, plan: Dict[str, Any] | None) -> str:
    """把已确认计划合并进当前用户消息，供模型执行本轮任务时遵守。"""

    if not plan:
        return user_input

    plan_markdown = _normalize_plan_markdown(plan.get("plan_markdown"))
    items = _normalize_plan_items(plan.get("items"))
    if not plan_markdown and not items:
        return user_input

    lines = [
        "## 用户原始请求",
        user_input,
        "",
        "## 已确认计划",
        "用户已经审阅并确认以下计划。本轮执行必须优先按这份计划推进；",
        "不要忽略、重排或扩展计划，除非用户新指令要求，或执行中发现安全/技术必要，并在回复中说明原因。",
        "",
    ]
    if plan_markdown:
        lines.append(plan_markdown)
    else:
        for index, item in enumerate(items, start=1):
            lines.append(f"{index}. [{item['status']}] {item['step']}")

    return "\n".join(lines)


def _normalize_plan_markdown(value: Any) -> str:
    """清洗计划书正文，限制恢复上下文里的持久 payload 尺寸。"""

    markdown = str(value or "").strip()
    if not markdown:
        return ""
    if len(markdown) <= MAX_PLAN_MARKDOWN_LENGTH:
        return markdown
    return f"{markdown[:MAX_PLAN_MARKDOWN_LENGTH]}\n\n..."


def _normalize_plan_items(value: Any) -> List[Dict[str, str]]:
    """只保留可呈现给模型的计划步骤，避免把任意 payload 直接注入上下文。"""

    if not isinstance(value, list):
        return []

    items: List[Dict[str, str]] = []
    for raw_item in value:
        if not isinstance(raw_item, dict):
            continue
        step = str(raw_item.get("step") or "").strip()
        if not step:
            continue
        status = str(raw_item.get("status") or "pending").strip() or "pending"
        items.append({"step": step, "status": status})
    return items
