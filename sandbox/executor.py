from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import docker
from docker.errors import DockerException


DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r":\(\)\{:\|:&\};:",
    r"mkfs\.",
    r"dd\s+if=",
    r"shutdown\b",
    r"reboot\b",
]


@dataclass
class SandboxResult:
    """沙箱执行结果。"""

    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    blocked: bool = False
    reason: str = ""


class DockerSandbox:
    """基于 Docker 的最小沙箱执行器。"""

    def __init__(
        self,
        project_root: str,
        image: str = "python:3.11-slim",
        timeout_seconds: int = 20,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.image = image
        self.timeout_seconds = timeout_seconds

    def _validate_command(self, command: str) -> Optional[str]:
        """执行前进行危险命令模式拦截。"""
        normalized = " ".join(command.strip().split()).lower()
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, normalized):
                return f"已拦截危险命令模式: {pattern}"
        return None

    def run(self, command: str) -> SandboxResult:
        """在隔离容器中执行命令并返回结果。"""
        reason = self._validate_command(command)
        if reason:
            return SandboxResult(
                ok=False,
                exit_code=126,
                stdout="",
                stderr=reason,
                blocked=True,
                reason=reason,
            )

        try:
            client = docker.from_env()
        except DockerException as exc:
            return SandboxResult(
                ok=False,
                exit_code=127,
                stdout="",
                stderr=f"Docker 不可用: {exc}",
            )

        if not self.project_root.exists() or not self.project_root.is_dir():
            return SandboxResult(
                ok=False,
                exit_code=127,
                stdout="",
                stderr=f"项目根目录无效: {self.project_root}",
            )

        try:
            client.images.pull(self.image)
            shell_command = f"set -eu; cd /workspace; {command}"
            container = client.containers.run(
                self.image,
                command=["sh", "-lc", shell_command],
                working_dir="/workspace",
                volumes={
                    str(self.project_root): {
                        "bind": "/workspace",
                        "mode": "rw",
                    }
                },
                detach=True,
                network_disabled=True,
                mem_limit="512m",
                nano_cpus=1_000_000_000,
            )

            result = container.wait(timeout=self.timeout_seconds)
            exit_code = int(result.get("StatusCode", 1))
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            container.remove(force=True)

            return SandboxResult(
                ok=exit_code == 0,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )
        except Exception as exc:  # pragma: no cover - 防御性兜底分支
            return SandboxResult(
                ok=False,
                exit_code=1,
                stdout="",
                stderr=f"沙箱执行失败: {exc}",
            )
