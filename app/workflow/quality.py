"""代码生成质量检查与反馈统计。

质量检查（层 3 — 评估与约束）：
- 验证 4 个页面文件是否存在且非空
- 验证 pages.json 路由是否注册
- 计算质量评分

反馈统计（层 4 — 反馈）：
- 持久化错误和评分到 .runtime/feedback.json
- 根据历史数据给出改进建议
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import ROOT_DIR
from app.models import QualityCheckItem, QualityReport, UserFeedback

logger = logging.getLogger(__name__)

FEEDBACK_PATH = ROOT_DIR / ".runtime" / "feedback.json"


# ── 质量检查 ──

def run_quality_checks(project_path: str) -> QualityReport:
    """对生成的 uni-app 项目执行自动化质量检查。"""
    base = Path(project_path)
    checks: list[QualityCheckItem] = []

    # 检查 4 个页面文件
    page_keys = ["page1", "page2", "page3", "page4"]
    for key in page_keys:
        vue_file = base / "pages" / key / f"{key}.vue"
        if vue_file.exists():
            size_kb = vue_file.stat().st_size / 1024
            checks.append(QualityCheckItem(
                label=f"{key}.vue 文件存在",
                passed=size_kb > 0.1,
                detail=f"{size_kb:.1f} KB" if size_kb > 0.1 else "文件为空",
            ))
        else:
            checks.append(QualityCheckItem(
                label=f"{key}.vue 文件存在",
                passed=False,
                detail="文件缺失",
            ))

    # 检查 pages.json 路由注册
    pages_json = base / "pages.json"
    if pages_json.exists():
        try:
            data = json.loads(pages_json.read_text(encoding="utf-8"))
            registered = {p["path"] for p in data.get("pages", [])}
            for key in page_keys:
                expected = f"pages/{key}/{key}"
                checks.append(QualityCheckItem(
                    label=f"{key} 路由已注册",
                    passed=expected in registered,
                    detail="已注册" if expected in registered else "未注册",
                ))
        except (json.JSONDecodeError, KeyError) as exc:
            checks.append(QualityCheckItem(
                label="pages.json 解析",
                passed=False,
                detail=f"解析失败: {exc}",
            ))
    else:
        checks.append(QualityCheckItem(
            label="pages.json 存在",
            passed=False,
            detail="文件缺失",
        ))

    # 计算总分
    total = len(checks)
    passed = sum(1 for c in checks if c.passed)
    score = round(passed / total * 100) if total > 0 else 0

    return QualityReport(checks=checks, score=score)


# ── 反馈持久化 ──

def load_feedback_db() -> dict[str, Any]:
    """加载反馈数据库。"""
    if not FEEDBACK_PATH.exists():
        return {"sessions": [], "error_stats": {}, "total_score": 0, "count": 0}
    try:
        return json.loads(FEEDBACK_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"sessions": [], "error_stats": {}, "total_score": 0, "count": 0}


def save_feedback_db(db: dict[str, Any]) -> None:
    """保存反馈数据库。"""
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    FEEDBACK_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def record_feedback(
    session_id: str,
    app_name: str,
    quality: QualityReport,
    feedback: UserFeedback,
) -> None:
    """记录一次完整的评估与反馈。"""
    db = load_feedback_db()

    entry = {
        "session_id": session_id,
        "app_name": app_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "quality_score": quality.score,
        "quality_checks": [
            {"label": c.label, "passed": c.passed, "detail": c.detail}
            for c in quality.checks
        ],
        "user_rating": feedback.rating,
        "user_score": feedback.score,
        "user_comment": feedback.comment,
    }
    db["sessions"].append(entry)
    if len(db["sessions"]) > 50:
        db["sessions"] = db["sessions"][-50:]

    # 更新评分统计
    if feedback.score > 0:
        db["total_score"] = db.get("total_score", 0) + feedback.score
        db["count"] = db.get("count", 0) + 1

    save_feedback_db(db)
    logger.info(f"反馈已记录: session={session_id}, rating={feedback.rating}")


def record_error(step: str, error_type: str, detail: str) -> None:
    """记录错误到统计数据库（用于反馈层分析）。"""
    db = load_feedback_db()
    key = f"{step}:{error_type}"
    stats = db.setdefault("error_stats", {})
    entry = stats.setdefault(key, {"count": 0, "last_seen": None, "samples": []})
    entry["count"] += 1
    entry["last_seen"] = datetime.now(timezone.utc).isoformat()
    entry["samples"].append(detail[:200])
    if len(entry["samples"]) > 5:
        entry["samples"] = entry["samples"][-5:]
    save_feedback_db(db)


def get_improvement_hints() -> list[str]:
    """根据历史错误统计返回改进建议。"""
    db = load_feedback_db()
    hints: list[str] = []

    stats = db.get("error_stats", {})
    stitch_count = sum(
        v["count"] for k, v in stats.items()
        if "stitch" in k.lower() or "design" in k.lower()
    )
    if stitch_count >= 3:
        hints.append("💡 Stitch 设计多次失败，可尝试「跳过设计，直接代码生成」路线")

    llm_count = sum(
        v["count"] for k, v in stats.items()
        if "llm" in k.lower() or "json" in k.lower()
    )
    if llm_count >= 2:
        hints.append("💡 LLM JSON 解析多次失败，可尝试降低 temperature 或更换模型")

    codex_count = sum(
        v["count"] for k, v in stats.items()
        if "codex" in k.lower()
    )
    if codex_count >= 1:
        hints.append("💡 Codex 执行失败，检查 CODEX_SANDBOX 是否设为 workspace-write")

    # 从用户反馈中统计
    sessions = db.get("sessions", [])
    bad_count = sum(1 for s in sessions if s.get("user_rating") == "bad")
    if bad_count >= 2:
        hints.append("💡 多项目评分较低，建议检查需求方案是否过于复杂")

    return hints
