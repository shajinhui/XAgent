from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import docker
from docker.errors import DockerException, ImageNotFound


@dataclass
class CommandExecResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str


class SecureDockerExecutor:
    """在容器里执行命令：真实项目目录以读写 workspace 形式挂载。"""

    def __init__(
        self,
        project_root: Path,
        image: str = "python:3.11-slim",
        timeout_seconds: int = 20,
    ) -> None:
        self.project_root = project_root.resolve()
        self.image = image
        self.timeout_seconds = timeout_seconds

    def _ensure_image(self, client: docker.DockerClient) -> None:
        try:
            client.images.get(self.image)
        except ImageNotFound:
            client.images.pull(self.image)

    def run(self, command: str, timeout_seconds: int | None = None) -> CommandExecResult:
        try:
            client = docker.from_env()
        except DockerException as exc:
            return CommandExecResult(False, 127, "", f"Docker 不可用: {exc}")

        if not self.project_root.exists() or not self.project_root.is_dir():
            return CommandExecResult(False, 127, "", f"项目根目录无效: {self.project_root}")

        container = None
        try:
            self._ensure_image(client)
            shell_command = f"set -eu; cd /workspace; {command}"
            container = client.containers.run(
                self.image,
                command=["sh", "-lc", shell_command],
                working_dir="/workspace",
                volumes={
                    str(self.project_root): {"bind": "/workspace", "mode": "rw"},
                },
                detach=True,
                network_disabled=True,
                mem_limit="512m",
                nano_cpus=1_000_000_000,
            )
            result = container.wait(timeout=timeout_seconds or self.timeout_seconds)
            exit_code = int(result.get("StatusCode", 1))
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            return CommandExecResult(exit_code == 0, exit_code, stdout, stderr)
        except Exception as exc:  # pragma: no cover
            return CommandExecResult(False, 1, "", f"容器执行失败: {exc}")
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
