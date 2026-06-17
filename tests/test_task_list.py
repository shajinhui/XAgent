"""测试 TaskList 和 update_plan 工具。"""
import unittest
import json
from pathlib import Path
from tools.planning.task_list import TaskList, run


class TestTaskList(unittest.TestCase):
    """测试 TaskList 类。"""

    def setUp(self):
        """每个测试前执行。"""
        self.test_path = Path(".codex-mini/test_tasks.json")
        self.task_list = TaskList(str(self.test_path))

    def tearDown(self):
        """每个测试后清理。"""
        if self.test_path.exists():
            self.test_path.unlink()

    def test_create_empty(self):
        """测试创建空列表。"""
        result = self.task_list.update([])
        self.assertIn("任务列表为空", result)

    def test_create_tasks(self):
        """测试创建任务。"""
        tasks = [
            {"step": "任务1", "status": "pending"},
            {"step": "任务2", "status": "pending"},
        ]
        result = self.task_list.update(tasks)
        self.assertIn("任务1", result)
        self.assertIn("任务2", result)
        self.assertIn("0/2", result)  # 都是 pending，0 个完成

    def test_update_status(self):
        """测试更新状态。"""
        tasks = [
            {"step": "任务1", "status": "completed"},
            {"step": "任务2", "status": "in_progress"},
            {"step": "任务3", "status": "pending"},
        ]
        result = self.task_list.update(tasks)
        self.assertIn("✓", result)  # completed
        self.assertIn("⋯", result)  # in_progress
        self.assertIn("○", result)  # pending

    def test_progress_count(self):
        """测试进度统计。"""
        tasks = [
            {"step": "任务1", "status": "completed"},
            {"step": "任务2", "status": "completed"},
            {"step": "任务3", "status": "pending"},
        ]
        result = self.task_list.update(tasks)
        self.assertIn("2/3", result)

    def test_persistence(self):
        """测试持久化。"""
        tasks = [{"step": "任务1", "status": "pending"}]
        self.task_list.update(tasks)

        # 创建新实例，应该能加载数据
        new_list = TaskList(str(self.test_path))
        self.assertEqual(len(new_list.tasks), 1)
        self.assertEqual(new_list.tasks[0]["step"], "任务1")

    def test_get_current_task(self):
        """测试获取当前任务。"""
        tasks = [
            {"step": "任务1", "status": "completed"},
            {"step": "任务2", "status": "in_progress"},
            {"step": "任务3", "status": "pending"},
        ]
        self.task_list.update(tasks)

        current = self.task_list.get_current_task()
        self.assertIsNotNone(current)
        self.assertEqual(current["step"], "任务2")

    def test_get_next_pending(self):
        """测试获取下一个待执行任务。"""
        tasks = [
            {"step": "任务1", "status": "completed"},
            {"step": "任务2", "status": "completed"},
            {"step": "任务3", "status": "pending"},
            {"step": "任务4", "status": "pending"},
        ]
        self.task_list.update(tasks)

        next_task = self.task_list.get_next_pending()
        self.assertIsNotNone(next_task)
        self.assertEqual(next_task["step"], "任务3")

    def test_with_explanation(self):
        """测试带说明的更新。"""
        tasks = [{"step": "任务1", "status": "pending"}]
        result = self.task_list.update(tasks, "这是测试说明")
        self.assertIn("这是测试说明", result)


class TestUpdatePlanTool(unittest.TestCase):
    """测试 update_plan 工具函数。"""

    def setUp(self):
        self.test_path = Path(".codex-mini/tasks.json")

    def tearDown(self):
        if self.test_path.exists():
            self.test_path.unlink()

    def test_run_function(self):
        """测试 run 函数。"""
        tasks = [
            {"step": "步骤1", "status": "pending"},
            {"step": "步骤2", "status": "pending"},
        ]
        result = run(None, {"plan": tasks, "explanation": "测试计划"})
        self.assertIn("步骤1", result)
        self.assertIn("步骤2", result)
        self.assertIn("测试计划", result)

    def test_run_with_none(self):
        """测试空任务列表。"""
        result = run(None, {"plan": []})
        self.assertIn("任务列表为空", result)


if __name__ == "__main__":
    unittest.main()
