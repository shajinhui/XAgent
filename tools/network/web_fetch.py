"""联网抓取工具，默认允许普通公网 HTTP/HTTPS 页面。"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from tools.core.types import ToolExecutionContext, ToolMeta


MAX_RESPONSE_BYTES = 200_000
DEFAULT_TIMEOUT_SECONDS = 10
ALLOWED_SCHEMES = {"http", "https"}

META = ToolMeta(
    name="web_fetch",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=False,
    requires_approval=False,
)


class WebFetchArgs(BaseModel):
    """web_fetch 工具入参。"""

    url: str = Field(..., description="要抓取的 URL")
    timeout: int = Field(DEFAULT_TIMEOUT_SECONDS, ge=1, le=30, description="超时时间（秒）")


def schema() -> dict:
    """返回供模型调用的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取公网 HTTP/HTTPS 网页文本，拒绝本地、内网和非 Web 协议地址",
            "parameters": WebFetchArgs.model_json_schema(),
        },
    }


def run(_ctx: ToolExecutionContext, payload: dict) -> str:
    """抓取公网网页文本，并在发起请求前拦截敏感地址。"""

    args = WebFetchArgs(**payload)
    _validate_public_web_url(args.url)
    req = Request(args.url, headers={"User-Agent": "codex-mini/0.2"})
    with urlopen(req, timeout=args.timeout) as resp:
        data = resp.read(MAX_RESPONSE_BYTES)
    return data.decode("utf-8", errors="replace")


def _validate_public_web_url(raw_url: str) -> None:
    """只允许公网 HTTP/HTTPS，避免模型借 web_fetch 探测本机或内网。"""

    parsed = urlparse(raw_url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise PermissionError("web_fetch 仅允许 HTTP/HTTPS URL")
    if not parsed.hostname:
        raise PermissionError("web_fetch URL 缺少主机名")
    if parsed.username or parsed.password:
        raise PermissionError("web_fetch URL 不允许包含用户名或密码")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise PermissionError("web_fetch 不允许访问 localhost")

    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise PermissionError("web_fetch URL 端口非法") from exc

    _ensure_public_hostname(hostname, port)


def _ensure_public_hostname(hostname: str, port: int) -> None:
    """校验主机解析结果，防止通过域名绕过内网地址拦截。"""

    direct_ip = _parse_ip_address(hostname)
    if direct_ip is not None:
        _ensure_public_ip(direct_ip)
        return

    try:
        address_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"无法解析 web_fetch URL 主机: {hostname}") from exc

    if not address_infos:
        raise ValueError(f"无法解析 web_fetch URL 主机: {hostname}")

    for address_info in address_infos:
        sockaddr = address_info[4]
        _ensure_public_ip(ipaddress.ip_address(sockaddr[0]))


def _parse_ip_address(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """解析 URL 中直接出现的 IP 地址；普通域名返回 None。"""

    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def _ensure_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """只允许公网地址，统一拒绝本机、内网、链路本地和保留地址。"""

    if not address.is_global:
        raise PermissionError(f"web_fetch 不允许访问非公网地址: {address}")
