"""macOS Seatbelt 命令执行器。"""

from __future__ import annotations

import platform
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandExecResult:
    """命令执行后的标准化结果。"""

    ok: bool
    exit_code: int
    stdout: str
    stderr: str


class SecureMacOSSandboxExecutor:
    """在 macOS 上使用 Seatbelt（sandbox-exec）在受限环境中运行命令。

    该执行器会生成一个 Seatbelt profile，限制写权限到工作区及临时目录，
    并通过 `sandbox-exec` 启动子进程以执行指定命令，返回统一的 `CommandExecResult`。
    """

    def __init__(
        self,
        project_root: Path,
        timeout_seconds: int = 20,
        sandbox_exec_path: str = "/usr/bin/sandbox-exec",
    ) -> None:
        self.project_root = project_root.resolve()
        self.timeout_seconds = timeout_seconds
        self.sandbox_exec_path = sandbox_exec_path

    @staticmethod
    def _seatbelt_string(value: str) -> str:
        """转义 Seatbelt profile 中的字符串字面量。"""

        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _profile(self) -> str:
        """生成限制写入范围的 Seatbelt profile。"""

        workspace = self._seatbelt_string(self.project_root.as_posix())
        return f"""
(version 1)
(deny default)

; Basic process execution.
(allow process*)
(allow signal (target self))

; Commands need to read tools, libraries, interpreters, and the workspace.
(allow file-read*)

; Keep writes inside the workspace and temporary directories.
(allow file-write*
  (subpath "{workspace}")
  (subpath "/tmp")
  (subpath "/private/tmp")
  (subpath "/private/var/folders")
  (literal "/dev/null"))

; Common read-only system queries used by shells and language runtimes.
(allow sysctl-read)
(allow mach-lookup)
""".strip()

    def run(
        self,
        command: str,
        timeout_seconds: int | None = None,
        cwd: Path | None = None,
    ) -> CommandExecResult:
        """在受限 Seatbelt profile 下执行 shell 命令。"""

        if platform.system() != "Darwin":
            return CommandExecResult(False, 127, "", "macOS 原生沙箱仅支持 Darwin/macOS")

        sandbox_exec = shutil.which(self.sandbox_exec_path) or shutil.which("sandbox-exec")
        if not sandbox_exec:
            return CommandExecResult(False, 127, "", "sandbox-exec 不可用，无法启用 macOS 原生沙箱")

        if not self.project_root.exists() or not self.project_root.is_dir():
            return CommandExecResult(False, 127, "", f"项目根目录无效: {self.project_root}")

        command_cwd = (cwd or self.project_root).resolve()
        if self.project_root not in command_cwd.parents and command_cwd != self.project_root:
            return CommandExecResult(False, 127, "", f"命令工作目录越界: {command_cwd}")
        if not command_cwd.exists() or not command_cwd.is_dir():
            return CommandExecResult(False, 127, "", f"命令工作目录无效: {command_cwd}")

        shell_command = f"set -eu; cd {shlex.quote(command_cwd.as_posix())}; {command}"
        try:
            # 使用 /bin/sh -lc 保持与终端 shell 命令接近的行为，同时由 Seatbelt 限制写入。
            proc = subprocess.run(
                [sandbox_exec, "-p", self._profile(), "/bin/sh", "-lc", shell_command],
                cwd=command_cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds or self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandExecResult(
                False,
                124,
                exc.stdout or "",
                (exc.stderr or "") + "\n命令执行超时",
            )
        except OSError as exc:
            return CommandExecResult(False, 127, "", f"macOS 沙箱执行失败: {exc}")

        stderr = proc.stderr
        if proc.returncode == 71 and "sandbox_apply: Operation not permitted" in stderr:
            stderr += (
                "\n当前进程可能已经处于受限沙箱中，无法再次应用 macOS Seatbelt profile。"
                "请在正常终端或桌面应用运行环境中验证。"
            )

        return CommandExecResult(
            proc.returncode == 0,
            proc.returncode,
            proc.stdout,
            stderr,
        )
