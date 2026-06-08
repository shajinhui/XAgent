"""按 workspace 边界加载项目说明文档。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from workspace.models import WorkspaceContext


INSTRUCTION_FILE_NAME = "AGENTS.md"
MAX_INSTRUCTION_CHARS = 40_000


@dataclass(frozen=True)
class ProjectInstructionFile:
    """一个被加载进模型上下文的项目说明文件。"""

    path: Path
    content: str

    def render(self, project_root: Path) -> str:
        try:
            label = self.path.relative_to(project_root).as_posix()
        except ValueError:
            label = self.path.as_posix()
        return f"### {label}\n\n{self.content.strip()}"


@dataclass(frozen=True)
class ProjectInstructions:
    """从 project_root 到 current_dir 分层得到的说明文档集合。"""

    files: tuple[ProjectInstructionFile, ...]

    def render_fragment(self, project_root: Path) -> str:
        if not self.files:
            return ""
        rendered = [item.render(project_root) for item in self.files]
        return "\n\n".join(rendered).strip()

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(item.path for item in self.files)


def load_project_instructions(workspace: WorkspaceContext) -> ProjectInstructions:
    """只在 project_root 到 current_dir 的路径链上加载 AGENTS.md。"""

    search_dirs = _instruction_search_dirs(workspace.project_root, workspace.current_dir)
    files: list[ProjectInstructionFile] = []
    for directory in search_dirs:
        path = directory / INSTRUCTION_FILE_NAME
        if not path.exists() or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > MAX_INSTRUCTION_CHARS:
            content = content[:MAX_INSTRUCTION_CHARS] + "\n\n[内容已截断]"
        files.append(ProjectInstructionFile(path=path.resolve(), content=content))
    return ProjectInstructions(tuple(files))


def render_system_prompt_with_project_instructions(
    base_prompt: str,
    workspace: WorkspaceContext,
) -> tuple[str, ProjectInstructions]:
    """把项目说明追加到基础 system prompt，供当前 turn 使用。"""

    instructions = load_project_instructions(workspace)
    fragment = instructions.render_fragment(workspace.project_root)
    if not fragment:
        return base_prompt, instructions
    return (
        f"{base_prompt}\n\n"
        "项目说明按从 project_root 到 current_dir 的顺序加载；越靠后的文件优先级越高。\n"
        f"{fragment}",
        instructions,
    )


def _instruction_search_dirs(project_root: Path, current_dir: Path) -> tuple[Path, ...]:
    """current_dir 在项目外时只读取 project_root，避免越界加载外部说明。"""

    project_root = project_root.resolve()
    current_dir = current_dir.resolve()
    if current_dir != project_root and project_root not in current_dir.parents:
        return (project_root,)

    relative_parts = current_dir.relative_to(project_root).parts
    dirs = [project_root]
    current = project_root
    for part in relative_parts:
        current = current / part
        dirs.append(current)
    return tuple(dirs)
