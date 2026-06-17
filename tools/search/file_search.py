"""文件搜索工具 - 模糊匹配，按相关性排序（中文注释）。"""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import List, Dict
from functools import lru_cache
from pydantic import BaseModel, Field
from tools.core.types import ToolExecutionContext, ToolMeta


META = ToolMeta(
    name="file_search",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=True,
)


class FileSearchArgs(BaseModel):
    """file_search 工具入参。"""

    query: str = Field(
        description="搜索关键词，支持模糊匹配。示例: 'user model', 'config', 'test util'",
    )
    directory: str = Field(
        default=".",
        description="搜索目录，默认当前目录",
    )
    max_results: int = Field(
        default=20,
        description="最多返回结果数，默认 20",
    )


def schema() -> dict:
    """返回供模型调用的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": META.name,
            "description": "按文件名或路径模糊搜索 workspace 内的文件",
            "parameters": FileSearchArgs.model_json_schema(),
        },
    }


# 默认排除的目录和文件模式
EXCLUDE_DIRS = {
    ".git", ".svn", ".hg",  # 版本控制
    ".venv", "venv", "env", "virtualenv",  # Python 虚拟环境
    "node_modules", "vendor",  # 依赖
    "__pycache__", ".pytest_cache", ".mypy_cache",  # Python 缓存
    "dist", "build", ".next", ".nuxt",  # 构建产物
    ".idea", ".vscode",  # IDE 配置
    "coverage", ".coverage",  # 测试覆盖率
}

EXCLUDE_PATTERNS = [
    "*.pyc", "*.pyo", "*.pyd",  # Python 编译文件
    ".DS_Store", "Thumbs.db",  # 系统文件
    "*.log", "*.tmp",  # 临时文件
    "*.swp", "*.swo",  # Vim 临时文件
]


def _should_exclude(path: Path) -> bool:
    """判断路径是否应该被排除。"""
    # 检查目录
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return True

    # 检查文件模式
    for pattern in EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(path.name, pattern):
            return True

    return False


@lru_cache(maxsize=1)
def _get_all_files(directory: str) -> List[Path]:
    """获取所有文件（缓存）。"""
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    files = []
    try:
        for item in path.rglob("*"):
            if item.is_file() and not _should_exclude(item):
                files.append(item)
    except PermissionError:
        pass

    return files


def _fuzzy_match_score(query: str, text: str) -> int:
    """
    模糊匹配评分（子序列匹配）。

    返回 0-100 的分数，越高越相关。
    """
    query = query.lower()
    text = text.lower()

    # 完全匹配
    if query == text:
        return 100

    # 完整子串匹配
    if query in text:
        # 在文本开头匹配，分数更高
        if text.startswith(query):
            return 95
        # 越短的文本中匹配，分数越高
        ratio = len(query) / len(text)
        return int(85 + 10 * ratio)

    # 子序列匹配 (例如 "usmd" 匹配 "user_model")
    query_idx = 0
    text_idx = 0
    matched_positions = []

    while query_idx < len(query) and text_idx < len(text):
        if query[query_idx] == text[text_idx]:
            matched_positions.append(text_idx)
            query_idx += 1
        text_idx += 1

    # 未完全匹配
    if query_idx < len(query):
        return 0

    # 计算间隙（匹配字符之间的距离）
    if len(matched_positions) < 2:
        return 70

    gaps = [matched_positions[i+1] - matched_positions[i]
            for i in range(len(matched_positions) - 1)]
    avg_gap = sum(gaps) / len(gaps)

    # 间隙越小，分数越高
    base_score = 70
    gap_penalty = min(avg_gap * 2, 30)

    # 连续匹配加分
    consecutive_count = sum(1 for g in gaps if g == 1)
    consecutive_bonus = consecutive_count * 3

    return int(base_score - gap_penalty + consecutive_bonus)


def _calculate_final_score(query: str, file_path: Path, base_dir: Path) -> int:
    """
    计算最终评分。

    考虑因素:
    - 文件名匹配度
    - 路径深度
    - 文件名长度
    """
    # 1. 文件名匹配分数
    name_score = _fuzzy_match_score(query, file_path.name)

    if name_score == 0:
        # 尝试匹配完整路径
        rel_path = str(file_path.relative_to(base_dir))
        name_score = _fuzzy_match_score(query, rel_path)

    if name_score == 0:
        return 0

    # 2. 路径深度惩罚（越深越不重要）
    try:
        depth = len(file_path.relative_to(base_dir).parts) - 1
    except ValueError:
        depth = 0
    depth_penalty = min(depth * 5, 20)

    # 3. 文件名长度奖励（短文件名更可能是重要文件）
    name_length = len(file_path.name)
    if name_length < 15:
        length_bonus = 5
    elif name_length > 30:
        length_bonus = -5
    else:
        length_bonus = 0

    # 4. 特殊文件类型加分
    important_suffixes = {".py", ".js", ".ts", ".jsx", ".tsx", ".md"}
    type_bonus = 5 if file_path.suffix in important_suffixes else 0

    final = name_score - depth_penalty + length_bonus + type_bonus
    return max(0, min(100, final))


def run(ctx: ToolExecutionContext, payload: dict) -> str:
    """
    模糊搜索文件。

    Args:
        ctx: 工具执行上下文，负责 workspace 边界校验
        payload: 模型传入的工具参数

    Returns:
        按相关性排序的文件列表
    """
    args = FileSearchArgs(**payload)
    try:
        base_dir = ctx.policy.resolve_read_path(args.directory)
        if not base_dir.exists():
            return f"错误: 目录不存在: {args.directory}"

        if not base_dir.is_dir():
            return f"错误: 不是目录: {args.directory}"

        # 获取所有文件
        all_files = _get_all_files(str(base_dir))

        if not all_files:
            return "未找到任何文件"

        # 计算每个文件的评分
        scored_files: List[Dict] = []
        for file_path in all_files:
            score = _calculate_final_score(args.query, file_path, base_dir)

            if score > 30:  # 最低相关性阈值
                try:
                    rel_path = str(file_path.relative_to(base_dir))
                except ValueError:
                    rel_path = str(file_path)

                scored_files.append({
                    "path": rel_path,
                    "score": score,
                })

        if not scored_files:
            return f"未找到匹配 '{args.query}' 的文件"

        # 按分数排序
        scored_files.sort(key=lambda x: x["score"], reverse=True)

        # 格式化输出
        results = scored_files[: args.max_results]
        lines = [f"找到 {len(scored_files)} 个匹配文件，显示前 {len(results)} 个:\n"]

        for item in results:
            lines.append(f"  {item['path']} (相关性: {item['score']})")

        if len(scored_files) > args.max_results:
            lines.append(f"\n... 还有 {len(scored_files) - args.max_results} 个结果")

        return "\n".join(lines)

    except Exception as e:
        return f"错误: {str(e)}"
