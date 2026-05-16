"""Single-turn runtime context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from context import EnvironmentContext, ModelContext, PermissionsContext, UserContext
from context_manager import ContextManager
from tools.core.context import TurnDiffTracker
from tools.core.registry import ToolRegistry
from tools.core.runner import ToolRunner
from workspace import WorkspaceContext


@dataclass
class TurnContext:
    """All runtime state needed to execute one user turn.

    Keep this as the boundary object for one user_input. If a value is needed
    by both the model loop and tool execution during the same turn, pass it
    through TurnContext instead of adding another positional argument to
    `run_turn()`.
    """

    session_id: str
    turn_id: str
    workspace: WorkspaceContext
    session_state: Any
    registry: ToolRegistry
    runner: ToolRunner
    history: ContextManager
    environment: EnvironmentContext
    permissions: PermissionsContext
    model: ModelContext
    user: UserContext
    diff_tracker: TurnDiffTracker = field(default_factory=TurnDiffTracker)

    @classmethod
    def from_runtime(
        cls,
        *,
        session_id: str,
        turn_id: str,
        workspace: WorkspaceContext,
        session_state: Any,
        registry: ToolRegistry,
        runner: ToolRunner,
        history: ContextManager,
        system_prompt: str,
        user_input: str,
        model_config: Any,
    ) -> "TurnContext":
        return cls(
            session_id=session_id,
            turn_id=turn_id,
            workspace=workspace,
            session_state=session_state,
            registry=registry,
            runner=runner,
            history=history,
            environment=EnvironmentContext.from_workspace(workspace),
            permissions=PermissionsContext.from_workspace(workspace),
            model=ModelContext.from_request_config(model_config),
            user=UserContext(system_prompt=system_prompt, user_input=user_input),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "workspace": self.workspace.as_dict(),
            "environment": self.environment.as_dict(),
            "permissions": self.permissions.as_dict(),
            "model": self.model.as_dict(),
            "user": self.user.as_dict(),
            "diff": {"touched_paths": list(self.diff_tracker.touched_paths)},
        }
