"""Single-turn runtime context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from context import EnvironmentContext, ModelContext, PermissionsContext, UserContext
from context_manager import ContextManager
from session.turn_diff import TurnDiffTracker
from skills import TurnSkills
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
    turn_skills: TurnSkills = field(default_factory=TurnSkills)
    current_user_message_index: int | None = None
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
        turn_skills: TurnSkills | None = None,
        current_user_message_index: int | None = None,
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
            turn_skills=turn_skills or TurnSkills(),
            current_user_message_index=current_user_message_index,
        )

    def model_messages(self) -> list[dict[str, Any]]:
        """返回本轮发给模型的消息，临时 skill 注入不写入长期 history。"""

        messages = self.history.messages
        skill_messages = self.turn_skills.injection_messages()
        if not skill_messages:
            return messages

        insert_at = self.current_user_message_index
        if insert_at is None or insert_at < 0 or insert_at > len(messages):
            insert_at = len(messages)
        return [*messages[:insert_at], *skill_messages, *messages[insert_at:]]

    def as_dict(self) -> dict[str, Any]:
        changed_files = self.diff_tracker.get_changed_files()
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "workspace": self.workspace.as_dict(),
            "environment": self.environment.as_dict(),
            "permissions": self.permissions.as_dict(),
            "model": self.model.as_dict(),
            "user": self.user.as_dict(),
            "skills": {
                "selected": [
                    {
                        "name": injection.name,
                        "path": injection.path.as_posix(),
                        "invocation_type": injection.invocation_type,
                    }
                    for injection in self.turn_skills.injections
                ],
                "warnings": list(self.turn_skills.warnings),
            },
            "changed_files": list(changed_files.keys()),
        }
