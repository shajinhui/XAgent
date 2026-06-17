"""任务分解工具的本地规则实现。

`create_task_list` 是工具调用路径的一部分，不能因为低成本模型鉴权失败而污染
本地撤销、审批等控制流。需要小模型生成 UI 任务列表时，由 server 层的
task_list_processor 统一处理；这里保持纯本地、稳定、无网络副作用。
"""

from __future__ import annotations

from typing import Dict, List


def decompose_goal(goal: str) -> List[Dict]:
    """把目标拆成稳定的本地任务骨架。"""

    clean_goal = " ".join(str(goal or "").strip().split()) or "处理用户目标"
    return [
        {"step": f"分析需求: {clean_goal}", "status": "pending"},
        {"step": "实现核心功能", "status": "pending"},
        {"step": "验证和调试", "status": "pending"},
        {"step": "汇总结果", "status": "pending"},
    ]
