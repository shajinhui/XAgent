"""按 workspace 边界加载项目说明文档。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex

from security.exec_policy import ExecPolicy
from workspace.models import WorkspaceContext


INSTRUCTION_FILE_NAME = "AGENTS.md"
MAX_INSTRUCTION_TOTAL_CHARS = 40_000
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_TEST_HEADING_KEYWORDS = ("test", "tests", "testing", "测试")
_COMMAND_HEADING_KEYWORDS = ("useful commands", "commands", "命令", "常用命令")
_SKIP_COMMAND_PREFIXES = ("#", "//")


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

    def render_fragment(self, project_root: Path, current_dir: Path) -> str:
        if not self.files:
            return ""
        rendered = [item.render(project_root) for item in self.files]
        body = "\n\n".join(rendered).strip()
        return (
            f"# AGENTS.md instructions for {current_dir.resolve().as_posix()}\n\n"
            "<INSTRUCTIONS>\n"
            "项目说明按从 project_root 到 current_dir 的顺序加载；越靠后的文件优先级越高。\n\n"
            f"{body}\n"
            "</INSTRUCTIONS>"
        )

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(item.path for item in self.files)


def load_project_instructions(workspace: WorkspaceContext) -> ProjectInstructions:
    """只在 project_root 到 current_dir 的路径链上加载 AGENTS.md。"""

    search_dirs = _instruction_search_dirs(workspace.project_root, workspace.current_dir)
    files: list[ProjectInstructionFile] = []
    remaining_chars = MAX_INSTRUCTION_TOTAL_CHARS
    for directory in search_dirs:
        if remaining_chars <= 0:
            break
        path = directory / INSTRUCTION_FILE_NAME
        if not path.exists() or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > remaining_chars:
            content = content[:remaining_chars] + "\n\n[内容已截断]"
            remaining_chars = 0
        else:
            remaining_chars -= len(content)
        files.append(ProjectInstructionFile(path=path.resolve(), content=content))
    return ProjectInstructions(tuple(files))


def render_system_prompt_with_project_instructions(
    base_prompt: str,
    workspace: WorkspaceContext,
) -> tuple[str, ProjectInstructions]:
    """把项目说明追加到基础 system prompt，供当前 turn 使用。"""

    instructions = load_project_instructions(workspace)
    fragment = instructions.render_fragment(workspace.project_root, workspace.current_dir)
    if not fragment:
        return base_prompt, instructions
    return f"{base_prompt}\n\n{fragment}", instructions


def resolve_workspace_test_defaults(
    workspace: WorkspaceContext,
) -> tuple[str | None, int, str | None]:
    """解析当前 workspace 默认测试命令，优先 trusted config，再退回 AGENTS.md。"""

    project_policy = workspace.project_policy
    default_timeout = project_policy.test_timeout if project_policy is not None else 60
    if project_policy is not None and project_policy.test_command:
        return project_policy.test_command, project_policy.test_timeout, "project_config"

    instructions = load_project_instructions(workspace)
    command = _extract_test_command_from_instructions(instructions)
    if not command:
        return None, default_timeout, None
    return command, default_timeout, "agents_md"


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


def _extract_test_command_from_instructions(instructions: ProjectInstructions) -> str | None:
    """按最近作用域优先，从 AGENTS.md 文本中提取显式声明的测试命令。"""

    for instruction in reversed(instructions.files):
        candidates = _instruction_test_candidates(instruction.content)
        if candidates:
            return candidates[0]
    return None


def _instruction_test_candidates(content: str) -> list[str]:
    current_heading = ""
    in_fence = False
    fence_delimiter = ""
    fence_lines: list[str] = []
    fence_heading = ""
    scored: list[tuple[int, int, str]] = []

    for index, raw_line in enumerate(content.splitlines()):
        stripped = raw_line.strip()
        if _is_fence_delimiter(stripped):
            if not in_fence:
                in_fence = True
                fence_delimiter = stripped[:3]
                fence_heading = current_heading
                fence_lines = []
            elif stripped.startswith(fence_delimiter):
                scored.extend(_score_block_candidates(fence_lines, fence_heading, index))
                in_fence = False
                fence_delimiter = ""
                fence_lines = []
            continue

        if in_fence:
            fence_lines.append(raw_line)
            continue

        if stripped.startswith("#"):
            current_heading = stripped.lstrip("#").strip().lower()
            continue

        scored.extend(_score_inline_candidates(raw_line, current_heading, index))

    if in_fence and fence_lines:
        scored.extend(_score_block_candidates(fence_lines, fence_heading, len(scored)))

    scored.sort(key=lambda item: (-item[0], item[1]))
    seen: set[str] = set()
    ordered: list[str] = []
    for _score, _index, command in scored:
        if command in seen:
            continue
        seen.add(command)
        ordered.append(command)
    return ordered


def _score_block_candidates(lines: list[str], heading: str, start_index: int) -> list[tuple[int, int, str]]:
    results: list[tuple[int, int, str]] = []
    heading_score = _heading_test_score(heading)
    for offset, raw_line in enumerate(lines):
        command = _normalize_candidate_command(raw_line)
        if not command or not _looks_like_test_command(command):
            continue
        results.append((heading_score + 2, start_index + offset, command))
    return results


def _score_inline_candidates(
    raw_line: str,
    heading: str,
    index: int,
) -> list[tuple[int, int, str]]:
    lowered = raw_line.lower()
    line_score = _heading_test_score(heading)
    if any(keyword in lowered for keyword in _TEST_HEADING_KEYWORDS):
        line_score += 3
    elif any(keyword in lowered for keyword in _COMMAND_HEADING_KEYWORDS):
        line_score += 1

    results: list[tuple[int, int, str]] = []
    for candidate in _INLINE_CODE_RE.findall(raw_line):
        command = _normalize_candidate_command(candidate)
        if not command or not _looks_like_test_command(command):
            continue
        results.append((line_score + 1, index, command))
    return results


def _heading_test_score(heading: str) -> int:
    lowered = heading.lower().strip()
    score = 0
    if any(keyword in lowered for keyword in _TEST_HEADING_KEYWORDS):
        score += 4
    if any(keyword in lowered for keyword in _COMMAND_HEADING_KEYWORDS):
        score += 2
    return score


def _normalize_candidate_command(raw_line: str) -> str | None:
    candidate = str(raw_line or "").strip()
    if not candidate:
        return None
    if candidate.startswith(_SKIP_COMMAND_PREFIXES):
        return None
    if candidate.startswith(("-", "*")):
        candidate = candidate[1:].strip()
    if candidate.startswith("$"):
        candidate = candidate[1:].strip()
    command = " ".join(candidate.split())
    if not command or len(command) > 1000:
        return None
    decision = ExecPolicy().decide(command, approved=True)
    if not decision.allowed:
        return None
    return command


def _looks_like_test_command(command: str) -> bool:
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv:
        return False

    head = Path(argv[0]).name.lower()
    tail = [part.lower() for part in argv[1:]]
    joined = " ".join([head, *tail])

    if head in {"pytest", "phpunit", "vitest", "jest"}:
        return True
    if head in {"python", "python3"} and tail[:2] in (["-m", "unittest"], ["-m", "pytest"]):
        return True
    if head == "npx" and tail and Path(tail[0]).name.lower() in {"vitest", "jest", "pytest"}:
        return True
    if head in {"npm", "pnpm", "yarn", "bun"} and "test" in tail[:3]:
        return True
    if head == "make" and tail[:1] == ["test"]:
        return True
    if head in {"go", "cargo", "mvn", "gradle", "deno"} and tail[:1] == ["test"]:
        return True
    if head == "gradlew" and tail[:1] == ["test"]:
        return True
    if "unittest" in joined or "pytest" in joined:
        return True
    return False


def _is_fence_delimiter(value: str) -> bool:
    return value.startswith("```") or value.startswith("~~~")
