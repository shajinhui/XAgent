"""Memory 搜索：按关键词搜索记忆内容。"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from memory.store import MemoryStore


def search_memory(memory_dir: Path, query: str, limit: int = 5) -> List[Tuple[str, str, int]]:
    """搜索 memory 内容。

    Returns:
        List of (memory_id, matched_line, line_number)
    """
    results = []
    query_lower = query.lower()

    store = MemoryStore(memory_dir)

    # 搜索所有类型的 memory
    all_memories = (
        store.list_session_memories()
        + store.list_task_memories()
    )

    for mem in all_memories:
        lines = mem.content.split('\n')
        for line_num, line in enumerate(lines, start=1):
            if query_lower in line.lower():
                results.append((mem.memory_id, line.strip(), line_num))
                if len(results) >= limit * 3:  # 预留足够候选
                    break

    # 按相关性排序（简单匹配）
    results.sort(key=lambda x: query_lower in x[1].lower(), reverse=True)

    return results[:limit]


def search_memory_index(memory_dir: Path, query: str) -> str:
    """搜索 MEMORY.md 并返回相关内容。"""
    index_path = memory_dir / "MEMORY.md"
    if not index_path.exists():
        return ""

    content = index_path.read_text(encoding="utf-8")
    lines = content.split('\n')
    query_lower = query.lower()

    matched_lines = []
    for i, line in enumerate(lines):
        if query_lower in line.lower():
            # 返回匹配行及其上下文
            start = max(0, i - 2)
            end = min(len(lines), i + 3)
            matched_lines.extend(lines[start:end])
            matched_lines.append("---")

    return "\n".join(matched_lines[:50])  # 最多返回50行
