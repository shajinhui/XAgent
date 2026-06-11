"""Memory 索引生成器：生成 MEMORY.md 索引文件。"""

from __future__ import annotations

from pathlib import Path
from typing import List

from memory.models import MemoryEntry, MemoryType
from memory.store import MemoryStore


def generate_memory_index(memory_dir: Path) -> str:
    """生成 MEMORY.md 索引内容。"""
    store = MemoryStore(memory_dir)

    sections = []

    # User Memory
    user_mem = store.load_user_memory()
    if user_mem:
        sections.append("## 用户偏好\n\n" + user_mem.content)

    # Project Memory
    projects = store.list_session_memories()  # 复用 list 逻辑，后续可优化
    project_mems = [m for m in projects if m.memory_type == MemoryType.PROJECT]
    if project_mems:
        lines = ["## 项目规则\n"]
        for mem in project_mems[:5]:
            lines.append(f"### {mem.memory_id}\n\n{mem.content[:200]}...\n")
        sections.append("\n".join(lines))

    # Recent Sessions
    sessions = store.list_session_memories()[:10]
    if sessions:
        lines = ["## 最近会话\n"]
        for sess in sessions:
            preview = sess.content.split('\n')[0][:100]
            lines.append(f"- [{sess.memory_id[:8]}](sessions/{sess.memory_id}.json) - {preview}")
        sections.append("\n".join(lines))

    # Tasks
    tasks = store.list_task_memories()[:5]
    if tasks:
        lines = ["## 当前任务\n"]
        for task in tasks:
            preview = task.content.split('\n')[0][:100]
            lines.append(f"- [{task.memory_id[:8]}](tasks/{task.memory_id}.json) - {preview}")
        sections.append("\n".join(lines))

    return "# Memory Index\n\n" + "\n\n".join(sections)


def generate_memory_summary(memory_dir: Path) -> str:
    """生成简短的 memory_summary.md。"""
    store = MemoryStore(memory_dir)

    parts = []

    # User preferences (最重要)
    user_mem = store.load_user_memory()
    if user_mem:
        parts.append(user_mem.content[:300])

    # Recent context
    sessions = store.list_session_memories()[:2]
    if sessions:
        parts.append(f"\n最近讨论：{sessions[0].content[:150]}")

    return "\n\n".join(parts) if parts else "暂无记忆"


def update_memory_index(memory_dir: Path) -> None:
    """更新 MEMORY.md 和 memory_summary.md 文件。"""
    memory_dir.mkdir(parents=True, exist_ok=True)

    # 生成索引
    index_content = generate_memory_index(memory_dir)
    (memory_dir / "MEMORY.md").write_text(index_content, encoding="utf-8")

    # 生成摘要
    summary_content = generate_memory_summary(memory_dir)
    (memory_dir / "memory_summary.md").write_text(summary_content, encoding="utf-8")
