from __future__ import annotations

from collections import defaultdict


class CircuitBreaker:
    """同一风险类别连续拒绝超过阈值则挂起会话。"""

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        self._counts: dict[tuple[str, str], int] = defaultdict(int)

    def record_rejection(self, session_id: str, category: str) -> bool:
        key = (session_id, category)
        self._counts[key] += 1
        return self._counts[key] >= self.threshold

    def record_success(self, session_id: str, category: str) -> None:
        key = (session_id, category)
        self._counts[key] = 0

    def count(self, session_id: str, category: str) -> int:
        return self._counts[(session_id, category)]

    def reset(self, session_id: str, category: str | None = None) -> None:
        if category is not None:
            self._counts[(session_id, category)] = 0
            return

        for key in list(self._counts):
            if key[0] == session_id:
                self._counts[key] = 0
