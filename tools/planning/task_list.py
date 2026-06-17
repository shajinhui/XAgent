"""任务规划工具 - 轻量级任务追踪（中文注释）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict
from pydantic import BaseModel, Field
from tools.core.types import ToolMeta


# ============= TaskList 核心类 =============

class TaskList:
    """任务列表管理类。

    负责任务的存储、加载、更新和格式化显示。
    数据存储在 .codex-mini/tasks.json
    """

    def __init__(self, storage_path: str = ".codex-mini/tasks.json"):
        self.path = Path(storage_path)
        self.tasks: List[Dict] = self._load()

    def _load(self) -> List[Dict]:
        """从文件加载任务列表。"""
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, Exception):
                return []
        return []

    def _save(self):
        """保存任务列表到文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.tasks, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def update(self, tasks: List[Dict], explanation: str = "") -> str:
        """更新任务列表。

        Args:
            tasks: 任务列表
            explanation: 可选说明

        Returns:
            格式化的任务显示
        """
        self.tasks = tasks
        self._save()
        return self._format(explanation)

    def _format(self, explanation: str = "") -> str:
        """格式化显示任务列表。

        Returns:
            带进度的任务列表字符串
        """
        if not self.tasks:
            return "任务列表为空"

        lines = []

        if explanation:
            lines.append(f"说明: {explanation}\n")

        lines.append("当前任务列表:\n")

        for i, task in enumerate(self.tasks, 1):
            status = task.get("status", "pending")
            status_icon = {
                "pending": "○",
                "in_progress": "⋯",
                "completed": "✓"
            }.get(status, "○")

            step = task.get("step", "未命名任务")
            lines.append(f"{status_icon} {i}. {step}")

        # 统计进度
        completed = sum(1 for t in self.tasks if t.get("status") == "completed")
        total = len(self.tasks)
        lines.append(f"\n进度: {completed}/{total} 已完成")

        return "\n".join(lines)

    def get_current_task(self) -> Dict | None:
        """获取当前正在进行的任务。"""
        for task in self.tasks:
            if task.get("status") == "in_progress":
                return task
        return None

    def get_next_pending(self) -> Dict | None:
        """获取下一个待执行任务。"""
        for task in self.tasks:
            if task.get("status") == "pending":
                return task
        return None


# ============= update_plan 工具 =============

META = ToolMeta(
    name="update_plan",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)


class UpdatePlanArgs(BaseModel):
    """update_plan 工具参数。"""

    explanation: str = Field(
        default="",
        description="任务说明（可选）",
    )
    plan: List[Dict] = Field(
        description="任务列表，每个任务包含 step (步骤描述) 和 status (pending/in_progress/completed)",
    )


def schema() -> dict:
    """返回供模型调用的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": META.name,
            "description": "更新当前任务计划，记录每个步骤的状态",
            "parameters": UpdatePlanArgs.model_json_schema(),
        },
    }


# 全局单例
_task_list = TaskList()


def run(ctx, payload: dict) -> str:
    """更新任务计划。

    Args:
        ctx: 工具执行上下文
        payload: 模型传入的工具参数

    Returns:
        格式化的任务列表
    """
    args = UpdatePlanArgs(**payload)

    storage_path = ".codex-mini/tasks.json"
    if ctx is not None and hasattr(ctx, "project_root") and ctx.project_root:
        storage_path = str(Path(ctx.project_root) / ".codex-mini" / "tasks.json")

    task_list = TaskList(storage_path)
    return task_list.update(args.plan, args.explanation)


# ============= create_task_list 工具 =============

CREATE_META = ToolMeta(
    name="create_task_list",
    is_read_only=False,
    is_mutating=True,
    supports_parallel=False,
    requires_approval=True,
)


class CreateTaskListArgs(BaseModel):
    """create_task_list 工具参数。"""

    goal: str = Field(
        description="要完成的目标，将被自动分解为任务列表",
    )


def create_schema() -> dict:
    """返回供模型调用的 OpenAI tool schema。"""

    return {
        "type": "function",
        "function": {
            "name": CREATE_META.name,
            "description": "根据一个目标创建初始任务列表",
            "parameters": CreateTaskListArgs.model_json_schema(),
        },
    }


def create_run(ctx, payload: dict) -> str:
    """创建任务列表（自动分解目标）。

    Args:
        ctx: 工具执行上下文
        payload: 模型传入的工具参数

    Returns:
        格式化的任务列表
    """
    from tools.planning.decomposer import decompose_goal

    args = CreateTaskListArgs(**payload)

    # 使用小模型分解
    tasks = decompose_goal(args.goal)

    # 保存并返回
    storage_path = ".codex-mini/tasks.json"
    if ctx is not None and hasattr(ctx, "project_root") and ctx.project_root:
        storage_path = str(Path(ctx.project_root) / ".codex-mini" / "tasks.json")

    task_list = TaskList(storage_path)
    return task_list.update(tasks, f"目标: {args.goal}")
