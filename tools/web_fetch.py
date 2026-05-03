from __future__ import annotations

import os
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from tools.types import ToolExecutionContext, ToolMeta


META = ToolMeta(
    name="web_fetch",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=False,
    requires_approval=True,
)


class WebFetchArgs(BaseModel):
    url: str = Field(..., description="要抓取的 URL")
    timeout: int = Field(10, ge=1, le=30, description="超时时间（秒）")


def schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取网页文本（可选功能，默认关闭）",
            "parameters": WebFetchArgs.model_json_schema(),
        },
    }


def run(_ctx: ToolExecutionContext, payload: dict) -> str:
    if os.getenv("ENABLE_WEB_FETCH", "false").lower() != "true":
        raise PermissionError("web_fetch 默认关闭，请在 .env 中设置 ENABLE_WEB_FETCH=true")

    args = WebFetchArgs(**payload)
    req = Request(args.url, headers={"User-Agent": "codex-mini/0.2"})
    with urlopen(req, timeout=args.timeout) as resp:
        data = resp.read(200_000)
    return data.decode("utf-8", errors="replace")
