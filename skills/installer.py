"""Skill 安装记录。"""

from __future__ import annotations

import io
import json
import os
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from typing import Any


SKILL_INSTALL_RECORD_NAME = ".skill-install.json"
MAX_GITHUB_ARCHIVE_BYTES = 50 * 1024 * 1024
GITHUB_INSTALLER_USER_AGENT = "codex-mini-skill-installer"


@dataclass(frozen=True)
class SkillInstallRecord:
    """记录一个 skill package 的安装来源。"""

    source_type: str
    source: str
    installed_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source_type": self.source_type,
            "source": self.source,
            "installed_at": self.installed_at,
        }


@dataclass(frozen=True)
class GitHubSkillSource:
    """GitHub skill 来源定位。"""

    owner: str
    repo: str
    ref: str
    path: str

    @property
    def archive_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/archive/{self.ref}.zip"

    @property
    def install_source(self) -> str:
        suffix = f"/{self.path}" if self.path else ""
        return f"https://github.com/{self.owner}/{self.repo}/tree/{self.ref}{suffix}"


class SkillInstallError(ValueError):
    """安装 skill package 时的可展示错误。"""


def local_install_record(source_dir: Path) -> SkillInstallRecord:
    """为本地目录安装生成来源记录。"""

    return SkillInstallRecord(
        source_type="local",
        source=source_dir.resolve().as_posix(),
        installed_at=datetime.now(timezone.utc).isoformat(),
    )


def github_install_record(source: GitHubSkillSource) -> SkillInstallRecord:
    """为 GitHub 安装生成来源记录。"""

    return SkillInstallRecord(
        source_type="github",
        source=source.install_source,
        installed_at=datetime.now(timezone.utc).isoformat(),
    )


def github_auth_token() -> str | None:
    """读取可选 GitHub token；调用方不得持久化这个值。"""

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return None
    clean = str(token).strip()
    return clean or None


def github_request(
    url: str,
    *,
    accept: str | None = None,
    user_agent: str = GITHUB_INSTALLER_USER_AGENT,
) -> Request:
    """构造 GitHub request，并在存在 token 时加入 Authorization。"""

    headers = {"User-Agent": user_agent}
    if accept:
        headers["Accept"] = accept
    token = github_auth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return Request(url, headers=headers)


def parse_github_skill_source(value: Any) -> GitHubSkillSource:
    """解析 GitHub repo 或 tree URL。"""

    raw = str(value or "").strip()
    if not raw:
        raise SkillInstallError("GitHub skill source is required")
    if raw.startswith("github.com/"):
        raw = "https://" + raw
    if "://" not in raw and raw.count("/") >= 1:
        raw = "https://github.com/" + raw

    parsed = urlparse(raw)
    if parsed.netloc not in {"github.com", "www.github.com"}:
        raise SkillInstallError("GitHub skill source must be a github.com URL")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise SkillInstallError("GitHub skill source must include owner and repo")

    owner, repo = parts[0], parts[1]
    ref = "main"
    package_path = ""
    if len(parts) > 2:
        if parts[2] != "tree" or len(parts) < 4:
            raise SkillInstallError("GitHub skill source path must use /tree/{ref}/{path}")
        ref = parts[3]
        package_path = "/".join(parts[4:])
    if not _safe_github_segment(owner) or not _safe_github_segment(repo) or not _safe_github_ref(ref):
        raise SkillInstallError("GitHub skill source contains invalid path segments")
    if package_path and not _safe_relative_posix_path(package_path):
        raise SkillInstallError("GitHub skill path is invalid")
    return GitHubSkillSource(owner=owner, repo=repo, ref=ref, path=package_path)


def fetch_github_skill_package(source: GitHubSkillSource, target_root: Path) -> Path:
    """下载 GitHub archive，并只抽取指定 skill package 目录。"""

    target_root.mkdir(parents=True, exist_ok=True)
    request = github_request(source.archive_url)
    try:
        with urlopen(request, timeout=30) as response:
            archive_bytes = response.read(MAX_GITHUB_ARCHIVE_BYTES + 1)
    except OSError as exc:
        raise SkillInstallError(f"failed to download GitHub skill archive: {exc}") from exc
    if len(archive_bytes) > MAX_GITHUB_ARCHIVE_BYTES:
        raise SkillInstallError("GitHub skill archive is too large")

    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:
        raise SkillInstallError("GitHub skill archive is not a valid zip file") from exc

    with archive:
        package_dir = target_root / "package"
        package_prefix = _github_archive_package_prefix(archive, source)
        extracted = False
        total_bytes = 0
        for info in archive.infolist():
            name = info.filename
            if not name.startswith(package_prefix):
                continue
            relative_name = name[len(package_prefix) :].lstrip("/")
            if not relative_name:
                continue
            if not _safe_relative_posix_path(relative_name):
                raise SkillInstallError("GitHub skill archive contains an unsafe path")
            if _zip_info_is_symlink(info):
                raise SkillInstallError("GitHub skill archive contains a symlink")
            relative_parts = [part for part in relative_name.split("/") if part]
            target = package_dir.joinpath(*relative_parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            total_bytes += info.file_size
            if total_bytes > MAX_GITHUB_ARCHIVE_BYTES:
                raise SkillInstallError("GitHub skill package is too large")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
            extracted = True
    if not extracted or not (package_dir / "SKILL.md").exists():
        raise SkillInstallError("GitHub source does not contain a SKILL.md package")
    return package_dir


def write_install_record(skill_dir: Path, record: SkillInstallRecord) -> None:
    """把安装来源写入 skill package。"""

    path = skill_dir / SKILL_INSTALL_RECORD_NAME
    path.write_text(
        json.dumps(record.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def read_install_record(skill_dir: Path) -> dict[str, Any] | None:
    """读取 skill package 的安装来源；非法记录不影响 skill 加载。"""

    path = skill_dir / SKILL_INSTALL_RECORD_NAME
    if path.is_symlink() or not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    source_type = payload.get("source_type")
    source = payload.get("source")
    installed_at = payload.get("installed_at")
    if not all(isinstance(value, str) and value.strip() for value in (source_type, source, installed_at)):
        return None
    return {
        "source_type": source_type,
        "source": source,
        "installed_at": installed_at,
    }


def _github_archive_package_prefix(archive: zipfile.ZipFile, source: GitHubSkillSource) -> str:
    names = [info.filename for info in archive.infolist() if info.filename]
    top_levels = sorted({name.split("/", 1)[0] for name in names if "/" in name})
    if not top_levels:
        raise SkillInstallError("GitHub skill archive is empty")
    prefix = top_levels[0]
    if source.path:
        prefix = prefix + "/" + source.path.strip("/")
    return prefix.rstrip("/") + "/"


def _zip_info_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _safe_github_segment(value: str) -> bool:
    return bool(value) and "/" not in value and value not in {".", ".."}


def _safe_github_ref(value: str) -> bool:
    return bool(value) and not value.startswith("/") and ".." not in value.split("/")


def _safe_relative_posix_path(value: str) -> bool:
    if not value or value.startswith("/"):
        return False
    parts = value.split("/")
    return all(part and part not in {".", ".."} for part in parts)
