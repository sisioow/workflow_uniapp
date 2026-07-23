"""将本地 uni-app 项目发布到目标 Git 仓库的新分支并推送远程。

流程：
1. fetch 更新远程
2. 若本地/远程已存在同名分支 → 返回 branch_exists，提示用户改名
3. 基于 origin/<base> 建临时 worktree + 新分支
4. 清空工作区后拷贝项目文件并 commit / push
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_BRANCH_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]*$")

# 拷贝时始终跳过的目录/文件名
_SKIP_NAMES = {
    ".git",
    "node_modules",
    "unpackage",
    "dist",
    ".cache",
    ".temp",
    ".tmp",
    "tmp",
    "temp",
    "logs",
    ".DS_Store",
    ".hbuilderx",
    ".idea",
    ".vscode",
}


@dataclass
class PublishResult:
    ok: bool
    code: str = ""  # "" | branch_exists | error | done
    message: str = ""
    repo_path: str = ""
    branch: str = ""
    remote_url: str = ""
    commit: str = ""
    logs: list[str] = field(default_factory=list)


def default_release_branch(group_no: str = "", project_dir: str = "", app_name: str = "") -> str:
    """默认分支：组号/产品目录全拼，如 g1/huakaiduoduo。"""
    group = (group_no or "").strip()
    if group and not group.lower().startswith("g"):
        group = f"g{group}"
    if not group:
        group = "g0"

    slug = (project_dir or "").strip().lower()
    if not slug:
        raw = (app_name or "app").strip().lower()
        slug = re.sub(r"[^a-z0-9_-]+", "", raw) or "app"
    slug = slug.strip("/").replace(" ", "")
    return f"{group}/{slug}"


def _run_git(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
    timeout: float = 180.0,
) -> subprocess.CompletedProcess[str]:
    cmd = ["git", *args]
    logger.info("git %s (cwd=%s)", " ".join(args), cwd)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"git {' '.join(args)} 失败：{detail}")
    return proc


def _normalize_branch(branch: str) -> str:
    name = (branch or "").strip().strip("/")
    if not name:
        raise ValueError("分支名称不能为空")
    if ".." in name or name.startswith("-"):
        raise ValueError("非法分支名称")
    if not _BRANCH_RE.match(name):
        raise ValueError("分支名称仅允许字母、数字、. _ / -，格式如 g1/huakaiduoduo")
    return name


def _is_git_repo(path: Path) -> bool:
    if not path.is_dir():
        return False
    return (path / ".git").exists()


def _remote_url(repo: Path) -> str:
    proc = _run_git(["remote", "get-url", "origin"], cwd=repo, check=False)
    return (proc.stdout or "").strip()


def branch_exists(repo: Path, branch: str) -> bool:
    """本地或远程（origin）是否已有该分支。调用前建议先 fetch。"""
    local = _run_git(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        check=False,
    )
    if local.returncode == 0:
        return True
    remote_ref = _run_git(
        ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
        cwd=repo,
        check=False,
    )
    if remote_ref.returncode == 0:
        return True
    ls = _run_git(["ls-remote", "--heads", "origin", branch], cwd=repo, check=False, timeout=60)
    return bool((ls.stdout or "").strip())


def _clear_workdir(root: Path) -> None:
    for child in root.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)


def _should_skip(name: str) -> bool:
    if name in _SKIP_NAMES:
        return True
    if name.endswith(".log") or name.endswith(".tmp") or name.endswith(".bak"):
        return True
    return False


def copy_project_files(src: Path, dest: Path) -> int:
    """拷贝项目文件到目标目录（跳过构建产物与 .git）。返回文件数。"""
    count = 0
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        parts = rel.parts
        if any(_should_skip(p) for p in parts):
            continue
        target = dest / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if item.is_symlink():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def _remove_worktree(repo: Path, worktree: Path) -> None:
    _run_git(["worktree", "remove", "--force", str(worktree)], cwd=repo, check=False, timeout=60)
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    # 清理可能残留的分支关联
    _run_git(["worktree", "prune"], cwd=repo, check=False, timeout=30)


def publish_project_to_repo(
    project_path: str,
    repo_path: str,
    branch: str,
    *,
    base_branch: str = "main",
    app_name: str = "",
) -> PublishResult:
    """拉取最新仓库、新建分支、拷贝项目并推送。"""
    logs: list[str] = []
    project = Path(project_path).expanduser().resolve()
    repo = Path(repo_path).expanduser().resolve()

    try:
        branch = _normalize_branch(branch)
    except ValueError as exc:
        return PublishResult(ok=False, code="error", message=str(exc), repo_path=str(repo), branch=branch)

    if not project.is_dir() or not (project / "manifest.json").exists():
        return PublishResult(
            ok=False,
            code="error",
            message=f"源项目无效（缺少 manifest.json）：{project}",
            repo_path=str(repo),
            branch=branch,
        )
    if not _is_git_repo(repo):
        return PublishResult(
            ok=False,
            code="error",
            message=f"目标路径不是 Git 仓库：{repo}",
            repo_path=str(repo),
            branch=branch,
        )

    remote = _remote_url(repo)
    logs.append(f"仓库：{repo}")
    logs.append(f"远程：{remote or '(无 origin)'}")
    logs.append(f"分支：{branch}")
    logs.append(f"基准：origin/{base_branch}")

    worktree: Path | None = None
    created_branch = False
    try:
        logs.append("拉取远程更新…")
        _run_git(["fetch", "origin", "--prune"], cwd=repo, timeout=300)

        if branch_exists(repo, branch):
            msg = f"分支「{branch}」已存在，请修改分支名称后再发布"
            logs.append(msg)
            return PublishResult(
                ok=False,
                code="branch_exists",
                message=msg,
                repo_path=str(repo),
                branch=branch,
                remote_url=remote,
                logs=logs,
            )

        # 确认基准分支存在
        base_ok = _run_git(
            ["rev-parse", "--verify", f"origin/{base_branch}"],
            cwd=repo,
            check=False,
        )
        if base_ok.returncode != 0:
            return PublishResult(
                ok=False,
                code="error",
                message=f"找不到基准分支 origin/{base_branch}，请先确认仓库已拉取",
                repo_path=str(repo),
                branch=branch,
                remote_url=remote,
                logs=logs,
            )

        worktree = Path(tempfile.mkdtemp(prefix="uniapp_publish_"))
        # mkdtemp 已建空目录，worktree add 要求路径不存在
        worktree.rmdir()
        logs.append(f"创建分支与工作树：{worktree}")
        _run_git(
            ["worktree", "add", "-b", branch, str(worktree), f"origin/{base_branch}"],
            cwd=repo,
            timeout=120,
        )
        created_branch = True

        logs.append("清空工作区并拷贝项目文件…")
        _clear_workdir(worktree)
        copied = copy_project_files(project, worktree)
        logs.append(f"已拷贝 {copied} 个文件")

        _run_git(["add", "-A"], cwd=worktree)
        status = _run_git(["status", "--porcelain"], cwd=worktree, check=False)
        if not (status.stdout or "").strip():
            raise RuntimeError("工作区无变更，无法提交（请检查源项目是否为空）")

        title = (app_name or project.name).strip() or branch
        msg = f"publish: {title} ({branch})"
        commit_proc = _run_git(["commit", "-m", msg], cwd=worktree, check=False)
        if commit_proc.returncode != 0:
            # 仓库未配置 user.name/email 时回退本地身份
            _run_git(
                [
                    "-c",
                    "user.email=workflow@local",
                    "-c",
                    "user.name=workflow",
                    "commit",
                    "-m",
                    msg,
                ],
                cwd=worktree,
            )
        sha_proc = _run_git(["rev-parse", "--short", "HEAD"], cwd=worktree)
        commit = (sha_proc.stdout or "").strip()
        logs.append(f"推送 origin {branch}…")
        _run_git(["push", "-u", "origin", branch], cwd=worktree, timeout=300)

        logs.append(f"✅ 发布成功：{branch} @ {commit}")
        return PublishResult(
            ok=True,
            code="done",
            message=f"已推送到远程分支 {branch}",
            repo_path=str(repo),
            branch=branch,
            remote_url=remote,
            commit=commit,
            logs=logs,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("publish failed")
        logs.append(f"❌ {exc}")
        # 失败时清理新建本地分支（worktree 移除后）
        result = PublishResult(
            ok=False,
            code="error",
            message=str(exc),
            repo_path=str(repo),
            branch=branch,
            remote_url=remote,
            logs=logs,
        )
        if created_branch:
            try:
                if worktree is not None:
                    _remove_worktree(repo, worktree)
                    worktree = None
                _run_git(["branch", "-D", branch], cwd=repo, check=False)
            except Exception:  # noqa: BLE001
                pass
        return result
    finally:
        if worktree is not None:
            try:
                _remove_worktree(repo, worktree)
            except Exception:  # noqa: BLE001
                pass
