"""简单的会话级安全熔断器。"""

from __future__ import annotations

from collections import defaultdict


class CircuitBreaker:
    """同一风险类别连续拒绝超过阈值则挂起会话。"""

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        self._counts: dict[tuple[str, str], int] = defaultdict(int)

    def record_rejection(self, session_id: str, category: str) -> bool:
        """记录一次拒绝，返回是否达到挂起阈值。"""

        key = (session_id, category)
        self._counts[key] += 1
        return self._counts[key] >= self.threshold

    def record_success(self, session_id: str, category: str) -> None:
        """某类风险成功执行后清零计数。"""

        key = (session_id, category)
        self._counts[key] = 0

    def count(self, session_id: str, category: str) -> int:
        """读取某 session 在某风险类别下的连续拒绝次数。"""

        return self._counts[(session_id, category)]

    def reset(self, session_id: str, category: str | None = None) -> None:
        """重置单个风险类别或整个 session 的拒绝计数。"""

        if category is not None:
            self._counts[(session_id, category)] = 0
            return

        for key in list(self._counts):
            if key[0] == session_id:
                self._counts[key] = 0
