"""借助 HBuilderX 内置工具链，将 uni-app 项目运行到 Chrome 并解析测试端口。

优先使用官方 CLI：`cli launch web --browser Chrome`（HBuilderX 5.0+）。
当前正式版 4.87 尚未内置 launch 时，回退为与 IDE 相同的 `uniapp-cli -p h5` 开发服务，
再自动用 Chrome 打开编译成功后的 Local 地址。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_URL_RE = re.compile(
    r"(?:Local|Network)\s*:\s*(https?://(?:localhost|127\.0\.0\.1)[^\s]*)",
    re.IGNORECASE,
)
_PORT_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1):(\d+)", re.IGNORECASE)
_OK_RE = re.compile(r"编译成功|App running at|Compiled successfully", re.IGNORECASE)

# 为各会话预留的独立 H5 端口区间（避开常见 8080/8081）
_H5_PORT_LO = 8100
_H5_PORT_HI = 8999

# session_id -> 后台 H5 开发服务进程
_session_procs: dict[str, subprocess.Popen] = {}
_session_ports: dict[str, int] = {}
_lock = threading.Lock()


@dataclass
class H5RunResult:
    ok: bool
    url: str = ""
    port: int = 0
    message: str = ""
    log_tail: str = ""
    via: str = ""  # launch | uniapp-cli


def default_hbuilderx_app() -> Path:
    for candidate in (
        Path("/Applications/HBuilderX.app"),
        Path("/Applications/HBuilderX-Alpha.app"),
        Path.home() / "Applications" / "HBuilderX.app",
    ):
        if candidate.exists():
            return candidate
    return Path("/Applications/HBuilderX.app")


def resolve_cli(cli_path: str = "", app_path: str = "") -> Path:
    if cli_path:
        p = Path(cli_path).expanduser()
        if p.exists():
            return p
    app = Path(app_path).expanduser() if app_path else default_hbuilderx_app()
    mac_cli = app / "Contents" / "MacOS" / "cli"
    if mac_cli.exists():
        return mac_cli
    win_cli = app / "cli.exe"
    if win_cli.exists():
        return win_cli
    linux_cli = app / "cli"
    if linux_cli.exists():
        return linux_cli
    which = shutil.which("cli")
    if which:
        return Path(which)
    raise FileNotFoundError(
        "未找到 HBuilderX CLI。请在 local_config.yaml 配置 hbuilderx.cli 或 hbuilderx.app"
    )


def resolve_hx_root(cli: Path) -> Path:
    """从 cli 路径推导 HBuilderX.app/Contents/HBuilderX 插件根目录。"""
    # .../HBuilderX.app/Contents/MacOS/cli
    mac_plugins = cli.parent.parent / "HBuilderX"
    if (mac_plugins / "plugins" / "uniapp-cli").exists():
        return mac_plugins
    # 安装目录下直接放 plugins 的结构
    if (cli.parent / "plugins" / "uniapp-cli").exists():
        return cli.parent
    raise FileNotFoundError(f"无法从 CLI 定位 HBuilderX 插件目录: {cli}")


def _cli_supports_launch_web(cli: Path) -> bool:
    try:
        proc = subprocess.run(
            [str(cli), "help"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        text = (proc.stdout or "") + (proc.stderr or "")
        return "launch web" in text.lower() or "\nlaunch\t" in text.lower()
    except Exception:  # noqa: BLE001
        return False


def ensure_hbuilderx_open(cli: Path) -> None:
    try:
        subprocess.run([str(cli), "open"], capture_output=True, text=True, timeout=30, check=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("启动 HBuilderX 失败（可忽略）: %s", exc)


def project_open(cli: Path, project_path: str) -> str:
    proc = subprocess.run(
        [str(cli), "project", "open", "--path", project_path],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return ((proc.stdout or "") + (proc.stderr or "")).strip()


def stop_h5_session(session_id: str) -> None:
    with _lock:
        proc = _session_procs.pop(session_id, None)
        _session_ports.pop(session_id, None)
    if proc and proc.poll() is None:
        _kill_proc_tree(proc)


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def _preferred_port_for_session(session_id: str) -> int:
    digest = hashlib.md5(session_id.encode("utf-8")).hexdigest()
    span = _H5_PORT_HI - _H5_PORT_LO + 1
    return _H5_PORT_LO + (int(digest[:8], 16) % span)


def allocate_h5_port(session_id: str) -> int:
    """为会话分配空闲端口：同会话尽量稳定，不同项目互不冲突。"""
    preferred = _preferred_port_for_session(session_id)
    with _lock:
        taken = set(_session_ports.values())
    for offset in range(_H5_PORT_HI - _H5_PORT_LO + 1):
        port = _H5_PORT_LO + ((preferred - _H5_PORT_LO + offset) % (_H5_PORT_HI - _H5_PORT_LO + 1))
        if port in taken:
            continue
        if not _port_in_use(port):
            with _lock:
                _session_ports[session_id] = port
            return port
    # 极端情况：交给系统分配临时端口
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    with _lock:
        _session_ports[session_id] = port
    return port


def reset_h5_build_output(project_path: str | Path) -> None:
    """清空 H5 编译产物，保证每次测试从空目录重新初始化。"""
    project = Path(project_path).expanduser().resolve()
    targets = (
        project / "unpackage" / "dist" / "dev" / "web",
        project / "unpackage" / "dist" / "build" / "web",
        project / "unpackage" / "dist" / "dev" / ".tmp",
    )
    for path in targets:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            logger.info("已清空 H5 输出目录: %s", path)


def _kill_proc_tree(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:  # noqa: BLE001
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


def _open_chrome(url: str) -> None:
    # macOS
    chrome = Path("/Applications/Google Chrome.app")
    if chrome.exists():
        subprocess.Popen(["open", "-a", "Google Chrome", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    # 通用回退
    subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _parse_url_port(text: str) -> tuple[str, int]:
    urls = _URL_RE.findall(text)
    for raw in urls:
        url = raw.strip().rstrip(".,;")
        m = _PORT_RE.search(url)
        if m:
            return url, int(m.group(1))
    m = _PORT_RE.search(text)
    if m:
        port = int(m.group(1))
        return f"http://localhost:{port}/", port
    return "", 0


def _validation_rules_path(hx_root: Path) -> str:
    # 优先用户扩展缓存中的 rules（与 IDE 一致）
    support = Path.home() / "Library" / "Application Support" / "HBuilder X"
    candidates = list(support.glob("extensions/uniapp-extension/compiler/*/rules.json"))
    if candidates:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return str(candidates[0])
    bundled = hx_root / "plugins" / "uniapp-extension" / "static" / "rules.json"
    if bundled.exists():
        return str(bundled)
    return ""


def _build_uniapp_cli_env(hx_root: Path, project: Path, *, port: int) -> dict[str, str]:
    node_bin = hx_root / "plugins" / "node"
    cache_dir = project / "unpackage" / "cache" / "cache"
    out_dir = project / "unpackage" / "dist" / "dev" / "web"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    path_parts = [str(node_bin), str(hx_root / "plugins" / "uniapp-cli" / "node_modules" / ".bin")]
    env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH", "")])
    env["NODE_ENV"] = "development"
    env["NODE_SKIP_PLATFORM_CHECK"] = "1"
    env["UNI_PLATFORM"] = "h5"
    env["UNI_INPUT_DIR"] = str(project)
    env["UNI_OUTPUT_DIR"] = str(out_dir)
    env["HX_RUN_DEVICE_TYPE"] = "h5"
    env["HX_DEPENDENCIES_DIR"] = str(cache_dir)
    env["HBUILDER_EXTENSIONS_DIR"] = str(hx_root / "plugins")
    env["UNI_HBUILDERX_LANGID"] = "zh_CN"
    env["UNI_SOCKET_HOSTS"] = "127.0.0.1"
    env["FORCE_KILL_EXTENSION_HOST_ON_EXIT"] = "0"
    # vue-cli-service: args.port || process.env.PORT || …
    env["PORT"] = str(port)
    # 版本号尽量从 app 名推断
    env.setdefault("HX_Version", "4.87")
    rules = _validation_rules_path(hx_root)
    if rules:
        env["UNI_COMPILER_VALIDATION_RULES_PATH"] = rules
    return env


def _wait_for_ready(proc: subprocess.Popen, timeout: float) -> tuple[bool, str, int, str]:
    """读 stdout/stderr，直到编译成功并拿到端口，或超时 / 进程退出。"""
    chunks: list[str] = []
    url, port = "", 0
    ok = False
    deadline = time.time() + timeout

    def _reader(stream) -> None:
        nonlocal url, port, ok
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            if not line:
                break
            chunks.append(line)
            if len(chunks) > 400:
                del chunks[:-300]
            text = "".join(chunks[-80:])
            if not url:
                url, port = _parse_url_port(text)
            if _OK_RE.search(line) or (url and port):
                # 同时有地址、且出现过成功/running 即可
                if url and port and (_OK_RE.search(text) or "localhost" in text.lower()):
                    ok = True

    t_out = threading.Thread(target=_reader, args=(proc.stdout,), daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr,), daemon=True)
    t_out.start()
    t_err.start()

    while time.time() < deadline:
        if ok and url and port:
            break
        if proc.poll() is not None:
            break
        time.sleep(0.2)

    log_tail = "".join(chunks[-60:])
    # 再扫一遍全量片段，防止竞态遗漏
    if not url:
        url, port = _parse_url_port("".join(chunks))
    if url and port and _OK_RE.search("".join(chunks)):
        ok = True
    elif url and port and proc.poll() is None:
        # 服务仍在跑且已给出 Local 地址，视为可用
        ok = True
    return ok, url, port, log_tail


def _run_via_launch(cli: Path, project_path: str, timeout: float) -> H5RunResult:
    cmd = [
        str(cli),
        "launch",
        "web",
        "--project",
        project_path,
        "--browser",
        "Chrome",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    ok, url, port, log_tail = _wait_for_ready(proc, timeout)
    # launch 可能在编译后仍挂起；若已成功则后台保留
    if not ok and proc.poll() is not None:
        return H5RunResult(
            ok=False,
            message="HBuilderX launch web 未编译成功",
            log_tail=log_tail,
            via="launch",
        )
    if ok:
        return H5RunResult(ok=True, url=url, port=port, message="编译成功", log_tail=log_tail, via="launch")
    _kill_proc_tree(proc)
    return H5RunResult(ok=False, message="等待编译超时", log_tail=log_tail, via="launch")


def _run_via_uniapp_cli(
    session_id: str,
    hx_root: Path,
    project_path: str,
    timeout: float,
    open_chrome: bool,
) -> H5RunResult:
    stop_h5_session(session_id)

    project = Path(project_path).resolve()
    # 每次测试从空产物目录重新编译，避免沿用上一次/其他项目缓存
    reset_h5_build_output(project)
    port = allocate_h5_port(session_id)

    uni_cli = hx_root / "plugins" / "uniapp-cli" / "bin" / "uniapp-cli.js"
    node = hx_root / "plugins" / "node" / "node"
    kill_js = hx_root / "plugins" / "uniapp-extension" / "static" / "kill.js"
    if not uni_cli.exists():
        return H5RunResult(ok=False, message=f"未找到 uniapp-cli: {uni_cli}", via="uniapp-cli")
    if not node.exists():
        return H5RunResult(ok=False, message=f"未找到 HBuilderX Node: {node}", via="uniapp-cli")

    cmd = [str(node), "--max-old-space-size=3072", "--no-warnings"]
    if kill_js.exists():
        cmd += ["-r", str(kill_js)]
    # uniapp-cli 不转发 --port；通过 PORT 环境变量交给 vue-cli-service
    cmd += [str(uni_cli), "-p", "h5"]

    env = _build_uniapp_cli_env(hx_root, project, port=port)
    cwd = str(hx_root / "plugins" / "uniapp-cli")
    logger.info("H5 测试启动 session=%s port=%s project=%s", session_id, port, project)

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    with _lock:
        _session_procs[session_id] = proc
        _session_ports[session_id] = port

    ok, url, got_port, log_tail = _wait_for_ready(proc, timeout)
    if not ok:
        stop_h5_session(session_id)
        hint = "编译失败或超时"
        if "编译失败" in log_tail:
            hint = "HBuilderX 编译失败，请查看日志"
        return H5RunResult(ok=False, message=hint, log_tail=log_tail, via="uniapp-cli")

    # 日志未解析到端口时，回退为我们分配的端口
    if not got_port:
        got_port = port
        url = url or f"http://localhost:{port}/"
    elif got_port != port:
        logger.warning(
            "H5 实际端口 %s 与分配端口 %s 不一致，以实际端口为准 session=%s",
            got_port,
            port,
            session_id,
        )
        with _lock:
            _session_ports[session_id] = got_port

    chrome_hint = ""
    if open_chrome and url:
        try:
            _open_chrome(url)
            chrome_hint = "，已尝试用 Chrome 打开"
        except Exception as exc:  # noqa: BLE001
            logger.warning("打开 Chrome 失败: %s", exc)
            chrome_hint = "，打开 Chrome 失败"

    return H5RunResult(
        ok=True,
        url=url,
        port=got_port,
        message=f"编译成功（端口 {got_port}）{chrome_hint}",
        log_tail=log_tail,
        via="uniapp-cli",
    )


def run_h5_to_chrome(
    project_path: str,
    *,
    session_id: str,
    cli_path: str = "",
    app_path: str = "",
    open_chrome: bool = True,
    timeout: float = 180.0,
) -> H5RunResult:
    """打开/编译项目并返回测试 URL（含端口）。"""
    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        return H5RunResult(ok=False, message=f"项目目录不存在: {project}")
    if not (project / "manifest.json").exists():
        return H5RunResult(ok=False, message="不是有效的 uni-app 项目（缺少 manifest.json）")

    try:
        cli = resolve_cli(cli_path, app_path)
    except FileNotFoundError as exc:
        return H5RunResult(ok=False, message=str(exc))

    ensure_hbuilderx_open(cli)
    try:
        open_msg = project_open(cli, str(project))
        logger.info("HBuilderX project open: %s", open_msg[:200])
    except Exception as exc:  # noqa: BLE001
        logger.warning("project open 失败（继续尝试编译）: %s", exc)

    if _cli_supports_launch_web(cli):
        result = _run_via_launch(cli, str(project), timeout)
        if result.ok:
            if open_chrome and result.url:
                try:
                    _open_chrome(result.url)
                except Exception:  # noqa: BLE001
                    pass
            return result
        logger.warning("launch web 失败，回退 uniapp-cli: %s", result.message)

    try:
        hx_root = resolve_hx_root(cli)
    except FileNotFoundError as exc:
        return H5RunResult(ok=False, message=str(exc))

    return _run_via_uniapp_cli(session_id, hx_root, str(project), timeout, open_chrome)
