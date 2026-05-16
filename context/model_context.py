"""Model request context for a single turn."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class ModelContext:
    """Snapshot of the model settings used by this turn."""

    model: str
    reasoning_effort: str
    request_config: Any

    @classmethod
    def from_request_config(cls, request_config: Any) -> "ModelContext":
        return cls(
            model=str(getattr(request_config, "model")),
            reasoning_effort=str(getattr(request_config, "reasoning_effort", "off")),
            request_config=request_config,
        )

    def as_dict(self) -> Dict[str, Any]:
        if hasattr(self.request_config, "as_dict"):
            return self.request_config.as_dict()
        return {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
        }

    def render_fragment(self) -> str:
        return f"Model: {self.model}\nReasoning effort: {self.reasoning_effort}"
