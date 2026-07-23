"""在本机用 Cursor IDE 打开项目目录。"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


def resolve_cursor_ide_bin() -> Path:
    """解析 Cursor 桌面端 CLI（非 agent 子命令）。"""
    settings = get_settings()
    raw = (getattr(settings, "cursor_bin", None) or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if p.name.lower() == "cursor" and p.exists():
            return p

    which = shutil.which("cursor")
    if which:
        return Path(which)

    for candidate in (
        Path("/Applications/Cursor.app/Contents/Resources/app/bin/cursor"),
        Path.home() / "Applications/Cursor.app/Contents/Resources/app/bin/cursor",
    ):
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "未找到 Cursor 可执行文件。请安装 Cursor 或将 cursor CLI 加入 PATH"
    )


def open_project_in_cursor(project_path: str, *, new_window: bool = True) -> str:
    """用 Cursor 打开项目目录；默认新窗口。"""
    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        raise FileNotFoundError(f"项目目录不存在: {project}")

    cli = resolve_cursor_ide_bin()
    cmd = [str(cli)]
    if new_window:
        cmd.append("-n")
    cmd.append(str(project))

    logger.info("打开 Cursor: %s", " ".join(cmd))
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return str(project)
