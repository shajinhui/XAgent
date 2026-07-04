"""受管理后台进程的 workspace 级生命周期与端口归属检查。"""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sandbox.macos_executor import SecureMacOSSandboxExecutor
from security.permissions import FileSystemPolicy, NetworkPolicy


@dataclass
class ManagedProcessRecord:
    """仅记录当前 Runtime 实际创建并持有 Popen handle 的进程。"""

    process_id: str
    process: subprocess.Popen[bytes]
    command: str
    cwd: Path
    log_path: Path
    started_at: float
    expected_port: int | None = None
    stop_requested: bool = False
    forced_stop: bool = False

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def pgid(self) -> int:
        return self.process.pid

    def as_dict(self) -> dict[str, Any]:
        exit_code = self.process.poll()
        if exit_code is None:
            status = "running"
        elif self.stop_requested:
            status = "stopped"
        else:
            status = "exited"
        return {
            "process_id": self.process_id,
            "pid": self.pid,
            "pgid": self.pgid,
            "command": self.command,
            "cwd": self.cwd.as_posix(),
            "log_path": self.log_path.as_posix(),
            "started_at": self.started_at,
            "expected_port": self.expected_port,
            "status": status,
            "exit_code": exit_code,
            "forced_stop": self.forced_stop,
        }


class ManagedProcessError(RuntimeError):
    """进程管理操作无法安全完成。"""

    def __init__(self, message: str, *, metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.metadata = metadata or {}


class ManagedProcessManager:
    """只管理本 Runtime 启动的进程，不接受任意 PID。"""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self._records: dict[str, ManagedProcessRecord] = {}
        self._lock = threading.RLock()
        digest = hashlib.sha256(self.project_root.as_posix().encode("utf-8")).hexdigest()[:12]
        self.log_dir = Path(tempfile.gettempdir()) / "codex-mini-processes" / digest
        self.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def start(
        self,
        command: str,
        *,
        cwd: Path,
        executor: SecureMacOSSandboxExecutor,
        filesystem_policy: FileSystemPolicy,
        network_policy: NetworkPolicy,
        sandbox_enabled: bool,
        expected_port: int | None = None,
        startup_timeout: int = 5,
    ) -> dict[str, Any]:
        """启动进程；指定端口时先查占用，再确认 listener 属于新进程组。"""

        if expected_port is not None:
            occupied = self.inspect_port(expected_port)
            if occupied:
                raise ManagedProcessError(
                    f"端口 {expected_port} 已被占用，未启动新进程",
                    metadata={"error_type": "port_in_use", "port": expected_port, "listeners": occupied},
                )

        process_id = str(uuid.uuid4())
        log_path = self.log_dir / f"{process_id}.log"
        with log_path.open("ab", buffering=0) as output:
            os.chmod(log_path, 0o600)
            started = executor.start_managed(
                command,
                filesystem_policy=filesystem_policy,
                network_policy=network_policy,
                cwd=cwd,
                output=output,
                sandbox_enabled=sandbox_enabled,
            )
        if not started.ok or started.process is None:
            log_path.unlink(missing_ok=True)
            raise ManagedProcessError(
                started.stderr or "受管理进程启动失败",
                metadata={"error_type": "process_start_failed"},
            )

        record = ManagedProcessRecord(
            process_id=process_id,
            process=started.process,
            command=command,
            cwd=cwd.resolve(),
            log_path=log_path,
            started_at=time.time(),
            expected_port=expected_port,
        )
        with self._lock:
            self._records[process_id] = record

        if expected_port is not None:
            try:
                listeners = self._wait_for_owned_port(record, expected_port, startup_timeout)
            except ManagedProcessError:
                self.stop(process_id, timeout=2)
                raise
        else:
            listeners = []

        payload = record.as_dict()
        payload["listeners"] = listeners
        return payload

    def list(self) -> list[dict[str, Any]]:
        """返回当前 Runtime 已登记进程，不扫描或接管外部 PID。"""

        with self._lock:
            return [record.as_dict() for record in self._records.values()]

    def get(self, process_id: str) -> ManagedProcessRecord:
        """按不可伪造的 process_id 获取本 Runtime 持有的进程。"""

        with self._lock:
            record = self._records.get(process_id)
        if record is None:
            raise ManagedProcessError(
                f"未知受管理进程: {process_id}",
                metadata={"error_type": "unknown_managed_process", "process_id": process_id},
            )
        return record

    def stop(self, process_id: str, *, timeout: int = 5) -> dict[str, Any]:
        """先 SIGTERM 整个自有进程组，超时后才 SIGKILL。"""

        record = self.get(process_id)
        if record.process.poll() is not None:
            return record.as_dict()

        record.stop_requested = True
        try:
            current_pgid = os.getpgid(record.pid)
        except ProcessLookupError:
            record.process.poll()
            return record.as_dict()
        if current_pgid != record.pgid:
            raise ManagedProcessError(
                "进程组身份已变化，拒绝停止以避免误杀其他进程",
                metadata={
                    "error_type": "process_identity_mismatch",
                    "process_id": process_id,
                    "pid": record.pid,
                },
            )

        os.killpg(record.pgid, signal.SIGTERM)
        try:
            record.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            record.forced_stop = True
            os.killpg(record.pgid, signal.SIGKILL)
            record.process.wait(timeout=2)
        return record.as_dict()

    def read_log(self, process_id: str, *, tail_lines: int = 80) -> str:
        """读取自有进程日志末尾，避免把临时日志目录扩展为 workspace root。"""

        record = self.get(process_id)
        if not record.log_path.exists():
            return ""
        lines = record.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-tail_lines:])

    def inspect_port(self, port: int) -> list[dict[str, Any]]:
        """只读检查 TCP listener，并标注是否属于当前 Runtime 的进程组。"""

        lsof = shutil.which("lsof")
        if not lsof:
            raise ManagedProcessError(
                "lsof 不可用，无法验证端口归属",
                metadata={"error_type": "port_inspection_unavailable", "port": port},
            )
        completed = subprocess.run(
            [lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpcn"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if completed.returncode not in {0, 1}:
            raise ManagedProcessError(
                f"端口检查失败: {completed.stderr.strip() or completed.returncode}",
                metadata={"error_type": "port_inspection_failed", "port": port},
            )

        listeners: list[dict[str, Any]] = []
        current_pid: int | None = None
        current_command = ""
        for raw_line in completed.stdout.splitlines():
            if not raw_line:
                continue
            tag, value = raw_line[0], raw_line[1:]
            if tag == "p":
                try:
                    current_pid = int(value)
                except ValueError:
                    current_pid = None
                current_command = ""
            elif tag == "c":
                current_command = value
            elif tag == "n" and current_pid is not None:
                listeners.append(
                    {
                        "pid": current_pid,
                        "command": current_command,
                        "address": value,
                        "managed_process_id": self._managed_process_id_for_pid(current_pid),
                    }
                )
        return listeners

    def _wait_for_owned_port(
        self,
        record: ManagedProcessRecord,
        port: int,
        timeout: int,
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            exit_code = record.process.poll()
            if exit_code is not None:
                raise ManagedProcessError(
                    f"进程在监听端口 {port} 前退出，exit_code={exit_code}",
                    metadata={
                        "error_type": "process_exited_before_ready",
                        "process_id": record.process_id,
                        "exit_code": exit_code,
                        "log_tail": self.read_log(record.process_id),
                    },
                )
            listeners = self.inspect_port(port)
            owned = [item for item in listeners if item["managed_process_id"] == record.process_id]
            foreign = [item for item in listeners if item["managed_process_id"] != record.process_id]
            if foreign:
                raise ManagedProcessError(
                    f"端口 {port} 出现非当前进程监听，启动验证失败",
                    metadata={"error_type": "port_identity_conflict", "listeners": listeners},
                )
            if owned:
                return listeners
            time.sleep(0.1)
        raise ManagedProcessError(
            f"进程未在 {timeout} 秒内监听端口 {port}",
            metadata={
                "error_type": "process_start_timeout",
                "process_id": record.process_id,
                "port": port,
                "log_tail": self.read_log(record.process_id),
            },
        )

    def _managed_process_id_for_pid(self, pid: int) -> str | None:
        try:
            pgid = os.getpgid(pid)
        except (PermissionError, ProcessLookupError):
            return None
        with self._lock:
            for record in self._records.values():
                if record.process.poll() is None and record.pgid == pgid:
                    return record.process_id
        return None


_MANAGERS: dict[Path, ManagedProcessManager] = {}
_MANAGERS_LOCK = threading.Lock()


def managed_process_manager(project_root: Path) -> ManagedProcessManager:
    """在同一 Runtime 内按 workspace 复用进程管理器。"""

    key = project_root.resolve()
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            manager = ManagedProcessManager(key)
            _MANAGERS[key] = manager
        return manager
