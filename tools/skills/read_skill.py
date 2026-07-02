"""读取已加载 skill 的完整 SKILL.md。"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from skills.resources import SkillResourceError, SkillResourceResolver
from tools.core.types import ToolExecutionContext, ToolMeta, ToolResult


META = ToolMeta(
    name="read_skill",
    is_read_only=True,
    is_mutating=False,
    supports_parallel=True,
)


class ReadSkillArgs(BaseModel):
    """read_skill 工具入参。"""

    name: str | None = Field(None, description="要读取的 skill 名称；名称唯一时可用")
    path: str | None = Field(None, description="要读取的 SKILL.md 绝对路径")

    @model_validator(mode="after")
    def validate_selector(self) -> "ReadSkillArgs":
        if not (self.name or self.path):
            raise ValueError("name or path is required")
        return self


def schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "read_skill",
            "description": "读取当前 catalog 中某个 skill 的完整 SKILL.md 内容",
            "parameters": ReadSkillArgs.model_json_schema(),
        },
    }


def run(ctx: ToolExecutionContext, payload: dict) -> ToolResult:
    args = ReadSkillArgs(**payload)
    resolver = _resolver(ctx)
    try:
        skill, contents = resolver.read_skill(name=args.name, path=args.path)
    except SkillResourceError as exc:
        return ToolResult(
            ok=False,
            content=str(exc),
            metadata={"error_type": "skill_read_error"},
        )
    return ToolResult(
        ok=True,
        content=(
            "<skill>\n"
            f"<name>{skill.name}</name>\n"
            f"<path>{skill.path.as_posix()}</path>\n"
            f"{contents}\n"
            "</skill>"
        ),
        metadata={
            "skill_used": True,
            "skill_name": skill.name,
            "skill_path": skill.path.as_posix(),
            "skill_scope": skill.scope.value,
            "invocation_type": "implicit",
        },
    )


def _resolver(ctx: ToolExecutionContext) -> SkillResourceResolver:
    resolver = ctx.skill_resource_resolver
    if not isinstance(resolver, SkillResourceResolver):
        raise RuntimeError("skill resolver is not available")
    return resolver
