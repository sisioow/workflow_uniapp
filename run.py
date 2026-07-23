#!/usr/bin/env python3
"""启动 App 生成工作流 Web 服务。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import ensure_runtime_dirs, get_settings  # noqa: E402
from app.workflow.orchestrator import recover_all_stale_session_flags  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ensure_runtime_dirs()
    fixed = recover_all_stale_session_flags()
    if fixed:
        print(f"已同步 {fixed} 个会话的僵死运行状态")
    settings = get_settings()

    if not settings.llm_api_key:
        print("警告：未配置 llm.api_key（local_config.yaml），需求分析将失败")
    if not settings.stitch_api_key:
        print("警告：未配置 stitch.api_key（local_config.yaml），设计生成将失败")
    print(f"LLM: {settings.llm_base_url} · model={settings.llm_model}")

    host = settings.host or "0.0.0.0"
    port = settings.port
    print(f"工作流服务监听：{host}:{port}")
    print(f"  本机访问：http://127.0.0.1:{port}")
    lan_ip = _guess_lan_ip()
    if lan_ip:
        print(f"  局域网访问：http://{lan_ip}:{port}")
    elif host == "0.0.0.0":
        print("  局域网访问：http://<本机局域网IP>:%s" % port)

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
    )


def _guess_lan_ip() -> str:
    """尽力探测本机局域网 IPv4，失败则返回空字符串。"""
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return ""


if __name__ == "__main__":
    main()
