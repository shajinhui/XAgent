"""模型配置与 LiteLLM 请求参数组装。

这里集中处理默认模型、低成本模型、通用 API_KEY/API_BASE，以及 DeepSeek thinking
兼容参数，避免 CLI、WebSocket 和标题生成路径各自拼一套请求参数。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List


DEFAULT_MODEL_OPTIONS = (
    "openai/gpt-4o-mini",
    "deepseek/deepseek-chat",
    "deepseek/deepseek-reasoner",
)
REASONING_EFFORT_OPTIONS = ("off", "low", "medium", "high", "max")
REASONING_EFFORT_ALIASES = {
    "": "off",
    "none": "off",
    "disabled": "off",
    "false": "off",
    "0": "off",
    "minimal": "low",
    "normal": "medium",
    "xhigh": "max",
}


@dataclass(frozen=True)
class ModelRequestConfig:
    """一次模型请求所需的模型名与推理强度。"""

    model: str
    reasoning_effort: str = "off"

    def as_dict(self) -> Dict[str, Any]:
        """返回可写入 transcript 或发给前端的安全配置快照。"""

        return {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
        }

    def completion_kwargs(self) -> Dict[str, Any]:
        """转换为 LiteLLM completion 可直接接收的参数。"""

        kwargs: Dict[str, Any] = {
            "model": self.model,
            **build_api_kwargs(),
        }
        if self._uses_deepseek_thinking_api():
            # DeepSeek thinking 模式禁用时必须显式发送 thinking.disabled；
            # 开启 thinking 时不传 temperature 等采样参数，因为官方说明这些参数不生效。
            kwargs["extra_body"] = {
                "thinking": {
                    "type": "disabled" if self.reasoning_effort == "off" else "enabled"
                }
            }
            if self.reasoning_effort != "off":
                kwargs["reasoning_effort"] = self._deepseek_reasoning_effort()
                return kwargs

            kwargs["temperature"] = 0
            return kwargs

        kwargs["temperature"] = 0
        if self.reasoning_effort != "off":
            kwargs["reasoning_effort"] = self.reasoning_effort
        return kwargs

    def _uses_deepseek_thinking_api(self) -> bool:
        return self.model.startswith("deepseek/")

    def _deepseek_reasoning_effort(self) -> str:
        if self.reasoning_effort == "max":
            return "max"
        return "high"


def normalize_model_name(value: Any) -> str | None:
    """校验前端传入的 provider/model 形式模型名。"""

    if not isinstance(value, str):
        return None

    model = value.strip()
    if not model or len(model) > 160:
        return None
    if any(char.isspace() for char in model):
        return None
    if "/" not in model:
        return None
    return model


def build_default_model_name() -> str:
    """从主模型环境变量构建默认模型名。"""

    provider = os.getenv("MODEL_PROVIDER", "openai").strip()
    model = os.getenv("MODEL_NAME", "gpt-4o-mini").strip()
    return f"{provider}/{model}"


def build_low_cost_model_name(model_override: Any | None = None) -> str:
    """构建低成本模型名，优先使用显式覆盖，其次使用 LOW_COST_* 配置。"""

    normalized_override = normalize_model_name(model_override)
    if normalized_override:
        return normalized_override

    provider = os.getenv("LOW_COST_MODEL_PROVIDER", os.getenv("MODEL_PROVIDER", "openai")).strip()
    model = os.getenv("LOW_COST_MODEL_NAME", os.getenv("MODEL_NAME", "gpt-4o-mini")).strip()
    return f"{provider}/{model}"


def build_model_name(model_override: Any | None = None) -> str:
    """解析单次请求的模型覆盖，不合法时回落到默认模型。"""

    return normalize_model_name(model_override) or build_default_model_name()


def build_api_kwargs() -> Dict[str, str]:
    """读取通用 API_KEY/API_BASE，避免 provider-specific key 命名扩散。"""

    kwargs: Dict[str, str] = {}
    api_key = os.getenv("API_KEY", "").strip()
    if api_key:
        kwargs["api_key"] = api_key

    api_base = os.getenv("API_BASE", "").strip()
    if api_base:
        kwargs["api_base"] = api_base
    return kwargs


def normalize_reasoning_effort(value: Any) -> str:
    """归一化前端或环境变量中的推理强度配置。"""

    if value is None:
        value = os.getenv("REASONING_EFFORT", "off")
    if not isinstance(value, str):
        return "off"

    effort = value.strip().lower()
    effort = REASONING_EFFORT_ALIASES.get(effort, effort)
    if effort in REASONING_EFFORT_OPTIONS:
        return effort
    return "off"


def _parse_model_option_list(raw_value: str) -> List[str]:
    """解析逗号分隔的模型选项，并过滤非法模型名。"""

    options: List[str] = []
    for item in raw_value.split(","):
        model = normalize_model_name(item)
        if model and model not in options:
            options.append(model)
    return options


def build_model_options() -> List[str]:
    """生成前端下拉可展示的模型候选列表。"""

    options = [
        build_default_model_name(),
        *_parse_model_option_list(os.getenv("MODEL_OPTIONS", "")),
        *DEFAULT_MODEL_OPTIONS,
    ]
    return list(dict.fromkeys(option for option in options if normalize_model_name(option)))


def build_model_request_config(packet: Dict[str, Any] | None = None) -> ModelRequestConfig:
    """从客户端 packet 中提取本轮模型请求配置。"""

    packet = packet or {}
    return ModelRequestConfig(
        model=build_model_name(packet.get("model")),
        reasoning_effort=normalize_reasoning_effort(packet.get("reasoning_effort")),
    )


def build_model_config_payload() -> Dict[str, Any]:
    """生成 ready 事件中发给前端的模型配置快照。"""

    return {
        "default_model": build_default_model_name(),
        "model_options": build_model_options(),
        "reasoning_effort": normalize_reasoning_effort(None),
        "reasoning_effort_options": list(REASONING_EFFORT_OPTIONS),
    }
