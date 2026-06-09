"""macOS Seatbelt 命令执行器。"""

from __future__ import annotations

import platform
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from security.permissions import FileSystemPolicy, NetworkPolicy


TEMP_PATHS = (
    "/tmp",
    "/private/tmp",
    "/private/var/folders",
)
DEV_NULL = Path("/dev/null")


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
        selected_root: Path,
        timeout_seconds: int = 20,
        sandbox_exec_path: str = "/usr/bin/sandbox-exec",
    ) -> None:
        self.selected_root = selected_root.resolve()
        self.timeout_seconds = timeout_seconds
        self.sandbox_exec_path = sandbox_exec_path

    @staticmethod
    def _seatbelt_string(value: str) -> str:
        """转义 Seatbelt profile 中的字符串字面量。"""

        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _profile(
        self,
        filesystem_policy: FileSystemPolicy,
        network_policy: NetworkPolicy,
    ) -> str:
        """按当前 filesystem/network policy 生成 Seatbelt profile。"""

        write_filters = self._path_filters(_writable_sandbox_paths(filesystem_policy))
        protected_read_denies = self._protected_path_denies(
            "file-read*",
            filesystem_policy.accessible_roots,
            filesystem_policy.deny_read_names,
        )
        protected_write_denies = self._protected_path_denies(
            "file-write*",
            filesystem_policy.writable_roots,
            filesystem_policy.deny_write_names,
        )
        network_allow = "\n(allow network*)" if network_policy == NetworkPolicy.ENABLED else ""

        return f"""
(version 1)
(deny default)

; Basic process execution.
(allow process*)
(allow signal (target self))

; macOS 启动二进制时会读取 dyld/cryptex/runtime 等非稳定公开路径。
; 这里放开读以保证进程可启动，写入仍由 filesystem policy 严格限制。
(allow file-read*)

; Keep writes inside filesystem policy writable roots and temporary directories.
(allow file-write*
{write_filters})
{network_allow}
{protected_read_denies}
{protected_write_denies}

; Common read-only system queries used by shells and language runtimes.
(allow sysctl-read)
(allow mach-lookup)
""".strip()

    def _path_filters(self, paths: tuple[Path, ...]) -> str:
        return "\n".join(
            f'  ({_seatbelt_path_kind(path)} "{self._seatbelt_string(path.as_posix())}")'
            for path in paths
        )

    def _protected_path_denies(
        self,
        operation: str,
        roots: tuple[Path, ...],
        names: tuple[str, ...],
    ) -> str:
        lines: list[str] = []
        for root in roots:
            for name in names:
                protected = root / name
                value = self._seatbelt_string(protected.as_posix())
                lines.append(f'(deny {operation} (literal "{value}"))')
                lines.append(f'(deny {operation} (subpath "{value}"))')
        return "\n".join(lines)

    def run(
        self,
        command: str,
        *,
        filesystem_policy: FileSystemPolicy,
        network_policy: NetworkPolicy,
        timeout_seconds: int | None = None,
        cwd: Path | None = None,
    ) -> CommandExecResult:
        """在受限 Seatbelt profile 下执行 shell 命令。"""

        if platform.system() != "Darwin":
            return CommandExecResult(False, 127, "", "macOS 原生沙箱仅支持 Darwin/macOS")

        sandbox_exec = shutil.which(self.sandbox_exec_path) or shutil.which("sandbox-exec")
        if not sandbox_exec:
            return CommandExecResult(False, 127, "", "sandbox-exec 不可用，无法启用 macOS 原生沙箱")

        if not self.selected_root.exists() or not self.selected_root.is_dir():
            return CommandExecResult(False, 127, "", f"所选工作区无效: {self.selected_root}")

        try:
            command_cwd = filesystem_policy.resolve_command_cwd(cwd)
        except (PermissionError, ValueError) as exc:
            return CommandExecResult(False, 127, "", f"命令工作目录无效: {exc}")

        shell_command = f"set -eu; cd {shlex.quote(command_cwd.as_posix())}; {command}"
        try:
            # 使用 /bin/sh -lc 保持与终端 shell 命令接近的行为，同时由 Seatbelt 限制写入。
            proc = subprocess.run(
                [
                    sandbox_exec,
                    "-p",
                    self._profile(filesystem_policy, network_policy),
                    "/bin/sh",
                    "-lc",
                    shell_command,
                ],
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


def _writable_sandbox_paths(filesystem_policy: FileSystemPolicy) -> tuple[Path, ...]:
    paths = (
        *filesystem_policy.writable_roots,
        *tuple(Path(path) for path in TEMP_PATHS),
        DEV_NULL,
    )
    return tuple(_dedupe_existing(paths))


def _dedupe_existing(paths: tuple[Path, ...]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved not in result and (resolved.exists() or resolved == DEV_NULL):
            result.append(resolved)
    return result


def _seatbelt_path_kind(path: Path) -> str:
    return "literal" if path == DEV_NULL or path.is_file() else "subpath"
