"""模型配置与 LiteLLM 请求参数组装。

这里集中处理默认模型、低成本模型、通用 API_KEY/API_BASE，以及 DeepSeek thinking
兼容参数，避免 CLI、WebSocket 和标题生成路径各自拼一套请求参数。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROVIDER_MODEL_ENDPOINTS = {
    "deepseek": "https://api.deepseek.com",
}
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


def configure_litellm_environment() -> None:
    """配置 LiteLLM 的本地运行默认值。

    LiteLLM 默认会联网拉取模型价格表；本项目不依赖这份远程价格表，且本地开发时
    网络/SSL 波动会产生大量无意义 warning，所以默认使用包内置的本地备份。
    """

    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


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
            "model": build_litellm_model_name(self.model),
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
        provider = os.getenv("MODEL_PROVIDER", "").strip().lower()
        model = self.model.lower()
        return model.startswith("deepseek/") or model.startswith("deepseek-") or (
            provider == "deepseek" and "/" not in model
        )

    def _deepseek_reasoning_effort(self) -> str:
        if self.reasoning_effort == "max":
            return "max"
        return "high"


def normalize_model_name(value: Any) -> str | None:
    """校验前端传入的模型名，兼容 `provider/model` 与服务商原始 id。"""

    if not isinstance(value, str):
        return None

    model = value.strip()
    if not model or len(model) > 160:
        return None
    if any(char.isspace() for char in model):
        return None
    return model


def build_default_model_name() -> str:
    """从主模型环境变量读取默认模型 id。"""

    return os.getenv("MODEL_NAME", "gpt-4o-mini").strip() or "gpt-4o-mini"


def build_low_cost_model_name(model_override: Any | None = None) -> str:
    """构建低成本模型名，优先使用显式覆盖，其次使用 LOW_COST_* 配置。"""

    normalized_override = normalize_model_name(model_override)
    if normalized_override:
        return normalized_override

    model = os.getenv("LOW_COST_MODEL_NAME", build_default_model_name()).strip()
    return model or build_default_model_name()


def build_litellm_model_name(model_name: str, provider: str | None = None) -> str:
    """把产品层模型 id 转成 LiteLLM 需要的调用模型名。

    前端和配置文件保存服务商原始 id，例如 `deepseek-v4-pro`；LiteLLM 调用 DeepSeek
    时仍需要 `deepseek/deepseek-v4-pro`，所以只在出站请求前做这一层内部适配。
    """

    normalized_model = normalize_model_name(model_name) or build_default_model_name()
    resolved_provider = (provider or os.getenv("MODEL_PROVIDER", "")).strip().lower()
    lower_model = normalized_model.lower()
    if "/" not in normalized_model and (
        resolved_provider == "deepseek" or lower_model.startswith("deepseek-")
    ):
        return f"deepseek/{normalized_model}"
    return normalized_model


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


def fetch_provider_model_options(
    provider: str | None = None,
    *,
    api_base: str | None = None,
    api_key: str | None = None,
    timeout_seconds: float = 3.0,
    opener: Callable[..., Any] | None = None,
) -> List[str]:
    """从服务商 `/models` 接口拉取可用模型列表。

    当前先支持 DeepSeek：请求 `${API_BASE或默认DeepSeek地址}/models`。
    成功时直接返回服务商给出的模型 id，不再额外加 provider 前缀；任何网络或格式错误都返回
    空列表，让 ready 事件可以安全回退到本地 `MODEL_OPTIONS`。
    """

    resolved_provider = (provider or os.getenv("MODEL_PROVIDER", "openai")).strip().lower()
    default_base = PROVIDER_MODEL_ENDPOINTS.get(resolved_provider)
    if not default_base:
        return []

    resolved_api_key = (api_key if api_key is not None else os.getenv("API_KEY", "")).strip()
    if not resolved_api_key:
        return []

    resolved_api_base = (api_base if api_base is not None else os.getenv("API_BASE", "")).strip()
    endpoint = f"{(resolved_api_base or default_base).rstrip('/')}/models"
    request = Request(
        endpoint,
        headers={
            "Authorization": f"Bearer {resolved_api_key}",
            "Accept": "application/json",
        },
    )

    try:
        open_fn = opener or urlopen
        with open_fn(request, timeout=timeout_seconds) as response:
            raw_payload = response.read().decode("utf-8")
        payload = json.loads(raw_payload)
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError):
        return []

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []

    options: List[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        normalized_model = normalize_model_name(model_id)
        if normalized_model and normalized_model not in options:
            options.append(normalized_model)
    return options


def build_model_options() -> List[str]:
    """生成前端下拉可展示的模型候选列表。

    规则保持简单：远程 `/models` 成功就信任远程列表；失败时只回退到 env 中的
    `MODEL_OPTIONS`。默认主模型通过 `default_model` 单独返回，不混进候选列表。
    """

    remote_options = fetch_provider_model_options()
    if remote_options:
        return remote_options

    return _parse_model_option_list(os.getenv("MODEL_OPTIONS", ""))


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
