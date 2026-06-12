"""提示词模板加载器。

在模块导入时加载所有 Markdown 模板为常量，避免运行时文件 I/O。
"""
from __future__ import annotations

from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load(name: str) -> str:
    """加载模板文件内容。

    Args:
        name: 模板文件路径（相对于 templates/ 目录，不含 .md 扩展名）

    Returns:
        模板文件的文本内容
    """
    return (_TEMPLATES_DIR / f"{name}.md").read_text(encoding="utf-8")


# 核心提示词模板
CORE_PROMPT = _load("core_prompt")

# 个性化模板
PERSONALITY_DEFAULT = _load("personality/default")
PERSONALITY_FRIENDLY = _load("personality/friendly")
PERSONALITY_PRAGMATIC = _load("personality/pragmatic")

# 工具使用指引
TOOLS_SHELL = _load("tools/shell")
TOOLS_FILE = _load("tools/file_operations")
TOOLS_GIT = _load("tools/git")
TOOLS_INTERACTION = _load("tools/interaction")
