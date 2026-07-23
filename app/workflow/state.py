from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config import SESSIONS_DIR, ensure_runtime_dirs
from app.models import WorkflowState, WorkflowStep


def _parse_created_at(value: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _session_created_ts(session_dir: Path, data: dict) -> float:
    """优先用 state.created_at；旧会话回退到目录创建时间。"""
    parsed = _parse_created_at(str(data.get("created_at") or ""))
    if parsed is not None:
        return parsed
    try:
        st = session_dir.stat()
        return float(getattr(st, "st_birthtime", None) or st.st_ctime)
    except OSError:
        return 0.0


class StateStore:
    def __init__(self, session_id: str | None = None) -> None:
        ensure_runtime_dirs()
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.path = SESSIONS_DIR / self.session_id / "state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> WorkflowState:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return WorkflowState.model_validate(data)
        return WorkflowState(session_id=self.session_id)

    def save(self, state: WorkflowState) -> None:
        self.path.write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def delete(self) -> None:
        """删除该会话的所有数据。"""
        if self.path.exists():
            self.path.unlink()
        # 如果目录为空，删除目录
        if self.path.parent.exists():
            try:
                self.path.parent.rmdir()
            except OSError:
                # 目录不为空，忽略
                pass

    @staticmethod
    def delete_session(session_id: str) -> None:
        """删除指定的会话及其所有数据。"""
        ensure_runtime_dirs()
        session_dir = SESSIONS_DIR / session_id
        if session_dir.exists():
            import shutil
            shutil.rmtree(session_dir)

    @staticmethod
    def list_sessions() -> list[dict[str, str]]:
        ensure_runtime_dirs()
        items: list[dict[str, str]] = []
        for p in SESSIONS_DIR.iterdir():
            if not p.is_dir():
                continue
            state_file = p / "state.json"
            if not state_file.exists():
                continue
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                created_ts = _session_created_ts(p, data)
                items.append(
                    {
                        "session_id": data.get("session_id", p.name),
                        "app_name": data.get("app_name", ""),
                        "group_no": (data.get("group_no") or "").strip(),
                        "step": data.get("step", ""),
                        "created": str(created_ts),
                        "updated": str(state_file.stat().st_mtime),
                    }
                )
            except (json.JSONDecodeError, OSError):
                continue
        # 按创建时间倒序：最新在上
        items.sort(key=lambda x: float(x.get("created") or 0), reverse=True)
        return items


def parse_json_from_llm(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def extract_project_id(name: str) -> str:
    """projects/123 -> 123"""
    if "/" in name:
        return name.rsplit("/", 1)[-1]
    return name


def extract_screen_id(name: str) -> str:
    if "/screens/" in name:
        return name.split("/screens/")[-1]
    if "/" in name:
        return name.rsplit("/", 1)[-1]
    return name


def iter_sse(data: dict) -> Iterator[str]:
    payload = json.dumps(data, ensure_ascii=False)
    yield f"data: {payload}\n\n"


def next_step_after(step: WorkflowStep) -> WorkflowStep:
    order = [
        WorkflowStep.INPUT,
        WorkflowStep.ANALYZE,
        WorkflowStep.REVIEW_REQUIREMENT,
        WorkflowStep.SELECT_STYLES,
        WorkflowStep.DESIGN,
        WorkflowStep.SELECT_PLAN,
        WorkflowStep.INIT_PROJECT,
        WorkflowStep.CODEGEN,
        WorkflowStep.DONE,
    ]
    idx = order.index(step) if step in order else 0
    return order[min(idx + 1, len(order) - 1)]
