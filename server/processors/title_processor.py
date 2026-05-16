"""会话标题生成与标题文本清理。"""

from __future__ import annotations

from typing import Any, Dict, List

from server.protocol.serialization import object_to_dict
from server.runtime.model_config import build_api_kwargs, build_low_cost_model_name


CONVERSATION_TITLE_MODEL_SOURCE = "low-cost-first-user"


def sanitize_conversation_title(raw_title: str) -> str:
    """清理模型输出的标题，只保留短文本。"""

    title = " ".join(raw_title.strip().split())
    title = title.strip("`'\"“”‘’# ")
    for prefix in ("标题：", "标题:", "Title:", "Title："):
        if title.startswith(prefix):
            title = title[len(prefix) :].strip()
            break

    if not title:
        return "新对话"
    if len(title) <= 18:
        return title
    return f"{title[:18]}..."


def first_user_message_content(messages: List[Dict[str, str]]) -> str:
    """取出第一条非空 user 消息，作为标题生成的唯一语义来源。"""

    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if content:
            return content
    return ""


def normalize_title_messages(messages: Any) -> List[Dict[str, str]]:
    """过滤前端传来的标题上下文，只保留 user/assistant 文本。"""

    if not isinstance(messages, list):
        return []

    normalized: List[Dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        normalized.append({"role": role, "content": content[:1200]})

    return normalized[-8:]


def extract_completion_text(response: Any) -> str:
    """兼容 dict/Pydantic 对象形式的 LiteLLM completion 响应。"""

    response_dict = object_to_dict(response)
    choices = response_dict.get("choices")
    if choices is None:
        choices = getattr(response, "choices", [])
    if not choices:
        return ""

    choice = choices[0]
    choice_dict = object_to_dict(choice)
    message = choice_dict.get("message")
    if message is None:
        message = getattr(choice, "message", None)

    message_dict = object_to_dict(message)
    content = message_dict.get("content")
    if content is None:
        content = getattr(message, "content", "")
    return str(content or "")


def generate_conversation_title(
    messages: List[Dict[str, str]],
    completion_fn: Any | None = None,
) -> tuple[str, str]:
    """用低成本模型根据首条用户消息生成会话标题。"""

    first_user_content = first_user_message_content(messages)
    if not first_user_content:
        return "新对话", CONVERSATION_TITLE_MODEL_SOURCE

    if completion_fn is None:
        from litellm import completion as completion_fn

    response = completion_fn(
        model=build_low_cost_model_name(),
        messages=[
            {
                "role": "system",
                "content": (
                    "你负责给对话生成简短标题。"
                    "只根据用户第一句提问概括主题，输出一个中文短标题。"
                    "不要解释，不要加引号，不要加“标题：”前缀，长度控制在 18 个字以内。"
                ),
            },
            {
                "role": "user",
                "content": f"用户第一句提问：{first_user_content}",
            },
        ],
        temperature=0,
        max_tokens=32,
        # 标题属于轻量任务，不需要 thinking，也不能把 reasoning_content 写入历史。
        extra_body={"thinking": {"type": "disabled"}},
        **build_api_kwargs(),
    )
    title = sanitize_conversation_title(extract_completion_text(response))
    if title == "新对话":
        raise ValueError("conversation title model returned an empty title")
    return title, CONVERSATION_TITLE_MODEL_SOURCE
