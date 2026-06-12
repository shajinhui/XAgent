"""Tool invocation context shared by router and runner."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from session.turn_diff import TurnDiffTracker


@dataclass(frozen=True)
class ToolApprovalState:
    """Approval state attached to a tool invocation."""

    approved: bool = False
    feedback: str | None = None


@dataclass
class ToolCancellationState:
    """Lightweight cancellation flag for future long-running tools."""

    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


@dataclass
class ToolInvocation:
    """Internal representation of one model-requested tool call.

    This is deliberately richer than the raw model `tool_call`: it carries the
    session/turn/workspace identity needed for auditing, approvals, future
    cancellation, and per-turn diff tracking.
    """

    name: str
    arguments: str
    call_id: str
    session_id: str | None = None
    turn_id: str | None = None
    selected_root: Path | None = None
    current_dir: Path | None = None
    source: str = "model"
    approval: ToolApprovalState = field(default_factory=ToolApprovalState)
    cancellation: ToolCancellationState = field(default_factory=ToolCancellationState)
    diff_tracker: "TurnDiffTracker | None" = None

    @classmethod
    def from_model_call(
        cls,
        name: str,
        arguments: str,
        call_id: str,
        turn_context: Any | None = None,
    ) -> "ToolInvocation":
        """Attach turn-scoped runtime identity to a raw model tool call."""

        invocation = cls(name=name, arguments=arguments, call_id=call_id)
        if turn_context is None:
            return invocation

        return cls(
            name=name,
            arguments=arguments,
            call_id=call_id,
            session_id=getattr(turn_context, "session_id", None),
            turn_id=getattr(turn_context, "turn_id", None),
            selected_root=getattr(turn_context.environment, "selected_root", None),
            current_dir=getattr(turn_context.environment, "current_dir", None),
            diff_tracker=getattr(turn_context, "diff_tracker", None),
        )

    def with_approval(
        self,
        approved: bool,
        feedback: str | None = None,
    ) -> "ToolInvocation":
        """Return a copy used for the second execution after user approval."""

        return replace(
            self,
            approval=ToolApprovalState(approved=approved, feedback=feedback),
        )
