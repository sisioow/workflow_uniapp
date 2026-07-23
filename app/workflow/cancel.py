"""会话级取消：协作式中断设计循环，并强制结束 CLI 子进程。"""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class WorkflowCancelled(Exception):
    """用户终止当前工作流环节。"""


class SessionCancelHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._procs: dict[str, subprocess.Popen] = {}

    def reset(self, session_id: str) -> None:
        """开始新任务前清除取消标记。"""
        with self._lock:
            self._events[session_id] = threading.Event()
            old = self._procs.pop(session_id, None)
        if old and old.poll() is None:
            self._kill_proc(old)

    def clear(self, session_id: str) -> None:
        """任务结束时清理子进程登记；取消标记保留到下次 reset，避免竞态。"""
        with self._lock:
            old = self._procs.pop(session_id, None)
        if old and old.poll() is None:
            self._kill_proc(old)

    def request_cancel(self, session_id: str) -> bool:
        """标记取消并尝试杀掉正在运行的子进程。返回是否有活跃标记。"""
        with self._lock:
            ev = self._events.get(session_id)
            if ev is None:
                ev = threading.Event()
                self._events[session_id] = ev
            ev.set()
            proc = self._procs.get(session_id)
        if proc and proc.poll() is None:
            self._kill_proc(proc)
            logger.info("已终止会话 %s 的子进程 pid=%s", session_id, proc.pid)
            return True
        logger.info("已标记会话 %s 取消", session_id)
        return True

    def is_cancelled(self, session_id: str) -> bool:
        with self._lock:
            ev = self._events.get(session_id)
            return bool(ev and ev.is_set())

    def check(self, session_id: str) -> None:
        if self.is_cancelled(session_id):
            raise WorkflowCancelled("用户终止了当前环节")

    def get_live_proc(self, session_id: str) -> Optional[subprocess.Popen]:
        """返回仍在运行的已登记子进程；无则 None。"""
        with self._lock:
            proc = self._procs.get(session_id)
        if proc is None:
            return None
        if proc.poll() is not None:
            return None
        return proc

    def has_live_proc(self, session_id: str) -> bool:
        return self.get_live_proc(session_id) is not None

    def register_proc(self, session_id: str, proc: subprocess.Popen) -> None:
        with self._lock:
            old = self._procs.get(session_id)
            self._procs[session_id] = proc
            cancelled = bool(self._events.get(session_id) and self._events[session_id].is_set())
        if old and old is not proc and old.poll() is None:
            self._kill_proc(old)
        if cancelled and proc.poll() is None:
            self._kill_proc(proc)

    def unregister_proc(self, session_id: str, proc: Optional[subprocess.Popen] = None) -> None:
        with self._lock:
            current = self._procs.get(session_id)
            if proc is None or current is proc:
                self._procs.pop(session_id, None)

    @staticmethod
    def _kill_proc(proc: subprocess.Popen) -> None:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


cancel_hub = SessionCancelHub()
