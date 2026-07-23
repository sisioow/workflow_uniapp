from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.config import ROOT_DIR, get_llm_model_options, get_settings, load_yaml_config
from app.models import WorkflowStep
from app.workflow.cancel import cancel_hub
from app.workflow.orchestrator import WorkflowOrchestrator
from app.workflow.state import StateStore, iter_sse

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

app = FastAPI(title="App 生成工作流", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

# session_id -> 后台 asyncio Task 集合（用于取消）
_session_tasks: dict[str, set[asyncio.Task]] = defaultdict(set)


def _track_session_task(session_id: str, task: asyncio.Task) -> asyncio.Task:
    _session_tasks[session_id].add(task)

    def _done(t: asyncio.Task) -> None:
        _session_tasks[session_id].discard(t)

    task.add_done_callback(_done)
    return task


async def _cancel_session_tasks(session_id: str) -> None:
    tasks = list(_session_tasks.get(session_id, ()))
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class StartRequest(BaseModel):
    app_name: str = Field(min_length=1, max_length=60)
    project_dir: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    group_no: str = Field(default="", max_length=40)
    llm_model: str = ""


class StyleSelection(BaseModel):
    style_ids: list[str] = Field(min_length=1)
    colors: str = ""


class PlanSelection(BaseModel):
    task_index: int = Field(ge=0)
    framework: str = "vue"
    codegen_tool: str = "claude"  # 默认 Claude Code


class PageUpdate(BaseModel):
    key: str
    title: str
    features: list[str] = Field(default_factory=list)
    description: str = ""


class RequirementUpdate(BaseModel):
    app_name: Optional[str] = None
    summary: Optional[str] = None
    direction: Optional[str] = None
    pages: Optional[List[PageUpdate]] = None
    selected_index: Optional[int] = None


class RequirementOptionSelect(BaseModel):
    index: int = Field(ge=0, le=2)


class AnalyzeRetryRequest(BaseModel):
    """重试需求分析：可选补充提示词与切换模型。"""
    extra_prompt: str = ""
    llm_model: str = ""


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    cfg = load_yaml_config()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "styles": cfg.get("ui_styles", []),
            "frameworks": cfg.get("frameworks", []),
        },
    )


@app.get("/api/config")
async def api_config() -> dict[str, Any]:
    settings = get_settings()
    cfg = load_yaml_config()
    return {
        "llm_model": settings.llm_model,
        "llm_provider": settings.llm_provider,
        "llm_base_url": settings.llm_base_url,
        "llm_models": get_llm_model_options(),
        "styles": cfg.get("ui_styles", []),
        "frameworks": cfg.get("frameworks", []),
        "template_dir": settings.uniapp_template_dir,
        "output_dir": settings.uniapp_output_dir,
        "publish_default_repo": settings.publish_default_repo,
        "publish_base_branch": settings.publish_base_branch,
    }


@app.get("/api/sessions")
async def list_sessions() -> list[dict[str, str]]:
    return StateStore.list_sessions()


@app.get("/api/session/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    orch = WorkflowOrchestrator(session_id)
    return orch.get_state().to_public_dict()


@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    """删除会话：先终止相关进程与后台任务，再清理会话数据。"""
    try:
        # 终止 CLI 子进程（设计/代码生成等）与 H5 测试进程
        cancel_hub.request_cancel(session_id)
        try:
            from app.hbuilderx import stop_h5_session

            stop_h5_session(session_id)
        except Exception:  # noqa: BLE001
            logger.exception("stop h5 session failed on delete: %s", session_id)
        await _cancel_session_tasks(session_id)
        _session_tasks.pop(session_id, None)

        StateStore.delete_session(session_id)
        return {"ok": True, "message": f"会话 {session_id} 已删除"}
    except Exception as exc:
        logger.exception("delete session failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/session/start")
async def start_session(body: StartRequest) -> dict[str, Any]:
    orch = WorkflowOrchestrator()
    try:
        state = orch.start(body.app_name, body.project_dir, body.llm_model, body.group_no)
        return state.to_public_dict()
    except Exception as exc:
        logger.exception("start failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/session/{session_id}/confirm-requirement")
async def confirm_requirement(session_id: str) -> dict[str, Any]:
    """用户确认需求方案后，进入 UI 风格选择步骤。"""
    orch = WorkflowOrchestrator(session_id)
    try:
        state = orch.confirm_requirement()
        return state.to_public_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/session/{session_id}/analyze")
async def retry_analyze(
    session_id: str,
    body: Optional[AnalyzeRetryRequest] = None,
) -> dict[str, Any]:
    """重试需求分析（失败恢复，或审核方案时按补充提示词重新生成）。"""
    req = body or AnalyzeRetryRequest()
    orch = WorkflowOrchestrator(session_id)
    try:
        state = orch.prepare_retry_analyze(llm_model=req.llm_model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    extra_prompt = (req.extra_prompt or "").strip()

    async def _run() -> None:
        try:
            await asyncio.to_thread(orch.retry_analyze, extra_prompt)
            logger.info("需求分析重试完成: %s", session_id)
        except asyncio.CancelledError:
            logger.info("需求分析重试已取消: %s", session_id)
            raise
        except Exception as exc:
            logger.exception("需求分析重试异常: %s", session_id)
            state_err = orch.get_state()
            if cancel_hub.is_cancelled(session_id):
                return
            state_err.step = WorkflowStep.ERROR
            state_err.error_step = WorkflowStep.ANALYZE
            state_err.analyze_running = False
            state_err.error = f"需求分析异常: {exc}"
            state_err.append_log(f"❌ 需求分析异常: {exc}")
            orch.save(state_err)

    _track_session_task(session_id, asyncio.create_task(_run()))
    return {"ok": True, "message": "需求分析已启动，请订阅 SSE 查看进度", "data": state.to_public_dict()}


@app.put("/api/session/{session_id}/requirement")
async def update_requirement(session_id: str, body: RequirementUpdate) -> dict[str, Any]:
    """用户修改需求方案（应用名称、页面标题、功能点等）。"""
    orch = WorkflowOrchestrator(session_id)
    try:
        state = orch.update_requirement(body)
        return state.to_public_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/session/{session_id}/requirement/select")
async def select_requirement_option(session_id: str, body: RequirementOptionSelect) -> dict[str, Any]:
    """在三套需求方案中切换当前选中项。"""
    orch = WorkflowOrchestrator(session_id)
    try:
        state = orch.select_requirement_option(body.index)
        return state.to_public_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/session/{session_id}/styles")
async def set_styles(session_id: str, body: StyleSelection) -> dict[str, Any]:
    cfg = load_yaml_config()
    style_map = {s["id"]: s["name"] for s in cfg.get("ui_styles", [])}
    colors = [c.strip() for c in body.colors.replace(";", "；").split("；") if c.strip()]

    selected: list[dict[str, str]] = []
    for i, sid in enumerate(body.style_ids):
        if sid not in style_map:
            raise HTTPException(status_code=400, detail=f"未知风格: {sid}")
        selected.append(
            {
                "id": sid,
                "name": style_map[sid],
                "color": colors[i] if i < len(colors) else (colors[0] if colors else ""),
            }
        )

    orch = WorkflowOrchestrator(session_id)
    state = orch.set_styles(selected)
    return state.to_public_dict()


@app.post("/api/session/{session_id}/design")
async def run_design(session_id: str) -> dict[str, Any]:
    orch = WorkflowOrchestrator(session_id)

    async def _run() -> None:
        try:
            await asyncio.to_thread(orch.run_design_step)
            logger.info(f"设计步骤完成: {session_id}")
        except asyncio.CancelledError:
            logger.info(f"设计步骤已取消: {session_id}")
            raise
        except Exception as exc:
            logger.exception(f"设计步骤异常: {session_id}")
            # 更新状态为错误
            state = orch.get_state()
            if cancel_hub.is_cancelled(session_id):
                return
            state.step = WorkflowStep.ERROR
            state.error_step = WorkflowStep.DESIGN
            state.error = f"设计生成异常: {str(exc)}"
            state.append_log(f"❌ 设计生成异常: {exc}")
            orch.save(state)

    _track_session_task(session_id, asyncio.create_task(_run()))
    # 不等待任务完成，立即返回
    return {"ok": True, "message": "设计任务已启动，请订阅 SSE 查看进度"}


@app.post("/api/session/{session_id}/cancel")
async def cancel_session(session_id: str) -> dict[str, Any]:
    """终止当前会话正在进行的设计/代码生成/补充对话。"""
    try:
        StateStore(session_id).load()
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}") from exc

    cancel_hub.request_cancel(session_id)
    await _cancel_session_tasks(session_id)
    orch = WorkflowOrchestrator(session_id)
    state = orch.finalize_cancel()
    return {"ok": True, "message": "已请求终止当前环节", "data": state.to_public_dict()}


@app.post("/api/session/{session_id}/skip-design")
async def skip_design(session_id: str) -> dict[str, Any]:
    """跳过 Stitch 设计，直接进入项目初始化+代码生成。"""
    orch = WorkflowOrchestrator(session_id)
    try:
        state = orch.skip_design()
        return state.to_public_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/session/{session_id}/events")
async def session_events(session_id: str) -> StreamingResponse:
    async def event_stream():
        last_len = 0
        last_followup_len = 0
        last_running = (False, False, False, False)
        last_step = None
        while True:
            orch = WorkflowOrchestrator(session_id)
            state = orch.get_state()
            payload = state.to_public_dict()
            running = (
                bool(state.codegen_running),
                bool(state.followup_running),
                bool(getattr(state, "design_running", False)),
                bool(getattr(state, "analyze_running", False)),
            )
            followup_len = len(state.followup_messages or [])

            # 仅在日志/运行状态/步骤实际变化时推送，避免等待用户步骤反复刷状态
            step_value = state.step.value if hasattr(state.step, "value") else str(state.step)
            changed = (
                len(state.logs) != last_len
                or followup_len != last_followup_len
                or running != last_running
                or step_value != last_step
            )
            if changed:
                last_step = step_value
                last_len = len(state.logs)
                last_followup_len = followup_len
                last_running = running
                for chunk in iter_sse({"type": "state", "data": payload}):
                    yield chunk

            # 设计/生成/分析/补充对话进行中时保持 SSE，方便查看进度
            if any(running):
                await asyncio.sleep(0.8)
                continue

            # 停止流：完成、错误或需要用户操作时（客户端会主动关闭，避免 EventSource 自动重连）
            if state.step in (
                WorkflowStep.DONE,
                WorkflowStep.ERROR,
                WorkflowStep.EVALUATE,
                WorkflowStep.SELECT_PLAN,
                WorkflowStep.SELECT_STYLES,
                WorkflowStep.REVIEW_REQUIREMENT,
            ):
                break

            await asyncio.sleep(1.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/session/{session_id}/select-plan")
async def select_plan(session_id: str, body: PlanSelection) -> dict[str, Any]:
    orch = WorkflowOrchestrator(session_id)
    try:
        state = orch.select_plan(body.task_index, body.framework, body.codegen_tool)
        return state.to_public_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class GenerateCodeRequest(BaseModel):
    codegen_tool: str = "claude"  # 默认 Claude Code


@app.post("/api/session/{session_id}/generate-code")
async def generate_code(session_id: str, body: GenerateCodeRequest) -> dict[str, Any]:
    tool = (body.codegen_tool or "claude").strip().lower()
    if tool not in ("codex", "claude", "cursor"):
        raise HTTPException(status_code=400, detail=f"不支持的代码生成工具: {tool}")

    orch = WorkflowOrchestrator(session_id)
    state = orch.get_state()
    if state.codegen_running or state.followup_running:
        return {
            "ok": True,
            "message": "代码生成已在进行中",
            "data": state.to_public_dict(),
        }

    # 先同步标记 running 并落盘，避免前端轮询在后台线程启动前误判为「已结束」
    state.codegen_tool = tool
    state.codegen_running = True
    state.codegen_started_at = time.time()
    state.error = ""
    state.error_step = ""
    if state.step in (WorkflowStep.SELECT_PLAN, WorkflowStep.ERROR, WorkflowStep.EVALUATE):
        state.step = WorkflowStep.INIT_PROJECT
    orch.save(state)

    async def _run() -> None:
        try:
            await asyncio.to_thread(orch.run_init_and_codegen, tool)
        except asyncio.CancelledError:
            logger.info(f"代码生成已取消: {session_id}")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("代码生成失败: %s", exc)
            s = orch.get_state()
            s.codegen_running = False
            s.codegen_started_at = 0.0
            if s.step != WorkflowStep.ERROR:
                s.step = WorkflowStep.ERROR
                s.error_step = WorkflowStep.CODEGEN
                s.error = str(exc)
                s.append_log(f"❌ 代码生成异常：{exc}")
            orch.save(s)

    _track_session_task(session_id, asyncio.create_task(_run()))
    return {
        "ok": True,
        "message": f"代码生成已启动（工具：{tool}）",
        "data": state.to_public_dict(),
    }


class FollowupRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    codegen_tool: str = ""


@app.post("/api/session/{session_id}/followup")
async def followup_chat(session_id: str, body: FollowupRequest) -> dict[str, Any]:
    """代码生成后的补充对话：修复 bug / 追加需求。"""
    orch = WorkflowOrchestrator(session_id)
    try:
        state = orch.enqueue_followup(body.message, body.codegen_tool)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def _run() -> None:
        try:
            await asyncio.to_thread(orch.run_followup)
        except asyncio.CancelledError:
            logger.info(f"补充对话已取消: {session_id}")
            raise

    _track_session_task(session_id, asyncio.create_task(_run()))
    return {"ok": True, "message": "补充指令已提交", "data": state.to_public_dict()}


class FeedbackRequest(BaseModel):
    rating: str = ""   # good | ok | bad
    score: int = 0     # 1-5
    comment: str = ""


@app.post("/api/session/{session_id}/evaluate")
async def run_evaluate(session_id: str) -> dict[str, Any]:
    """第 8 步：进入整理测试发布。"""
    orch = WorkflowOrchestrator(session_id)
    try:
        state = orch.evaluate()
        return state.to_public_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/session/{session_id}/prepare-readme")
async def prepare_readme(session_id: str) -> dict[str, Any]:
    """整理：生成 README.md 功能清单。"""
    orch = WorkflowOrchestrator(session_id)
    try:
        state = orch.prepare_readme()
        return state.to_public_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/session/{session_id}/open-in-cursor")
async def open_in_cursor(session_id: str) -> dict[str, Any]:
    """用 Cursor IDE 在新窗口打开当前项目目录。"""
    from pathlib import Path

    from app.ide import open_project_in_cursor

    orch = WorkflowOrchestrator(session_id)
    state = orch.get_state()
    project_path = (state.project_path or "").strip()
    if not project_path:
        raise HTTPException(status_code=400, detail="项目尚未生成，请先完成代码生成")
    path = Path(project_path).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"项目目录不存在: {path}")
    try:
        opened = await asyncio.to_thread(open_project_in_cursor, str(path), new_window=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("open in cursor failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "message": f"已在 Cursor 新窗口打开：{opened}", "path": opened}


@app.post("/api/session/{session_id}/run-h5-test")
async def run_h5_test(session_id: str) -> dict[str, Any]:
    """测试：HBuilderX 编译运行到 Chrome，返回测试端口地址。"""
    orch = WorkflowOrchestrator(session_id)

    async def _run() -> None:
        try:
            await asyncio.to_thread(orch.run_h5_test)
        except Exception as exc:  # noqa: BLE001
            logger.exception("H5 测试失败: %s", exc)
            state = orch.get_state()
            state.h5_test_running = False
            state.h5_test_status = "failed"
            state.h5_test_message = str(exc)
            state.append_log(f"❌ 测试异常：{exc}")
            orch.save(state)

    # 先快速标记 running，便于前端轮询
    state = orch.get_state()
    if not state.project_path:
        raise HTTPException(status_code=400, detail="项目尚未生成，请先完成代码生成")
    if state.h5_test_running:
        return {"ok": True, "message": "测试进行中", "data": state.to_public_dict()}

    state.h5_test_running = True
    state.h5_test_status = "running"
    state.h5_test_message = "正在通过 HBuilderX 编译并打开 Chrome…"
    orch.save(state)
    _track_session_task(session_id, asyncio.create_task(_run()))
    return {"ok": True, "message": "已开始 H5 测试", "data": state.to_public_dict()}


@app.post("/api/session/{session_id}/stop-h5-test")
async def stop_h5_test(session_id: str) -> dict[str, Any]:
    """关闭 H5 测试进程，释放测试端口。"""
    orch = WorkflowOrchestrator(session_id)
    # 若仍在编译中，取消后台任务；再杀进程
    await _cancel_session_tasks(session_id)
    try:
        state = await asyncio.to_thread(orch.stop_h5_test)
    except Exception as exc:
        logger.exception("stop h5 test failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "message": state.h5_test_message or "已关闭测试端口", "data": state.to_public_dict()}


class PublishRequest(BaseModel):
    repo_path: str = ""
    branch: str = ""


@app.get("/api/session/{session_id}/publish-defaults")
async def publish_defaults(session_id: str) -> dict[str, Any]:
    """发布表单默认路径与分支名。"""
    from app.workflow.orchestrator import default_publish_options

    orch = WorkflowOrchestrator(session_id)
    state = orch.get_state()
    return default_publish_options(state)


@app.post("/api/session/{session_id}/publish")
async def publish_project(session_id: str, body: PublishRequest) -> dict[str, Any]:
    """发布：拉取最新仓库、新建分支、拷贝项目并推送。"""
    orch = WorkflowOrchestrator(session_id)
    repo_path = (body.repo_path or "").strip()
    branch = (body.branch or "").strip()

    async def _run() -> None:
        try:
            await asyncio.to_thread(orch.run_publish, repo_path, branch)
        except Exception as exc:  # noqa: BLE001
            logger.exception("发布失败: %s", exc)
            state = orch.get_state()
            state.release_running = False
            state.release_status = "failed"
            state.release_message = str(exc)
            state.append_log(f"❌ 发布异常：{exc}")
            orch.save(state)

    state = orch.get_state()
    if not state.project_path:
        raise HTTPException(status_code=400, detail="项目尚未生成，请先完成代码生成")
    if state.release_running:
        return {"ok": True, "message": "发布进行中", "data": state.to_public_dict()}

    state.release_running = True
    state.release_status = "running"
    state.release_repo_path = repo_path
    state.release_branch = branch
    state.release_message = "正在拉取仓库并发布…"
    orch.save(state)
    _track_session_task(session_id, asyncio.create_task(_run()))
    return {"ok": True, "message": "已开始发布", "data": state.to_public_dict()}


@app.post("/api/session/{session_id}/complete-workflow")
async def complete_workflow(session_id: str) -> dict[str, Any]:
    """整理、测试、发布均完成后结束工作流。"""
    orch = WorkflowOrchestrator(session_id)
    try:
        state = orch.complete_workflow(force=False)
        return state.to_public_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/session/{session_id}/feedback")
async def submit_feedback(session_id: str, body: FeedbackRequest) -> dict[str, Any]:
    """兼容旧反馈接口。"""
    orch = WorkflowOrchestrator(session_id)
    try:
        state = orch.submit_feedback(body.rating, body.score, body.comment)
        return state.to_public_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/feedback/stats")
async def feedback_stats() -> dict[str, Any]:
    """全局反馈统计。"""
    from app.workflow.quality import load_feedback_db, get_improvement_hints
    db = load_feedback_db()
    sessions = db.get("sessions", [])
    total = db.get("total_score", 0)
    count = db.get("count", 1)
    return {
        "total_sessions": len(sessions),
        "avg_score": round(total / max(count, 1), 1),
        "hints": get_improvement_hints(),
        "recent": sessions[-5:],
    }


@app.get("/api/health")
async def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "root": str(ROOT_DIR),
        "stitch_configured": bool(settings.stitch_api_key),
        "llm_configured": bool(settings.llm_api_key),
    }
