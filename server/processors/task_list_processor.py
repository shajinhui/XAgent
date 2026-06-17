"""用户任务拆解与任务列表清洗。"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Tuple

from server.processors.title_processor import extract_completion_text
from server.runtime.model_config import (
    build_api_kwargs,
    build_litellm_model_name,
    build_low_cost_model_name,
    configure_litellm_environment,
)


TASK_LIST_MODEL_SOURCE = "low-cost-task-list"
TASK_STATUS_OPTIONS = {"pending", "in_progress", "completed"}
MAX_TASK_COUNT = 5
MAX_TASK_LABEL_LENGTH = 28


def sanitize_task_label(value: Any) -> str:
    """清洗单条任务文案，避免模型输出过长或带列表符号。"""

    label = " ".join(str(value or "").strip().split())
    label = re.sub(r"^[-*•\d.)\s]+", "", label).strip()
    if len(label) <= MAX_TASK_LABEL_LENGTH:
        return label
    return f"{label[:MAX_TASK_LABEL_LENGTH]}..."


def normalize_task_status(value: Any, index: int) -> str:
    """归一化小模型返回的任务状态；默认首项进行中，其余待处理。"""

    status = str(value or "").strip().lower()
    if status in {"done", "success", "completed", "complete"}:
        return "completed"
    if status in {"running", "current", "in_progress", "progress"}:
        return "in_progress"
    if status in TASK_STATUS_OPTIONS:
        return status
    return "in_progress" if index == 0 else "pending"


def normalize_task_list(value: Any) -> List[Dict[str, str]]:
    """把模型 JSON 输出规整为前端可直接渲染的 tasklist。"""

    if isinstance(value, dict):
        raw_items = value.get("tasks") or value.get("items") or value.get("plan") or []
    else:
        raw_items = value

    if not isinstance(raw_items, list):
        return []

    tasks: List[Dict[str, str]] = []
    for raw_item in raw_items:
        if isinstance(raw_item, dict):
            label = sanitize_task_label(
                raw_item.get("step") or raw_item.get("task") or raw_item.get("label")
            )
            status = normalize_task_status(raw_item.get("status"), len(tasks))
        else:
            label = sanitize_task_label(raw_item)
            status = normalize_task_status(None, len(tasks))

        if not label:
            continue
        tasks.append({"step": label, "status": status})
        if len(tasks) >= MAX_TASK_COUNT:
            break

    if tasks and not any(task["status"] == "in_progress" for task in tasks):
        first_pending = next((task for task in tasks if task["status"] == "pending"), tasks[0])
        first_pending["status"] = "in_progress"
    return tasks


def extract_task_json(raw_text: str) -> Any:
    """从模型输出中提取 JSON 对象或数组。"""

    text = raw_text.strip()
    if not text:
        return None

    fenced_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced_match:
        text = fenced_match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for open_char, close_char in (("[", "]"), ("{", "}")):
        start = text.find(open_char)
        end = text.rfind(close_char)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def fallback_task_list(user_input: str) -> List[Dict[str, str]]:
    """小模型失败时给 UI 一个稳定但保守的任务骨架。"""

    text = " ".join(user_input.strip().split())
    target = sanitize_task_label(text) or "处理用户任务"
    return [
        {"step": f"理解目标：{target}", "status": "in_progress"},
        {"step": "执行必要检查和修改", "status": "pending"},
        {"step": "汇总结果并交付", "status": "pending"},
    ]


def generate_task_list(
    user_input: str,
    completion_fn: Any | None = None,
) -> Tuple[List[Dict[str, str]], str]:
    """用低成本模型把用户输入拆成 3-5 条任务列表。"""

    text = " ".join(user_input.strip().split())
    if not text:
        return [], TASK_LIST_MODEL_SOURCE

    if completion_fn is None:
        configure_litellm_environment()
        from litellm import completion as completion_fn

    low_cost_model = build_low_cost_model_name()
    low_cost_provider = os.getenv("LOW_COST_MODEL_PROVIDER", os.getenv("MODEL_PROVIDER", "openai"))

    response = completion_fn(
        model=build_litellm_model_name(low_cost_model, low_cost_provider),
        messages=[
            {
                "role": "system",
                "content": (
                    "你负责把用户任务拆成 UI 可展示的简短任务列表。"
                    "只输出 JSON，不要解释。格式为："
                    "{\"tasks\":[{\"step\":\"步骤\",\"status\":\"in_progress\"},"
                    "{\"step\":\"步骤\",\"status\":\"pending\"}]}。"
                    "要求 3 到 5 步，中文，每步不超过 18 个字。"
                    "只能有一个 in_progress，通常是第一步。"
                ),
            },
            {"role": "user", "content": text[:1600]},
        ],
        temperature=0,
        max_tokens=256,
        extra_body={"thinking": {"type": "disabled"}},
        **build_api_kwargs(),
    )

    tasks = normalize_task_list(extract_task_json(extract_completion_text(response)))
    return (tasks or fallback_task_list(text), TASK_LIST_MODEL_SOURCE)
