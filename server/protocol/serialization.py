"""协议层使用的对象转 dict 兼容工具。"""

from __future__ import annotations

from typing import Any, Dict


def object_to_dict(value: Any) -> Dict[str, Any]:
    """把 dict、Pydantic 模型或 SDK 对象转换成普通 dict。"""

    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(exclude_none=True)
    return {
        key: getattr(value, key)
        for key in dir(value)
        if not key.startswith("_") and not callable(getattr(value, key))
    }
