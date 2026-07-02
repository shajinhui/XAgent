"""读取已加载 skill 目录内的相对资源。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from skills.resources import SkillResourceError, SkillResourceResolver
from tools.core.types import ToolExecutionContext, ToolMeta, ToolResult


META = ToolMeta(
    name="read_skill_resource",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=True,
)


class ReadSkillResourceArgs(BaseModel):
    """read_skill_resource 工具入参。"""

    skill_path: str = Field(..., description="已加载 skill 的 SKILL.md 绝对路径")
    resource: str = Field(..., description="相对 skill 目录的资源路径")


def schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "read_skill_resource",
            "description": "读取当前已加载 skill 目录内的相对资源",
            "parameters": ReadSkillResourceArgs.model_json_schema(),
        },
    }


def run(ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    args = ReadSkillResourceArgs(**payload)
    resolver = _resolver(ctx)
    try:
        skill, path, contents = resolver.read_resource(
            skill_path=args.skill_path,
            resource=args.resource,
        )
    except SkillResourceError as exc:
        return ToolResult(
            ok=False,
            content=str(exc),
            metadata={"error_type": "skill_resource_error"},
        )
    return ToolResult(
        ok=True,
        content=contents,
        metadata={
            "skill_resource": True,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "resource_path": path.as_posix(),
        },
    )


def _resolver(ctx: ToolExecutionContext) -> SkillResourceResolver:
    resolver = ctx.skill_resource_resolver
    if not isinstance(resolver, SkillResourceResolver):
        raise RuntimeError("skill resolver is not available")
    return resolver
