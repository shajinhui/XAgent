"""User-provided turn context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class UserContext:
    """The user input and system prompt active for a turn."""

    system_prompt: str
    user_input: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "system_prompt": self.system_prompt,
            "user_input": self.user_input,
        }

    def render_fragment(self) -> str:
        return self.user_input
