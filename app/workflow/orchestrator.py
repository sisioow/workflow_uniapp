from __future__ import annotations

import hashlib
import json
import logging
import re
import select
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from app.config import SESSIONS_DIR, get_settings
from app.llm.client import LlmClient
from app.models import (
    FollowupMessage,
    QualityReport,
    RequirementPlan,
    StyleTask,
    UserFeedback,
    WorkflowState,
    WorkflowStep,
)
from app.stitch.mcp_client import StitchMcpClient, StitchMcpError
from app.workflow.cancel import WorkflowCancelled, cancel_hub
from app.workflow.state import StateStore
from app.workflow.quality import record_error, record_feedback

logger = logging.getLogger(__name__)

STITCH_TAB_NAMES = ("tab1", "tab2", "tab3", "tab4")


def session_stitch_dir(session_id: str) -> Path:
    return SESSIONS_DIR / session_id / "stitch"


def project_stitch_dir(project_path: str | Path) -> Path:
    return Path(project_path) / "static" / "stitch"


def clear_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def download_stitch_tabs_for_task(
    state: WorkflowState,
    task: StyleTask,
) -> list[str]:
    """将所选方案 4 屏的 PNG + HTML 下载为 tab1~tab4，落到会话目录 stitch/。

    返回已保存的文件名列表（如 tab1.png、tab1.html）。
    """
    if getattr(task, "skip_design", False) or not task.project_id or not task.screen_ids:
        state.append_log("跳过 Stitch 资源下载（无设计稿）")
        return []

    out_dir = session_stitch_dir(state.session_id)
    clear_dir(out_dir)
    stitch = StitchMcpClient()
    stitch.initialize()

    saved: list[str] = []
    state.append_log(f"下载 Stitch 设计图与 HTML 至：{out_dir}")
    for i, screen_id in enumerate(task.screen_ids[:4]):
        tab = STITCH_TAB_NAMES[i] if i < len(STITCH_TAB_NAMES) else f"tab{i + 1}"
        png_dest = out_dir / f"{tab}.png"
        html_dest = out_dir / f"{tab}.html"
        state.append_log(f"  下载 {tab}.png / {tab}.html <- screen {screen_id}")
        png_path, html_path = stitch.download_screen_assets(
            task.project_id, screen_id, png_dest, html_dest
        )
        saved.extend([png_path.name, html_path.name])
        state.append_log(
            f"  ✅ {png_path.name} ({png_path.stat().st_size} B), "
            f"{html_path.name} ({html_path.stat().st_size} B)"
        )

    n = min(4, len(task.screen_ids))
    if n < 4:
        state.append_log(f"⚠️ 仅下载 {n}/4 屏设计资源（screen_ids 不足）")
    else:
        state.append_log("✅ Stitch tab1~tab4 的 png/html 已下载到会话 stitch 目录")
    return saved


def copy_session_stitch_to_project(state: WorkflowState, project_path: str | Path) -> None:
    """将会话中的 tab*.png / tab*.html 拷贝到项目 static/stitch（先清空目标目录）。"""
    src = session_stitch_dir(state.session_id)
    dest = project_stitch_dir(project_path)
    clear_dir(dest)
    if not src.exists():
        state.append_log("⚠️ 会话 stitch 目录不存在，未拷贝设计资源")
        return
    files = sorted(src.glob("tab*.*"))
    if not files:
        state.append_log("⚠️ 会话 stitch 目录无 tab 文件，未拷贝设计资源")
        return
    for f in files:
        shutil.copy2(f, dest / f.name)
        state.append_log(f"  已拷贝 -> static/stitch/{f.name}")
    state.append_log(f"✅ 设计资源已就绪：{dest}")


def _persist_state(state: WorkflowState) -> None:
    """流式输出时即时落盘，供 SSE/轮询读取进度。"""
    try:
        StateStore(state.session_id).save(state)
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist state failed: %s", exc)


def chinese_style_label(style_name: str, style_id: str = "") -> str:
    """提取用于 Stitch 项目命名的中文风格名。

    兼容历史格式，例如：
    - Alexandria (亚历山大 / 经典阅读)｜stich → 亚历山大
    - 柔化粗野主义（Soft Brutalism）→ 柔化粗野主义
    """
    name = (style_name or "").strip()
    if not name:
        return style_id or "未命名风格"

    # 去掉后缀标记
    name = re.split(r"[｜|]", name, maxsplit=1)[0].strip()

    # 优先取括号内中文：Alexandria (亚历山大 / 经典阅读)
    paren = re.search(r"[（(]([^）)]+)[）)]", name)
    if paren:
        inner = paren.group(1).strip()
        parts = [p.strip() for p in re.split(r"[/／]", inner) if p.strip()]
        chinese_parts = [p for p in parts if re.search(r"[\u4e00-\u9fff]", p)]
        if chinese_parts:
            return chinese_parts[0]
        if re.search(r"[\u4e00-\u9fff]", inner):
            return inner

    # 去掉括号中的英文注释
    cleaned = re.sub(r"[（(][^）)]*[）)]", "", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if re.search(r"[\u4e00-\u9fff]", cleaned):
        return cleaned

    # 整段已是中文名（配置里直接给中文）
    if re.search(r"[\u4e00-\u9fff]", name):
        return name

    return cleaned or name or style_id or "未命名风格"


def format_claude_stream_line(raw: str) -> str | None:
    """将 Claude --output-format stream-json 行转为可读进度。"""
    line = (raw or "").strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return f"claude: {line[:240]}"

    event_type = data.get("type") or ""
    subtype = data.get("subtype") or ""

    if event_type == "system":
        return f"claude: 初始化 {subtype or 'system'}".strip()

    if event_type == "assistant":
        message = data.get("message") or {}
        content = message.get("content") or []
        parts: list[str] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = (block.get("text") or "").strip().replace("\n", " ")
                    if text:
                        parts.append(text[:180])
                elif btype == "tool_use":
                    name = block.get("name") or "tool"
                    tool_input = block.get("input") or {}
                    detail = ""
                    if isinstance(tool_input, dict):
                        detail = (
                            tool_input.get("file_path")
                            or tool_input.get("path")
                            or tool_input.get("command")
                            or ""
                        )
                    parts.append(f"🔧 {name}" + (f" → {detail}" if detail else ""))
        if parts:
            return "claude: " + " | ".join(parts)
        return None

    if event_type == "user":
        # tool_result 回传，精简显示
        message = data.get("message") or {}
        content = message.get("content") or []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    is_err = block.get("is_error")
                    return f"claude: {'❌ 工具失败' if is_err else '✅ 工具完成'}"
        return None

    if event_type == "result":
        cost = data.get("total_cost_usd")
        suffix = f" · ${cost:.4f}" if isinstance(cost, (int, float)) else ""
        return f"claude: 任务结束 ({subtype or 'result'}){suffix}"

    if event_type in {"content_block_delta", "message_delta", "message_start", "message_stop"}:
        return None

    # 其它事件给短提示，避免刷屏
    return f"claude: [{event_type}{('/' + subtype) if subtype else ''}]"


def format_elapsed(seconds: float) -> str:
    """将秒数格式化为可读耗时。"""
    sec = max(0, int(round(seconds)))
    if sec < 60:
        return f"{sec} 秒"
    minutes, rem = divmod(sec, 60)
    if minutes < 60:
        return f"{minutes} 分 {rem} 秒" if rem else f"{minutes} 分"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"


CODEGEN_TOOL_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor Agent",
}


class StreamCmdResult:
    """CLI 流式执行结果（含 Claude stream-json 终态解析）。"""

    __slots__ = (
        "returncode",
        "stdout",
        "result_seen",
        "result_ok",
        "result_is_error",
        "result_subtype",
        "result_text",
        "killed_after_idle",
        "api_error_seen",
    )

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        *,
        result_seen: bool = False,
        result_ok: bool = False,
        result_is_error: bool = False,
        result_subtype: str = "",
        result_text: str = "",
        killed_after_idle: bool = False,
        api_error_seen: bool = False,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.result_seen = result_seen
        self.result_ok = result_ok
        self.result_is_error = result_is_error
        self.result_subtype = result_subtype
        self.result_text = result_text
        self.killed_after_idle = killed_after_idle
        self.api_error_seen = api_error_seen


def _parse_claude_stream_result(raw_line: str) -> dict[str, Any] | None:
    """解析 Claude stream-json 的最终 result 事件；非 result 返回 None。"""
    line = (raw_line or "").strip()
    if not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("type") == "result":
        return data
    return None


def _assistant_text_has_api_error(raw_line: str) -> bool:
    line = (raw_line or "").strip()
    if not line.startswith("{"):
        return "API Error:" in line
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return "API Error:" in line
    if not isinstance(data, dict):
        return False
    if data.get("type") != "assistant":
        return "API Error:" in line
    message = data.get("message") or {}
    content = message.get("content") or []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text") or ""
                if "API Error:" in text or "invalid_request_error" in text:
                    return True
    return False


def _touch_codegen_heartbeat(state: WorkflowState, started_at: float) -> None:
    """quiet 模式下刷新「正在生成」进度行，避免 UI 一直像卡住。"""
    label = CODEGEN_TOOL_LABELS.get(
        (getattr(state, "codegen_tool", None) or "claude").strip().lower(),
        "代码生成",
    )
    msg = f"{label} 正在生成代码中…（已进行 {format_elapsed(time.time() - started_at)}）"
    progress = state.codegen_progress
    replaced = False
    for i in range(len(progress) - 1, -1, -1):
        if "正在生成代码中" in progress[i]:
            progress[i] = msg
            replaced = True
            break
    if not replaced:
        progress.append(msg)
    _persist_state(state)


def stream_command_to_logs(
    state: WorkflowState,
    cmd: list[str],
    *,
    cwd: str | None = None,
    input_text: str | None = None,
    line_formatter: Callable[[str], str | None] | None = None,
    timeout: int = 3600,
    quiet: bool = False,
    post_result_idle_sec: float = 90.0,
    env: dict[str, str] | None = None,
) -> StreamCmdResult:
    """边执行 CLI 边写日志并落盘。

    quiet=True：不刷终端全文，但会心跳更新实施进度。
    若检测到 Claude stream-json 的成功 result 后长时间无新输出，主动结束卡住的 CLI。
    """
    if not quiet:
        state.append_log(f"$ {' '.join(cmd[:8])}{' …' if len(cmd) > 8 else ''}")
        _persist_state(state)

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    cancel_hub.register_proc(state.session_id, proc)

    chunks: list[str] = []
    last_flush = 0.0
    pending_persist = False
    deadline = time.time() + timeout
    started_at = time.time()
    last_output_at = started_at
    last_heartbeat_at = 0.0
    # 耗时展示以前端按秒刷新为主；后端仅低频落盘，供刷新页面/重连后恢复进度
    heartbeat_interval = 30.0
    result_seen = False
    result_ok = False
    result_is_error = False
    result_subtype = ""
    result_text = ""
    killed_after_idle = False
    api_error_seen = False

    try:
        if input_text is not None and proc.stdin:
            proc.stdin.write(input_text)
            proc.stdin.close()

        assert proc.stdout is not None
        # 启动写一次，避免刷新页面时看不到「正在生成」
        if quiet:
            _touch_codegen_heartbeat(state, started_at)
            last_heartbeat_at = started_at

        # 用非阻塞轮询：否则 Claude 在 result 之后挂起时，for-readline 会永远卡住，
        # finally 无法清理 codegen_running，UI 一直显示「生成中」。
        while True:
            cancel_hub.check(state.session_id)
            now = time.time()
            if now > deadline:
                proc.kill()
                raise subprocess.TimeoutExpired(cmd, timeout)

            if quiet and (now - last_heartbeat_at) >= heartbeat_interval:
                _touch_codegen_heartbeat(state, started_at)
                last_heartbeat_at = now

            # 仅在成功 result 后才因空闲结束；失败 result 应保留真实退出码
            if result_ok and (now - last_output_at) >= post_result_idle_sec:
                state.append_log(
                    f"⚠️ CLI 在完成信号后 {int(post_result_idle_sec)}s 无新输出，结束卡住的进程"
                )
                _persist_state(state)
                killed_after_idle = True
                if proc.poll() is None:
                    proc.kill()
                    try:
                        proc.wait(timeout=15)
                    except Exception:  # noqa: BLE001
                        pass
                break

            ready, _, _ = select.select([proc.stdout], [], [], 2.0)
            if not ready:
                if proc.poll() is not None:
                    # 排空残留缓冲
                    while True:
                        raw_line = proc.stdout.readline()
                        if not raw_line:
                            break
                        chunks.append(raw_line)
                        parsed = _parse_claude_stream_result(raw_line)
                        if parsed is not None:
                            result_seen = True
                            result_is_error = bool(parsed.get("is_error"))
                            result_subtype = str(parsed.get("subtype") or "")
                            result_text = str(parsed.get("result") or "")[:800]
                            result_ok = (not result_is_error) and result_subtype in (
                                "",
                                "success",
                            )
                        if _assistant_text_has_api_error(raw_line):
                            api_error_seen = True
                    break
                continue

            raw_line = proc.stdout.readline()
            if not raw_line:
                if proc.poll() is not None:
                    break
                continue

            last_output_at = time.time()
            chunks.append(raw_line)
            parsed = _parse_claude_stream_result(raw_line)
            if parsed is not None:
                result_seen = True
                result_is_error = bool(parsed.get("is_error"))
                result_subtype = str(parsed.get("subtype") or "")
                result_text = str(parsed.get("result") or "")[:800]
                result_ok = (not result_is_error) and result_subtype in ("", "success")
            if _assistant_text_has_api_error(raw_line):
                api_error_seen = True

            if not quiet:
                display = line_formatter(raw_line) if line_formatter else raw_line.rstrip()
                if display:
                    state.append_log(display[:500])
                    pending_persist = True

            now = time.time()
            if pending_persist and (now - last_flush) >= 0.6:
                _persist_state(state)
                last_flush = now
                pending_persist = False

        returncode = proc.poll()
        if returncode is None:
            returncode = proc.wait(timeout=max(1, deadline - time.time()))
        # 仅当我们因「成功 result 后空闲挂起」主动 kill 时，才把信号退出码视为成功
        if killed_after_idle and result_ok and returncode not in (0, None):
            returncode = 0
        cancel_hub.check(state.session_id)
    except WorkflowCancelled:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
        state.append_log("⏹ 命令已按用户请求终止")
        _persist_state(state)
        raise
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        state.append_log("❌ 命令超时，已终止")
        _persist_state(state)
        raise
    finally:
        cancel_hub.unregister_proc(state.session_id, proc)
        if pending_persist:
            _persist_state(state)

    output = "".join(chunks)
    # 二次扫描：防止排空缓冲时漏解析
    if not result_seen:
        for raw_line in chunks:
            parsed = _parse_claude_stream_result(raw_line)
            if parsed is not None:
                result_seen = True
                result_is_error = bool(parsed.get("is_error"))
                result_subtype = str(parsed.get("subtype") or "")
                result_text = str(parsed.get("result") or "")[:800]
                result_ok = (not result_is_error) and result_subtype in ("", "success")
            if _assistant_text_has_api_error(raw_line):
                api_error_seen = True

    code = int(returncode or 0)
    if result_is_error or api_error_seen:
        # 确保失败不会被当成退出码 0
        if code == 0:
            code = 1
    return StreamCmdResult(
        code,
        output,
        result_seen=result_seen,
        result_ok=result_ok,
        result_is_error=result_is_error,
        result_subtype=result_subtype,
        result_text=result_text,
        killed_after_idle=killed_after_idle,
        api_error_seen=api_error_seen,
    )


def run_analyze(
    state: WorkflowState,
    extra_prompt: str = "",
    on_progress: Callable[[WorkflowState], None] | None = None,
) -> WorkflowState:
    state.analyze_running = True
    state.error = ""
    state.error_step = ""
    state.append_log(f"开始需求分析：{state.app_name}")
    hint = (extra_prompt or "").strip()
    if hint:
        state.append_log(f"用户补充提示词：{hint[:200]}{'…' if len(hint) > 200 else ''}")
    model_label = state.llm_model or "默认"
    state.append_log(f"⏳ 正在调用模型 {model_label} 生成三套方案，请稍候…")
    if on_progress:
        on_progress(state)

    llm = LlmClient(model=state.llm_model or None)
    options = llm.analyze_requirement(state.app_name, extra_prompt=hint)
    state.requirement_options = options
    state.selected_requirement_index = 0
    state.requirement = options[0] if options else None
    state.analyze_running = False
    state.error = ""
    state.error_step = ""
    state.step = WorkflowStep.REVIEW_REQUIREMENT
    dirs = "、".join((o.direction or f"方案{i+1}") for i, o in enumerate(options))
    state.append_log(f"需求分析完成，生成 {len(options)} 套功能方向：{dirs}")
    state.append_log("请选择一套方案查看、修改后确认")
    return state


def select_requirement_option(state: WorkflowState, index: int) -> WorkflowState:
    """切换当前审核中的需求方案（三选一）。"""
    if not state.requirement_options:
        raise ValueError("没有可选的需求方案")
    if index < 0 or index >= len(state.requirement_options):
        raise ValueError("无效的方案序号")
    # 将当前 requirement 写回 options，避免切换丢失未保存编辑
    if state.requirement is not None and 0 <= state.selected_requirement_index < len(state.requirement_options):
        state.requirement_options[state.selected_requirement_index] = state.requirement
    state.selected_requirement_index = index
    state.requirement = state.requirement_options[index]
    label = state.requirement.direction or f"方案{index + 1}"
    state.append_log(f"已切换到需求方案 #{index + 1}：{label}")
    return state


def confirm_requirement(state: WorkflowState) -> WorkflowState:
    """用户确认需求方案后，进入 UI 风格选择步骤。"""
    if not state.requirement:
        raise ValueError("缺少需求方案，请先完成需求分析")
    # 同步所选方案回 options
    idx = state.selected_requirement_index
    if state.requirement_options and 0 <= idx < len(state.requirement_options):
        state.requirement_options[idx] = state.requirement
    label = state.requirement.direction or f"方案{idx + 1}"
    state.step = WorkflowStep.SELECT_STYLES
    state.append_log(f"需求方案已确认（{label}），请选择 UI 风格")
    return state


def update_requirement(state: WorkflowState, updates: dict[str, Any]) -> WorkflowState:
    """
    用户修改需求方案。

    支持修改：
    - app_name: 应用名称
    - summary: 产品定位
    - direction: 功能方向名称
    - pages: 页面列表（包括标题、功能点、交互说明）
    - selected_index: 切换三套方案之一
    """
    if not state.requirement and not state.requirement_options:
        raise ValueError("缺少需求方案，请先完成需求分析")

    if state.step not in (WorkflowStep.REVIEW_REQUIREMENT, WorkflowStep.SELECT_STYLES):
        raise ValueError("当前状态不允许修改需求方案，设计已开始")

    if "selected_index" in updates and updates["selected_index"] is not None:
        state = select_requirement_option(state, int(updates["selected_index"]))

    if not state.requirement:
        raise ValueError("缺少需求方案，请先完成需求分析")

    # 更新应用名称
    if "app_name" in updates and updates["app_name"]:
        state.app_name = updates["app_name"].strip()
        state.requirement.app_name = state.app_name
        state.append_log(f"应用名称已更新：{state.app_name}")

    if "direction" in updates and updates["direction"] is not None:
        state.requirement.direction = str(updates["direction"]).strip()
        state.append_log("功能方向名称已更新")

    # 更新产品定位
    if "summary" in updates and updates["summary"]:
        state.requirement.summary = updates["summary"].strip()
        state.append_log("产品定位已更新")

    # 更新页面
    if "pages" in updates and updates["pages"]:
        for page_update in updates["pages"]:
            page_key = page_update.get("key")
            page = next((p for p in state.requirement.pages if p.key == page_key), None)
            if page:
                if "title" in page_update and page_update["title"]:
                    page.title = page_update["title"].strip()
                if "features" in page_update:
                    page.features = [f.strip() for f in page_update["features"] if f.strip()]
                if "description" in page_update:
                    page.description = page_update["description"].strip()
                state.append_log(f"页面 {page_key} 已更新")

    # 写回 options 列表
    idx = state.selected_requirement_index
    if state.requirement_options and 0 <= idx < len(state.requirement_options):
        state.requirement_options[idx] = state.requirement

    return state


# 部分风格名在 Stitch/Gemini 侧偶发触发 invalid argument；附英文释义降低歧义
_STYLE_PROMPT_HINTS: dict[str, str] = {
    "柔化粗野主义": (
        "Soft Brutalism：大色块与清晰分区，但圆角更柔和、阴影更轻、配色更友好，避免生硬压迫感"
    ),
    "soft-brutalism": (
        "Soft Brutalism：大色块与清晰分区，但圆角更柔和、阴影更轻、配色更友好，避免生硬压迫感"
    ),
}


def build_design_prompt(
    requirement: RequirementPlan,
    style_name: str,
    color_hint: str,
    *,
    style_id: str = "",
) -> str:
    """构建 Stitch 一次性生成 4 页的设计提示词。"""
    color_line = f"；主色调：{color_hint}" if color_hint else ""
    style_hint = (
        _STYLE_PROMPT_HINTS.get((style_name or "").strip())
        or _STYLE_PROMPT_HINTS.get((style_id or "").strip().lower())
        or ""
    )
    page_keys = ["page1", "page2", "page3", "page4"]
    page_blocks: list[str] = []
    for i, key in enumerate(page_keys):
        page = next((p for p in requirement.pages if p.key == key), None)
        if page is None and i < len(requirement.pages):
            page = requirement.pages[i]
        title = page.title if page else key
        features = "、".join(page.features) if page and page.features else "核心工具功能"
        desc = (
            page.description
            if page and page.description
            else "布局优美、强调可操作的交互组件"
        )
        page_blocks.extend(
            [
                f"{i + 1}. {key}「{title}」",
                f"   功能点：{features}",
                f"   交互说明：{desc}",
            ]
        )

    prompt_parts = [
        "【生成要求】",
        "请一次性生成恰好 4 个独立移动端页面设计（page1~page4），不要只生成单个页面；一次请求完成全部 4 屏。",
        "每个页面一张完整设计稿，视觉与交互风格高度统一，适合底部 Tab 切换的 App。",
        "",
        "【产品方案】",
        f"产品定位：{requirement.summary}",
        "页面清单：",
        *page_blocks,
        "",
        "【UI 风格要求】",
        f"风格主题：{style_name}{color_line}",
    ]
    if style_hint:
        prompt_parts.append(f"风格说明：{style_hint}")
    prompt_parts.extend(
        [
            "设备类型：移动端（375px 宽度）",
            "语言：中文",
            "",
            "【设计约束】",
            "- 纯前端可实现",
            "- 无个人中心、登录、注册等通用页面",
            "- 强调交互可操作性",
            f"- 保持 {style_name} 风格高度统一",
            "- 必须产出 4 个页面，顺序对应 page1、page2、page3、page4",
        ]
    )
    return "\n".join(prompt_parts)


def build_screen_prompt(
    requirement: RequirementPlan,
    page_key: str,
    style_name: str,
    color_hint: str,
) -> str:
    """兼容旧调用：现统一走四页批量提示词。"""
    return build_design_prompt(requirement, style_name, color_hint)


def _run_single_style_design(
    *,
    session_id: str,
    app_name: str,
    requirement: RequirementPlan,
    task: StyleTask,
    index: int,
    total: int,
    lock: threading.Lock,
    state: WorkflowState,
) -> None:
    """并行 worker：为一套风格创建 Stitch 项目并一次生成 4 屏。"""

    def _log(msg: str) -> None:
        with lock:
            state.append_log(msg)
            _persist_state(state)

    style_name = task.style_name
    style_id = task.style_id
    color_hint = task.color_hint

    cancel_hub.check(session_id)
    _log(f"\n【{index + 1}/{total}】并行处理风格: {style_name}")

    stitch = StitchMcpClient()
    stitch.initialize()

    try:
        style_label = chinese_style_label(style_name, style_id)
        style_label = re.split(r"[/／]", style_label, maxsplit=1)[0].strip() or style_label
        project_title = f"{app_name}+{style_label}"
        _log(f"  [{style_name}] 创建 Stitch 项目: {project_title}")

        cancel_hub.check(session_id)
        project_id = stitch.create_project(project_title)
        with lock:
            task.project_id = project_id
            _persist_state(state)
        _log(f"  [{style_name}] ✅ 项目创建成功: {project_id}")

        _log(f"  [{style_name}] 一次性生成 page1~page4…")
        prompt = build_design_prompt(
            requirement, style_name, color_hint, style_id=style_id
        )
        cancel_hub.check(session_id)
        # invalid argument 等瞬时错误会在客户端内自动重试；超时仍不重试
        screen_ids = stitch.generate_screens(project_id, prompt, expected_count=4)
        cancel_hub.check(session_id)

        with lock:
            task.screen_ids = list(screen_ids)
            task.status = "done"
            task.error = ""
            _persist_state(state)

        _log(f"  [{style_name}] ✅ 已生成 {len(screen_ids)} 屏: {', '.join(screen_ids)}")
        if len(screen_ids) < 4:
            _log(
                f"  [{style_name}] ⚠️ 期望 4 屏，实际 {len(screen_ids)} 屏，可稍后在 Stitch 项目中核对"
            )
        _log(f"  [{style_name}] ✅ 设计完成")
    except WorkflowCancelled:
        with lock:
            if task.status == "running":
                task.status = "failed"
                task.error = "用户终止"
                _persist_state(state)
        raise
    except StitchMcpError as exc:
        with lock:
            task.status = "failed"
            task.error = str(exc)
            _persist_state(state)
        _log(f"  [{style_name}] ❌ 失败: {exc}")
    except Exception as exc:  # noqa: BLE001
        with lock:
            task.status = "failed"
            task.error = str(exc)
            _persist_state(state)
        _log(f"  [{style_name}] ❌ 异常: {exc}")
        logger.exception("设计生成异常: %s", style_name)


def run_design(state: WorkflowState) -> WorkflowState:
    if not state.requirement:
        raise ValueError("缺少需求方案")

    if not state.selected_styles:
        raise ValueError("未选择 UI 风格")

    state.append_log("【开始设计生成】")
    _persist_state(state)

    stitch = StitchMcpClient()

    # 检查 API Key 配置
    if not stitch.api_key:
        state.append_log("❌ 错误：STITCH_API_KEY 未配置，请在 local_config.yaml 的 stitch.api_key 中设置")
        state.step = WorkflowStep.ERROR
        state.error_step = WorkflowStep.DESIGN
        state.error = "Stitch API 未配置"
        record_error("design", "api_key_missing", state.error)
        return state

    try:
        cancel_hub.check(state.session_id)
        state.append_log("初始化 Stitch MCP...")
        stitch.initialize()
        state.append_log("✅ Stitch MCP 初始化成功")
        _persist_state(state)
    except WorkflowCancelled:
        return _finalize_design_cancel(state)
    except StitchMcpError as exc:
        state.append_log(f"❌ Stitch 初始化失败: {exc}")
        state.step = WorkflowStep.ERROR
        state.error_step = WorkflowStep.DESIGN
        state.error = f"Stitch 初始化失败: {exc}"
        record_error("design", "stitch_init_failed", str(exc))
        return state

    state.style_tasks = [
        StyleTask(
            style_id=item["id"],
            style_name=item["name"],
            color_hint=item.get("color", ""),
            status="running",
        )
        for item in state.selected_styles
    ]
    total = len(state.style_tasks)
    max_workers = min(total, 3)
    state.append_log(f"并行生成 {total} 套设计方案（并发 {max_workers}）…")
    _persist_state(state)

    lock = threading.Lock()
    session_id = state.session_id
    app_name = state.app_name
    requirement = state.requirement

    try:
        cancel_hub.check(session_id)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(
                    _run_single_style_design,
                    session_id=session_id,
                    app_name=app_name,
                    requirement=requirement,
                    task=state.style_tasks[i],
                    index=i,
                    total=total,
                    lock=lock,
                    state=state,
                )
                for i in range(total)
            ]
            try:
                for fut in as_completed(futures):
                    cancel_hub.check(session_id)
                    try:
                        fut.result()
                    except WorkflowCancelled:
                        raise
                    except Exception:  # noqa: BLE001
                        # 任务级错误已在 worker 内写回 status
                        pass
            except WorkflowCancelled:
                for fut in futures:
                    fut.cancel()
                return _finalize_design_cancel(state)
    except WorkflowCancelled:
        return _finalize_design_cancel(state)

    # 统计结果
    done_count = sum(1 for t in state.style_tasks if t.status == "done")
    failed_count = len(state.style_tasks) - done_count

    state.append_log("\n【设计阶段总结】")
    state.append_log(f"成功: {done_count}/{len(state.style_tasks)}")
    state.append_log(f"失败: {failed_count}/{len(state.style_tasks)}")

    if done_count == 0:
        state.step = WorkflowStep.ERROR
        state.error_step = WorkflowStep.DESIGN
        state.error = "所有风格设计均失败，请检查 Stitch API 配置或网络连接"
        state.append_log("❌ 所有设计均失败，无法继续")
        record_error("design", "all_styles_failed", state.error)
    else:
        state.step = WorkflowStep.SELECT_PLAN
        state.append_log("✅ 设计阶段完成，请选择最终方案")

    return state


def _finalize_design_cancel(state: WorkflowState) -> WorkflowState:
    for task in state.style_tasks:
        if task.status == "running":
            task.status = "failed"
            task.error = "用户终止"
    done_count = sum(1 for t in state.style_tasks if t.status == "done")
    state.design_running = False
    state.error = ""
    state.error_step = ""
    if done_count > 0:
        state.step = WorkflowStep.SELECT_PLAN
        state.append_log("⏹ 设计生成已终止（已有部分成功方案可选用）")
    else:
        state.step = WorkflowStep.DESIGN
        state.append_log("⏹ 设计生成已终止，可重新开始")
    _persist_state(state)
    return state


def run_init_project(state: WorkflowState) -> WorkflowState:
    settings = get_settings()
    template = Path(settings.uniapp_template_dir)
    output_root = Path(settings.uniapp_output_dir)
    project_dir = state.project_dir.strip()

    if not project_dir or not project_dir.replace("_", "").replace("-", "").isalnum():
        raise ValueError("非法项目目录名，仅允许字母、数字、下划线、连字符")

    dest = output_root / project_dir
    if dest.exists():
        shutil.rmtree(dest)
    if not template.exists():
        raise FileNotFoundError(f"模板目录不存在：{template}")

    shutil.copytree(template, dest)
    state.project_path = str(dest)
    # 将第 6 步已下载的 Stitch 截图放入项目 static/stitch
    copy_session_stitch_to_project(state, dest)
    state.step = WorkflowStep.CODEGEN
    state.append_log(f"项目已初始化：{dest}")
    return state


def _page_feature_line(req: RequirementPlan, index: int, key: str) -> tuple[str, str, str]:
    """返回 (标题, 功能点拼接, 说明)。"""
    page = req.pages[index] if index < len(req.pages) else None
    title = page.title if page else key
    features = "、".join(page.features) if page and page.features else "核心功能"
    desc = page.description if page and page.description else "布局清晰、交互完整"
    return title, features, desc


def build_codegen_prompt_with_stitch(state: WorkflowState) -> str:
    """有 Stitch 设计稿时的代码生成提示词：按本地 png/html 复现。"""
    task = state.style_tasks[state.selected_task_index]
    req = state.requirement
    assert req is not None
    stitch_dir = "static/stitch"

    lines = [
        "【App 开发任务 · 依据 Stitch 设计稿】",
        f"应用：{state.app_name}",
        f"风格：{task.style_name}",
        f"配色：{task.color_hint or '默认'}",
        "",
        f"设计资源已在项目目录 {stitch_dir}/（勿调用 Stitch/MCP）：",
        "请同时参考 html（结构/样式）与 png（视觉还原）。",
    ]
    for i, key in enumerate(["page1", "page2", "page3", "page4"]):
        title, features, _ = _page_feature_line(req, i, key)
        tab = STITCH_TAB_NAMES[i]
        lines.append(
            f"- {key}「{title}」← {stitch_dir}/{tab}.html + {stitch_dir}/{tab}.png；功能：{features}"
        )
    lines.extend([
        "",
        "要求：",
        f"1. 阅读 {stitch_dir}/tab1~tab4.html 获取结构与样式，并对照同名 png 做视觉还原"
        "（布局、配色、间距、图标与层次）；若图片读取失败，仍须依据 html + 功能清单完成实现。",
        "2. 设计图/HTML 中引用的在线图片、图标等素材（如 http/https 外链、CDN、"
        "emoji 以外的远程资源）必须下载到项目本地（建议放入 static/image/ 或 static/icon/），"
        "并修改代码中的引用路径为本地相对路径；禁止在最终代码里继续依赖外网图片链接。",
        "3. 依据设计稿复现 page1~page4 的功能与 UI，直接改写 /pages 下对应 vue 文件。",
        "4. 登录、注册、引导和「我的」页面只需修改产品文案和主题风格即可，代码逻辑不用改变。「我的」页面作为第5个tab页面",
        "5. 自测可编译通过，功能闭环无漏洞。",
        "6. 请实际修改文件后再结束；不要只做任务拆解或口头计划。",
    ])
    return "\n".join(lines)


def build_codegen_prompt_skip_design(state: WorkflowState) -> str:
    """跳过 Stitch、直接开发时的代码生成提示词：按需求与风格自行设计实现。"""
    task = state.style_tasks[state.selected_task_index]
    req = state.requirement
    assert req is not None

    lines = [
        "【App 开发任务 · 跳过设计稿，自行设计实现】",
        f"应用：{state.app_name}",
        f"风格：{task.style_name}",
        f"配色：{task.color_hint or '默认'}",
        f"产品定位：{req.summary or '—'}",
        "",
        "页面清单（无设计稿，请按风格与需求自行设计并实现）：",
    ]
    for i, key in enumerate(["page1", "page2", "page3", "page4"]):
        title, features, desc = _page_feature_line(req, i, key)
        lines.append(f"- {key}「{title}」— 功能：{features}；说明：{desc}")
    lines.extend([
        "",
        "要求：",
        "1. 按上述页面清单实现 page1~page4 的完整功能与 UI，风格与配色统一。",
        "2. 在 /pages 实现完整交互，布局清晰、可操作。",
        "3. 登录、注册、引导和「我的」页面只需修改产品文案和主题风格即可，代码逻辑不用改变。",
        "4. 自测可编译通过，功能闭环无漏洞。",
    ])
    return "\n".join(lines)


def build_codegen_prompt(state: WorkflowState) -> str:
    """按是否使用 Stitch 设计稿分发到对应提示词。"""
    task = state.style_tasks[state.selected_task_index]
    has_designs = bool(task.project_id and task.screen_ids)
    is_skip_design = getattr(task, "skip_design", False)
    if has_designs and not is_skip_design:
        return build_codegen_prompt_with_stitch(state)
    return build_codegen_prompt_skip_design(state)


def run_codegen(state: WorkflowState) -> WorkflowState:
    settings = get_settings()
    task = state.style_tasks[state.selected_task_index]
    is_stitch = bool(task.project_id and task.screen_ids) and not getattr(
        task, "skip_design", False
    )
    prompt = build_codegen_prompt(state)
    project_path = state.project_path

    if not project_path:
        raise ValueError("项目路径为空，请先初始化项目")

    tool = getattr(state, "codegen_tool", "claude") or "claude"
    label = CODEGEN_TOOL_LABELS.get(tool, tool)
    route = "Stitch 设计稿" if is_stitch else "跳过设计"

    state.reset_codegen_progress()
    state.append_codegen_progress("【提示词】")
    state.append_codegen_progress(prompt)
    state.append_codegen_progress(f"{label} 正在生成代码中…")
    state.append_log(f"启动代码生成（工具：{label}，提示词：{route}）")
    _persist_state(state)

    started = time.time()
    if tool == "codex":
        state = _run_codex(state, settings, project_path, prompt)
    elif tool == "cursor":
        state = _run_cursor(state, settings, project_path, prompt)
    else:
        state = _run_claude(state, settings, project_path, prompt)

    elapsed = format_elapsed(time.time() - started)
    if state.step == WorkflowStep.ERROR:
        done_msg = f"生成失败（耗时 {elapsed}）"
    else:
        done_msg = f"生成完成（耗时 {elapsed}）"
    state.append_codegen_progress(done_msg)
    state.append_log(done_msg)
    _persist_state(state)
    return state


def build_followup_prompt(state: WorkflowState, user_message: str) -> str:
    """构建补充对话提示词：在已有项目上继续改代码。"""
    history_lines: list[str] = []
    for msg in state.followup_messages[-8:]:
        if msg.status == "running":
            continue
        role = "用户" if msg.role == "user" else "助手"
        history_lines.append(f"{role}：{msg.content[:500]}")

    history = "\n".join(history_lines) if history_lines else "（无）"
    app_name = state.app_name or "当前应用"
    return "\n".join(
        [
            "【补充开发任务】",
            f"应用名称：{app_name}",
            f"项目路径：{state.project_path}",
            "这是一个已生成的 uni-app 项目。请在当前仓库内直接修改/新增代码完成用户指令。",
            "不要重新初始化项目；不要删除无关文件；保持现有页面结构与风格统一。",
            "",
            "最近对话：",
            history,
            "",
            "当前用户指令：",
            user_message,
            "",
            "完成后简要说明改了哪些文件与要点。",
        ]
    )


def summarize_tool_output(output: str, limit: int = 2500) -> str:
    text = (output or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…（输出已截断）"


def build_cursor_agent_command(settings: Any, project_path: str, prompt: str) -> list[str]:
    """构造 Cursor Agent 无交互 CLI（headless）命令。

    - `cursor_bin=agent`（或 cursor-agent 路径）→ `agent --print --yolo ...`
    - `cursor_bin=cursor` → `cursor agent --print --yolo ...`

    注意：不可使用已失效的 `cursor composer`；该命令会瞬间退出码 0，导致「0 秒生成完成」。
    """
    raw = (getattr(settings, "cursor_bin", None) or "agent").strip() or "agent"
    name = Path(raw).name.lower()

    if name == "cursor":
        cmd = [raw, "agent"]
    else:
        cmd = [raw]

    cmd.extend(
        [
            "--print",
            "--yolo",
            "--trust",
            "--workspace",
            str(project_path),
        ]
    )
    model = (getattr(settings, "cursor_model", None) or "").strip()
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)
    return cmd


def _page_source_fingerprint(project_path: str | Path) -> dict[str, str]:
    """关键页面哈希，用于验证代码生成是否真正改写了文件。"""
    root = Path(project_path)
    rels = [
        "pages/page1/page1.vue",
        "pages/page2/page2.vue",
        "pages/page3/page3.vue",
        "pages/page4/page4.vue",
        "App.vue",
        "pages.json",
    ]
    out: dict[str, str] = {}
    for rel in rels:
        path = root / rel
        if not path.is_file():
            out[rel] = ""
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        out[rel] = digest
    return out


_CLAUDE_STITCH_SYSTEM = (
    "实现 UI 时须同时参考 static/stitch/ 下的 .html（结构样式）与同名 .png（视觉效果）。"
    "优先按设计稿还原；若 png 读取失败，继续依据 html 与功能清单完成，不要中断任务。"
    "设计稿中使用的在线图片、图标素材须下载到本地并修改引用为本地路径，禁止保留外网图片链接。"
)


def build_claude_codegen_command(settings: Any, prompt: str) -> list[str]:
    """构造 Claude Code headless 命令。

    - `--bare`：跳过 MCP/skills/hooks，避免无关插件导致首轮请求异常退出
    - `--tools` 白名单：只用读写改文件能力，避开 Task/Agent（部分兼容网关会 400）
    - 提示词作为位置参数传入（不用管道 stdin），避免服务进程继承 stdin 干扰
    """
    cmd = [
        settings.claude_bin,
        "--print",
        "--bare",
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
        "--tools",
        "Bash,Edit,Write,Read,Glob,Grep",
        "--append-system-prompt",
        _CLAUDE_STITCH_SYSTEM,
    ]
    if settings.claude_model:
        cmd.extend(["--model", settings.claude_model])
    # `--tools` 是可变参数，需用 `--` 结束选项，否则提示词会被当成工具名吃掉
    cmd.append("--")
    cmd.append(prompt)
    return cmd


def _claude_subprocess_env() -> dict[str, str]:
    """Claude 走第三方网关时关闭 tool_search，避免 tool_reference / 多模态 400。"""
    import os

    env = os.environ.copy()
    env.setdefault("ENABLE_TOOL_SEARCH", "false")
    env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    return env


def _summarize_claude_failure_text(raw: str) -> str:
    """把 Claude 失败输出收敛成人能读的短句，避免把 system init JSON 整段抛到前端。"""
    text = (raw or "").strip()
    if not text:
        return "Claude 异常退出，未完成代码生成"
    lowered = text.lower()
    if "multimodalitem" in lowered or (
        "invalidparameter" in lowered and ("image" in lowered or "valid string" in lowered)
    ):
        return (
            "兼容 API 网关拒绝了含图片/多模态的请求（读取 png 时偶发）。"
            "请重试；若反复失败可改用 Codex 或 Cursor。"
        )
    if "api error:" in lowered or "invalid_request_error" in lowered:
        # 截取 API Error 行
        for line in text.splitlines():
            if "API Error:" in line or "invalid_request_error" in line:
                brief = line.strip()[:500]
                if "MultiModal" in brief or "InvalidParameter" in brief:
                    return (
                        "兼容 API 网关参数校验失败（多为多模态/工具格式不兼容）。"
                        "请重试；若反复失败请改用 Codex 或 Cursor。"
                    )
                return brief
        return text[-500:]
    # system/init 元数据（slash_commands / claude_code_version 等）对排障无处不大
    if any(
        marker in text
        for marker in (
            "claude_code_version",
            "apiKeySource",
            "slash_commands",
            '"type":"system"',
            '"subtype":"init"',
            "run-skill-generator",
            "team-onboarding",
        )
    ):
        return (
            "Claude 异常提前退出（仅输出了初始化信息，未真正开始改代码）。"
            "已切换为 bare/工具白名单模式，请重试；若仍失败可改用 Codex 或 Cursor。"
        )
    if "Input must be provided either through stdin or as a prompt argument" in text:
        return "Claude 未收到提示词（stdin/参数为空），请重试"
    # 普通截断
    return text[-500:] if len(text) > 500 else text


def _claude_stream_failed(stream: StreamCmdResult) -> str:
    """根据 stream-json 终态判断失败原因；成功返回空字符串。"""
    if stream.result_is_error or stream.api_error_seen:
        detail = (stream.result_text or "").strip()
        if not detail and stream.api_error_seen:
            detail = "检测到 API Error（网关/模型参数不兼容或工具调用失败）"
        if not detail:
            detail = (stream.stdout or "").strip()[-800:] or "Claude 返回错误结果"
        return _summarize_claude_failure_text(detail)
    if stream.result_seen and not stream.result_ok:
        return _summarize_claude_failure_text(
            stream.result_text or stream.result_subtype or "Claude 未成功完成"
        )
    if stream.returncode != 0:
        return _summarize_claude_failure_text(
            (stream.stdout or "").strip()[-1000:] or f"退出码 {stream.returncode}"
        )
    # 无 result 且退出码 0：疑似异常瞬间退出，仍算失败（避免空跑成功）
    if not stream.result_seen:
        return _summarize_claude_failure_text(
            (stream.stdout or "").strip()[-800:]
            or "Claude 未返回完成信号（result），请重试"
        )
    return ""


def execute_codegen_tool(
    tool: str,
    project_path: str,
    prompt: str,
    state: WorkflowState | None = None,
) -> tuple[bool, str, str]:
    """统一调用代码生成 CLI，返回 (ok, stdout, error)。不修改工作流步骤。"""
    settings = get_settings()
    tool = (tool or "claude").strip().lower()

    try:
        if tool == "codex":
            cmd = [settings.codex_bin, "exec", "-C", project_path, "--skip-git-repo-check"]
            if settings.codex_sandbox:
                cmd.extend(["--sandbox", settings.codex_sandbox])
            if settings.codex_model:
                cmd.extend(["-m", settings.codex_model])
            if state is not None:
                stream = stream_command_to_logs(
                    state,
                    cmd,
                    cwd=project_path,
                    input_text=prompt,
                    line_formatter=lambda ln: f"codex: {ln.rstrip()}"[:300] if ln.strip() else None,
                )
                code, stdout = stream.returncode, stream.stdout
            else:
                proc = subprocess.run(
                    cmd, input=prompt, capture_output=True, text=True, timeout=3600
                )
                code, stdout = proc.returncode, proc.stdout or ""
        elif tool == "cursor":
            cmd = build_cursor_agent_command(settings, project_path, prompt)
            if state is not None:
                stream = stream_command_to_logs(
                    state,
                    cmd,
                    cwd=project_path,
                    quiet=True,
                )
                code, stdout = stream.returncode, stream.stdout
            else:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=3600,
                    cwd=project_path,
                )
                code, stdout = proc.returncode, proc.stdout or ""
        else:
            cmd = build_claude_codegen_command(settings, prompt)
            before = _page_source_fingerprint(project_path)
            claude_env = _claude_subprocess_env()
            if state is not None:
                stream = stream_command_to_logs(
                    state,
                    cmd,
                    cwd=project_path,
                    line_formatter=format_claude_stream_line,
                    quiet=True,
                    env=claude_env,
                )
                fail = _claude_stream_failed(stream)
                stdout = stream.stdout
                if fail:
                    return False, stdout, fail
                after = _page_source_fingerprint(project_path)
                if before == after:
                    return (
                        False,
                        stdout,
                        "Claude 已结束但未修改核心页面文件（可能中途 API 失败），请重试",
                    )
                return True, stdout, ""
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=3600,
                cwd=project_path,
                env=claude_env,
            )
            code, stdout = proc.returncode, proc.stdout or ""
            if code != 0:
                return False, stdout, _summarize_claude_failure_text(
                    (stdout or "").strip()[-1000:] or f"退出码 {code}"
                )
            after = _page_source_fingerprint(project_path)
            if before == after:
                return False, stdout, "Claude 已结束但未修改核心页面文件，请重试"
            return True, stdout, ""
    except WorkflowCancelled:
        raise
    except subprocess.TimeoutExpired:
        return False, "", "执行超时（>1h）"
    except FileNotFoundError as exc:
        return False, "", f"未找到可执行文件：{exc.filename or tool}"

    if code != 0:
        err = (stdout or "").strip()[-1000:] or f"退出码 {code}"
        return False, stdout, err
    return True, stdout, ""


def _run_codex(state, settings, project_path, prompt):
    """使用 Codex CLI 生成代码（终端输出不写入实施进度）。"""
    cmd = [settings.codex_bin, "exec", "-C", project_path, "--skip-git-repo-check"]
    if settings.codex_sandbox:
        cmd.extend(["--sandbox", settings.codex_sandbox])
    if settings.codex_model:
        cmd.extend(["-m", settings.codex_model])

    try:
        stream = stream_command_to_logs(
            state,
            cmd,
            cwd=project_path,
            input_text=prompt,
            quiet=True,
        )
        if stream.returncode != 0:
            err = (stream.stdout or "").strip()[-1000:] or "未知错误"
            state.step = WorkflowStep.ERROR
            state.error_step = WorkflowStep.CODEGEN
            state.error = f"Codex 退出码 {stream.returncode}: {err}"
            state.append_log(state.error)
            record_error("codegen", "codex_nonzero_exit", state.error)
            _persist_state(state)
            return state
    except subprocess.TimeoutExpired:
        state.step = WorkflowStep.ERROR
        state.error_step = WorkflowStep.CODEGEN
        state.error = "Codex 执行超时（>1h）"
        record_error("codegen", "codex_timeout", state.error)
        _persist_state(state)
        return state
    except FileNotFoundError:
        state.step = WorkflowStep.ERROR
        state.error_step = WorkflowStep.CODEGEN
        state.error = f"未找到 Codex 可执行文件：{settings.codex_bin}"
        record_error("codegen", "codex_not_found", state.error)
        _persist_state(state)
        return state

    state.step = WorkflowStep.EVALUATE
    _persist_state(state)
    return state


def _run_claude(state, settings, project_path, prompt):
    """使用 Claude Code CLI 生成代码（终端输出不写入实施进度）。"""
    cmd = build_claude_codegen_command(settings, prompt)
    before = _page_source_fingerprint(project_path)
    state.append_log("Claude Code 使用 bare + 工具白名单模式启动（参考 html + png）")
    _persist_state(state)

    try:
        stream = stream_command_to_logs(
            state,
            cmd,
            cwd=project_path,
            quiet=True,
            env=_claude_subprocess_env(),
        )
        fail = _claude_stream_failed(stream)
        if fail:
            state.step = WorkflowStep.ERROR
            state.error_step = WorkflowStep.CODEGEN
            state.error = f"Claude 生成失败：{fail}"
            state.append_log(state.error)
            record_error("codegen", "claude_stream_failed", state.error)
            _persist_state(state)
            return state

        after = _page_source_fingerprint(project_path)
        if before == after:
            state.step = WorkflowStep.ERROR
            state.error_step = WorkflowStep.CODEGEN
            state.error = (
                "Claude 未实际修改核心页面文件（常见于 API/工具调用失败后仍返回完成信号）。"
                "请点击重试，或更换代码生成工具。"
            )
            state.append_log(state.error)
            record_error("codegen", "claude_no_file_change", state.error)
            _persist_state(state)
            return state
    except subprocess.TimeoutExpired:
        state.step = WorkflowStep.ERROR
        state.error_step = WorkflowStep.CODEGEN
        state.error = "Claude 执行超时（>1h）"
        record_error("codegen", "claude_timeout", state.error)
        _persist_state(state)
        return state
    except FileNotFoundError:
        state.step = WorkflowStep.ERROR
        state.error_step = WorkflowStep.CODEGEN
        state.error = f"未找到 Claude 可执行文件：{settings.claude_bin}"
        record_error("codegen", "claude_not_found", state.error)
        _persist_state(state)
        return state

    state.step = WorkflowStep.EVALUATE
    _persist_state(state)
    return state


def _run_cursor(state, settings, project_path, prompt):
    """使用 Cursor Agent CLI 生成代码（终端输出不写入实施进度）。"""
    cmd = build_cursor_agent_command(settings, project_path, prompt)
    state.append_log(
        f"Cursor Agent 命令：{' '.join(cmd[:8])}{' …' if len(cmd) > 8 else ''}"
    )
    _persist_state(state)

    try:
        stream = stream_command_to_logs(
            state,
            cmd,
            cwd=project_path,
            quiet=True,
        )
        if stream.returncode != 0:
            err = (stream.stdout or "").strip()[-1000:] or "未知错误"
            state.step = WorkflowStep.ERROR
            state.error_step = WorkflowStep.CODEGEN
            state.error = f"Cursor 退出码 {stream.returncode}: {err}"
            state.append_log(state.error)
            record_error("codegen", "cursor_nonzero_exit", state.error)
            _persist_state(state)
            return state
    except subprocess.TimeoutExpired:
        state.step = WorkflowStep.ERROR
        state.error_step = WorkflowStep.CODEGEN
        state.error = "Cursor 执行超时（>1h）"
        record_error("codegen", "cursor_timeout", state.error)
        _persist_state(state)
        return state
    except FileNotFoundError:
        state.step = WorkflowStep.ERROR
        state.error_step = WorkflowStep.CODEGEN
        state.error = f"未找到 Cursor 可执行文件：{settings.cursor_bin}"
        record_error("codegen", "cursor_not_found", state.error)
        _persist_state(state)
        return state

    state.step = WorkflowStep.EVALUATE
    _persist_state(state)
    return state

def run_evaluate(state: WorkflowState) -> WorkflowState:
    """进入第 8 步：整理测试发布。"""
    state.quality_report = None
    state.step = WorkflowStep.EVALUATE
    state.append_log(
        "\n【第 8 步：整理测试发布】请生成 README 功能清单，并用 HBuilderX 运行到 Chrome 自测"
    )
    return state


def build_feature_readme(state: WorkflowState) -> str:
    """根据需求方案整理详细功能清单 Markdown。"""
    req = state.requirement
    app_name = (req.app_name if req else None) or state.app_name or "未命名应用"
    direction = (req.direction if req else "") or ""
    summary = (req.summary if req else "") or ""
    lines: list[str] = [
        f"# {app_name}",
        "",
        "## 产品简介",
        "",
        summary or f"{app_name}：纯前端工具类应用。",
        "",
    ]
    if direction:
        lines += [f"**功能方向**：{direction}", ""]

    lines += [
        "## 功能清单",
        "",
        "以下为当前方案确定的页面与核心功能，可用于验收与测试对照。",
        "",
    ]

    pages = list(req.pages) if req and req.pages else []
    if not pages:
        lines += ["_需求方案中暂无页面明细，请先完成需求分析或补充方案。_", ""]
    else:
        for i, page in enumerate(pages, start=1):
            title = page.title or f"页面{i}"
            key = page.key or f"page{i}"
            lines.append(f"### {i}. {title}（`{key}`）")
            lines.append("")
            if page.description:
                lines.append(f"**说明**：{page.description}")
                lines.append("")
            features = [f.strip() for f in (page.features or []) if str(f).strip()]
            if features:
                lines.append("**功能点**：")
                lines.append("")
                for feat in features:
                    lines.append(f"- [ ] {feat}")
                lines.append("")
            else:
                lines.append("- [ ] （待补充功能点）")
                lines.append("")

    sample = list(req.sample_data) if req and req.sample_data else []
    if sample:
        lines += ["## 示例数据", ""]
        for item in sample:
            lines.append(f"- {item}")
        lines.append("")

    style_name = ""
    if 0 <= state.selected_task_index < len(state.style_tasks):
        style_name = state.style_tasks[state.selected_task_index].style_name or ""
    elif state.selected_styles:
        style_name = (state.selected_styles[0] or {}).get("name") or ""

    lines += [
        "## 技术信息",
        "",
        "- 框架：uni-app（Vue）",
        f"- 项目目录：`{state.project_path or state.project_dir or '-'}`",
    ]
    if style_name:
        lines.append(f"- UI 风格：{style_name}")
    if state.group_no:
        lines.append(f"- 组号：{state.group_no}")
    lines += [
        "",
        "## 本地测试",
        "",
        "1. 使用 HBuilderX 打开本项目",
        "2. 菜单「运行 → 运行到浏览器 → Chrome」",
        "3. 确认控制台出现「编译成功」，并记录 Local 测试地址（含端口）",
        "",
        "---",
        "",
        "_本文档由工作流「整理测试发布」自动生成，可按验收结果勾选功能点。_",
        "",
    ]
    return "\n".join(lines)


def prepare_readme(state: WorkflowState) -> WorkflowState:
    """整理：将详细功能清单写入项目 README.md。"""
    if not state.project_path:
        raise ValueError("项目尚未生成，请先完成代码生成")

    project = Path(state.project_path)
    if not project.is_dir():
        raise ValueError(f"项目目录不存在: {state.project_path}")

    state.step = WorkflowStep.EVALUATE
    state.readme_status = "running"
    state.readme_error = ""
    state.append_log("\n【整理】正在生成 README.md 功能清单…")

    try:
        content = build_feature_readme(state)
        readme_path = project / "README.md"
        readme_path.write_text(content, encoding="utf-8")
        state.readme_path = str(readme_path)
        state.readme_status = "done"
        state.append_log(f"✅ README.md 已写入：{readme_path}")
    except Exception as exc:  # noqa: BLE001
        state.readme_status = "failed"
        state.readme_error = str(exc)
        state.append_log(f"❌ 生成 README 失败：{exc}")
        raise
    return state


def run_h5_chrome_test(state: WorkflowState) -> WorkflowState:
    """测试：使用 HBuilderX 工具链编译运行到 Chrome，并记录测试端口。"""
    from app.hbuilderx import run_h5_to_chrome

    if not state.project_path:
        raise ValueError("项目尚未生成，请先完成代码生成")

    settings = get_settings()
    state.step = WorkflowStep.EVALUATE
    state.h5_test_running = True
    state.h5_test_status = "running"
    state.h5_test_message = "正在通过 HBuilderX 编译并打开 Chrome…"
    state.h5_test_url = ""
    state.h5_test_port = 0
    state.append_log("\n【测试】HBuilderX 运行到 Chrome…")

    try:
        result = run_h5_to_chrome(
            state.project_path,
            session_id=state.session_id,
            cli_path=settings.hbuilderx_cli,
            app_path=settings.hbuilderx_app,
            open_chrome=True,
            timeout=180.0,
        )
        state.h5_test_url = result.url
        state.h5_test_port = result.port
        state.h5_test_message = result.message
        if result.ok and result.url:
            state.h5_test_status = "done"
            state.append_log(
                f"✅ 编译成功，测试地址：{result.url}（端口 {result.port}，via={result.via}）"
            )
        else:
            state.h5_test_status = "failed"
            state.append_log(f"❌ 测试失败：{result.message}")
            if result.log_tail:
                state.append_log(result.log_tail[-1500:])
    except Exception as exc:  # noqa: BLE001
        state.h5_test_status = "failed"
        state.h5_test_message = str(exc)
        state.append_log(f"❌ 测试异常：{exc}")
    finally:
        state.h5_test_running = False

    return state


def default_publish_options(state: WorkflowState) -> dict[str, str]:
    """前端发布表单默认值。"""
    from app.publish import default_release_branch

    settings = get_settings()
    return {
        "repo_path": state.release_repo_path or settings.publish_default_repo,
        "branch": state.release_branch
        or default_release_branch(state.group_no, state.project_dir, state.app_name),
        "base_branch": settings.publish_base_branch or "main",
    }


def run_publish(
    state: WorkflowState,
    *,
    repo_path: str,
    branch: str,
) -> WorkflowState:
    """发布：拉取最新仓库 → 新建分支 → 拷贝项目 → 推送远程。"""
    from app.publish import publish_project_to_repo

    if not state.project_path:
        raise ValueError("项目尚未生成，请先完成代码生成")

    settings = get_settings()
    repo = (repo_path or settings.publish_default_repo or "").strip()
    br = (branch or "").strip()
    if not repo:
        raise ValueError("请填写目标 Git 仓库路径")
    if not br:
        raise ValueError("请填写分支名称")

    state.step = WorkflowStep.EVALUATE
    state.release_running = True
    state.release_status = "running"
    state.release_repo_path = repo
    state.release_branch = br
    state.release_message = "正在拉取仓库并发布…"
    state.release_remote_url = ""
    state.release_commit = ""
    state.append_log(f"\n【发布】目标仓库：{repo}")
    state.append_log(f"【发布】分支：{br}")

    try:
        result = publish_project_to_repo(
            state.project_path,
            repo,
            br,
            base_branch=settings.publish_base_branch or "main",
            app_name=state.app_name,
        )
        state.release_repo_path = result.repo_path or repo
        state.release_branch = result.branch or br
        state.release_remote_url = result.remote_url
        state.release_commit = result.commit
        state.release_message = result.message
        for line in result.logs[-20:]:
            state.append_log(line)

        if result.ok:
            state.release_status = "done"
            state.append_log(f"✅ 发布成功：{result.branch}（{result.commit}）")
        elif result.code == "branch_exists":
            state.release_status = "branch_exists"
            state.append_log(f"⚠ {result.message}")
        else:
            state.release_status = "failed"
            state.append_log(f"❌ 发布失败：{result.message}")
    except Exception as exc:  # noqa: BLE001
        state.release_status = "failed"
        state.release_message = str(exc)
        state.append_log(f"❌ 发布异常：{exc}")
    finally:
        state.release_running = False

    return state


def complete_release_step(state: WorkflowState, *, force: bool = False) -> WorkflowState:
    """整理 / 测试 / 发布完成后结束工作流。"""
    if not force:
        if state.readme_status != "done":
            raise ValueError("请先完成「整理」：生成 README.md 功能清单")
        if state.h5_test_status != "done":
            raise ValueError("请先完成「测试」：HBuilderX 运行到 Chrome 并确认编译成功")
        if state.release_status != "done":
            raise ValueError("请先完成「发布」：推送到远程分支")

    state.step = WorkflowStep.DONE
    state.append_log("✅ 整理、测试与发布已完成")
    try:
        from datetime import datetime, timezone

        record_feedback(
            state.session_id,
            state.app_name,
            state.quality_report or QualityReport(checks=[], score=0),
            UserFeedback(
                rating="ok",
                score=0,
                comment="整理测试发布完成",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
        )
    except Exception:  # noqa: BLE001
        pass
    return state


def submit_feedback(state: WorkflowState, feedback: UserFeedback) -> WorkflowState:
    """兼容旧接口：直接完成工作流。"""
    feedback.timestamp = feedback.timestamp or ""
    state.user_feedback = feedback
    if feedback.comment:
        state.append_log(f"\n备注：{feedback.comment}")
    return complete_release_step(state, force=True)


def recover_stale_session_flags(state: WorkflowState, *, grace_sec: float = 180.0) -> bool:
    """清理无对应进程的僵死 running 标志。返回是否有修改。

    grace_sec 需覆盖 init_project 阶段（尚未 register CLI 子进程）。
    """
    changed = False
    now = time.time()

    if state.codegen_running and not cancel_hub.has_live_proc(state.session_id):
        started = float(getattr(state, "codegen_started_at", 0) or 0)
        age = (now - started) if started > 0 else grace_sec + 1
        if age >= grace_sec:
            state.codegen_running = False
            state.codegen_started_at = 0.0
            if state.step in (WorkflowStep.CODEGEN, WorkflowStep.INIT_PROJECT) and state.project_path:
                state.step = WorkflowStep.EVALUATE
                state.error = ""
                state.error_step = ""
                done_lines = state.codegen_progress or []
                if not any(
                    str(x).startswith("生成完成") or str(x).startswith("生成失败")
                    for x in done_lines
                ):
                    state.append_codegen_progress(
                        "生成完成（检测到生成进程已结束，已自动同步状态）"
                    )
                state.append_log("⚠️ 检测到代码生成进程已结束，已同步会话状态")
            changed = True

    if state.design_running and not cancel_hub.has_live_proc(state.session_id):
        started = float(getattr(state, "design_started_at", 0) or 0)
        age = (now - started) if started > 0 else grace_sec + 1
        if age >= grace_sec:
            state.design_running = False
            state.design_started_at = 0.0
            state.append_log("⚠️ 检测到设计生成进程已结束，已同步会话状态")
            changed = True

    if state.followup_running and not cancel_hub.has_live_proc(state.session_id):
        state.followup_running = False
        state.append_log("⚠️ 检测到补充对话进程已结束，已同步会话状态")
        changed = True

    return changed


def recover_all_stale_session_flags() -> int:
    """服务启动时批量清理各会话僵死 running 标志（启动后 cancel_hub 为空，一律视为僵死）。"""
    fixed = 0
    for item in StateStore.list_sessions():
        sid = item.get("session_id") or ""
        if not sid:
            continue
        try:
            store = StateStore(sid)
            state = store.load()
            dirty = False
            if (
                state.codegen_running
                or state.design_running
                or state.followup_running
                or state.h5_test_running
                or state.release_running
            ):
                if state.codegen_running:
                    state.codegen_running = False
                    state.codegen_started_at = 0.0
                    if (
                        state.step in (WorkflowStep.CODEGEN, WorkflowStep.INIT_PROJECT)
                        and state.project_path
                    ):
                        state.step = WorkflowStep.EVALUATE
                        state.error = ""
                        state.error_step = ""
                        done_lines = state.codegen_progress or []
                        if not any(
                            str(x).startswith("生成完成") or str(x).startswith("生成失败")
                            for x in done_lines
                        ):
                            state.append_codegen_progress(
                                "生成完成（服务重启后已同步状态）"
                            )
                        state.append_log("⚠️ 服务重启：已解除卡住的代码生成状态")
                if state.design_running:
                    state.design_running = False
                    state.design_started_at = 0.0
                    state.append_log("⚠️ 服务重启：已解除卡住的设计生成状态")
                if state.followup_running:
                    state.followup_running = False
                    state.append_log("⚠️ 服务重启：已解除卡住的补充对话状态")
                if state.h5_test_running:
                    state.h5_test_running = False
                    if state.h5_test_status == "running":
                        state.h5_test_status = "failed"
                        state.h5_test_message = "服务重启：测试已中断"
                    state.append_log("⚠️ 服务重启：已解除卡住的 H5 测试状态")
                if state.release_running:
                    state.release_running = False
                    if state.release_status == "running":
                        state.release_status = "failed"
                        state.release_message = "服务重启：发布已中断"
                    state.append_log("⚠️ 服务重启：已解除卡住的发布状态")
                dirty = True
            if dirty:
                store.save(state)
                fixed += 1
        except Exception:  # noqa: BLE001
            logger.exception("recover session flags failed: %s", sid)
    return fixed


class WorkflowOrchestrator:
    def __init__(self, session_id: str | None = None) -> None:
        self.store = StateStore(session_id)

    def get_state(self) -> WorkflowState:
        state = self.store.load()
        if recover_stale_session_flags(state):
            self.store.save(state)
        return state

    def save(self, state: WorkflowState) -> None:
        self.store.save(state)

    def start(
        self,
        app_name: str,
        project_dir: str,
        llm_model: str = "",
        group_no: str = "",
    ) -> WorkflowState:
        from datetime import datetime, timezone

        state = WorkflowState(
            session_id=self.store.session_id,
            app_name=app_name.strip(),
            project_dir=project_dir.strip(),
            group_no=(group_no or "").strip(),
            llm_model=llm_model.strip(),
            created_at=datetime.now(timezone.utc).isoformat(),
            step=WorkflowStep.ANALYZE,
        )
        state = run_analyze(state)
        self.save(state)
        return state

    def confirm_requirement(self) -> WorkflowState:
        """用户确认需求方案后，进入 UI 风格选择步骤。"""
        state = self.get_state()
        state = confirm_requirement(state)
        self.save(state)
        return state

    def select_requirement_option(self, index: int) -> WorkflowState:
        """切换三套需求方案中的当前选中项。"""
        state = self.get_state()
        state = select_requirement_option(state, index)
        self.save(state)
        return state

    def prepare_retry_analyze(self, llm_model: str = "") -> WorkflowState:
        """将会话切回分析中，供异步重试前立即刷新 UI。"""
        state = self.get_state()
        allowed = {
            WorkflowStep.ANALYZE,
            WorkflowStep.REVIEW_REQUIREMENT,
            WorkflowStep.SELECT_STYLES,
            WorkflowStep.ERROR,
        }
        if state.step not in allowed:
            raise ValueError("当前步骤不允许重试需求分析")
        if state.step == WorkflowStep.ERROR and state.error_step:
            err_step = state.error_step
            err_val = err_step.value if hasattr(err_step, "value") else str(err_step)
            if err_val not in ("analyze", ""):
                raise ValueError("当前错误不属于需求分析步骤，无法从此处重试分析")
        state.step = WorkflowStep.ANALYZE
        state.error = ""
        state.error_step = ""
        state.analyze_running = True
        if (llm_model or "").strip():
            state.llm_model = llm_model.strip()
            state.append_log(f"切换分析模型：{state.llm_model}")
        state.append_log("🔄 重新开始需求分析…")
        self.save(state)
        return state

    def retry_analyze(self, extra_prompt: str = "") -> WorkflowState:
        """重试需求分析（失败恢复，或审核阶段按提示词重新生成）。"""
        state = self.get_state()
        if state.step != WorkflowStep.ANALYZE or not state.analyze_running:
            state = self.prepare_retry_analyze()

        def _progress(s: WorkflowState) -> None:
            self.save(s)

        try:
            state = run_analyze(state, extra_prompt=extra_prompt, on_progress=_progress)
            self.save(state)
            return state
        except Exception:
            state = self.get_state()
            state.analyze_running = False
            self.save(state)
            raise

    def update_requirement(self, updates: Any) -> WorkflowState:
        """用户修改需求方案（应用名称、页面标题、功能点等）。"""
        state = self.get_state()
        updates_dict = updates.model_dump(exclude_none=True) if hasattr(updates, "model_dump") else dict(updates)
        state = update_requirement(state, updates_dict)
        self.save(state)
        return state

    def set_styles(
        self,
        styles: list[dict[str, str]],
    ) -> WorkflowState:
        state = self.get_state()
        state.selected_styles = styles
        state.step = WorkflowStep.DESIGN
        self.save(state)
        return state

    def skip_design(self) -> WorkflowState:
        """跳过 Stitch 设计，创建虚拟 StyleTask，进入方案确认以便选择代码生成工具。"""
        state = self.get_state()

        if not state.selected_styles:
            raise ValueError("请先选择 UI 风格")

        state.style_tasks = []
        for item in state.selected_styles:
            task = StyleTask(
                style_id=item["id"],
                style_name=item["name"],
                color_hint=item.get("color", ""),
                status="done",
                skip_design=True,
            )
            state.style_tasks.append(task)

        state.selected_task_index = 0
        # 停在选择方案步骤，便于用户确认风格与代码生成工具
        state.step = WorkflowStep.SELECT_PLAN
        state.append_log("跳过 Stitch 设计，请在「选择方案」中确认风格与代码生成工具")
        self.save(state)
        return state

    def run_design_step(self) -> WorkflowState:
        cancel_hub.reset(self.store.session_id)
        state = self.get_state()
        state.design_running = True
        state.design_started_at = time.time()
        self.save(state)
        try:
            state = run_design(state)
            state.design_running = False
            state.design_started_at = 0.0
            self.save(state)
            return state
        except WorkflowCancelled:
            state = self.get_state()
            state.design_running = False
            state.design_started_at = 0.0
            self.save(state)
            raise
        finally:
            state = self.get_state()
            if state.design_running:
                state.design_running = False
                state.design_started_at = 0.0
                self.save(state)
            cancel_hub.clear(self.store.session_id)

    def finalize_cancel(self) -> WorkflowState:
        """立即落盘「用户终止」状态；后台任务感知后安全退出。"""
        state = self.get_state()
        busy = (
            state.design_running
            or state.codegen_running
            or state.followup_running
            or any(t.status == "running" for t in state.style_tasks)
        )
        cancel_hub.request_cancel(self.store.session_id)

        if not busy:
            state.append_log("⏹ 当前没有进行中的环节可终止")
            self.save(state)
            return state

        for task in state.style_tasks:
            if task.status == "running":
                task.status = "failed"
                task.error = "用户终止"

        for msg in state.followup_messages:
            if msg.status == "running":
                msg.status = "error"
                msg.content = "已终止"

        was_design = state.design_running or state.step == WorkflowStep.DESIGN
        was_codegen = state.codegen_running or state.step in (
            WorkflowStep.INIT_PROJECT,
            WorkflowStep.CODEGEN,
        )
        state.design_running = False
        state.codegen_running = False
        state.followup_running = False
        state.analyze_running = False
        state.error = ""
        state.error_step = ""

        if was_design and not was_codegen:
            done_count = sum(1 for t in state.style_tasks if t.status == "done")
            state.step = WorkflowStep.SELECT_PLAN if done_count else WorkflowStep.DESIGN
        elif was_codegen:
            if any(t.status == "done" for t in state.style_tasks):
                state.step = WorkflowStep.SELECT_PLAN
            elif state.project_path:
                state.step = WorkflowStep.EVALUATE
            else:
                state.step = (
                    WorkflowStep.SELECT_STYLES
                    if state.selected_styles
                    else WorkflowStep.REVIEW_REQUIREMENT
                )

        state.append_log("⏹ 已终止当前选中项目的进行中环节")
        self.save(state)
        return state

    def select_plan(
        self,
        task_index: int,
        framework: str = "vue",
        codegen_tool: str = "claude",
    ) -> WorkflowState:
        state = self.get_state()
        if task_index < 0 or task_index >= len(state.style_tasks):
            raise ValueError("无效的方案序号")
        if state.style_tasks[task_index].status != "done":
            raise ValueError("所选方案未完成设计")
        tool = (codegen_tool or "claude").strip().lower()
        if tool not in ("codex", "claude", "cursor"):
            raise ValueError(f"不支持的代码生成工具: {tool}")
        state.selected_task_index = task_index
        state.framework = framework
        state.codegen_tool = tool
        state.append_log(f"已选择方案 #{task_index + 1}，代码生成工具：{tool}")

        task = state.style_tasks[task_index]
        if task.project_id and task.screen_ids and not getattr(task, "skip_design", False):
            try:
                download_stitch_tabs_for_task(state, task)
            except Exception as exc:
                logger.exception("下载 Stitch 设计图失败")
                state.append_log(f"❌ 下载 Stitch 设计图失败: {exc}")
                raise ValueError(f"下载 Stitch 设计图失败: {exc}") from exc
        else:
            state.append_log("所选方案无 Stitch 设计稿，跳过截图下载")

        state.step = WorkflowStep.INIT_PROJECT
        self.save(state)
        return state

    def run_init_and_codegen(self, codegen_tool: str = "claude") -> WorkflowState:
        cancel_hub.reset(self.store.session_id)
        state = self.get_state()
        tool = (codegen_tool or state.codegen_tool or "claude").strip().lower()
        if tool not in ("codex", "claude", "cursor"):
            tool = "claude"
        state.codegen_tool = tool
        # API 层可能已提前置位；此处确保标志一致
        state.codegen_running = True
        if not state.codegen_started_at:
            state.codegen_started_at = time.time()
        state.error = ""
        state.error_step = ""
        self.save(state)
        try:
            cancel_hub.check(self.store.session_id)
            state = run_init_project(state)
            self.save(state)
            cancel_hub.check(self.store.session_id)
            state = run_codegen(state)
            self.save(state)
            if state.step != WorkflowStep.ERROR and not cancel_hub.is_cancelled(self.store.session_id):
                state = run_evaluate(state)
        except WorkflowCancelled:
            state = self.get_state()
            state.append_log("⏹ 代码生成已终止")
            state.append_codegen_progress("⏹ 已终止")
            if any(t.status == "done" for t in state.style_tasks):
                state.step = WorkflowStep.SELECT_PLAN
            state.error = ""
            state.error_step = ""
            self.save(state)
        finally:
            state = self.get_state()
            state.codegen_running = False
            state.codegen_started_at = 0.0
            self.save(state)
            cancel_hub.clear(self.store.session_id)
        return self.get_state()

    def evaluate(self) -> WorkflowState:
        """第 8 步：进入整理测试发布。"""
        state = self.get_state()
        state = run_evaluate(state)
        self.save(state)
        return state

    def prepare_readme(self) -> WorkflowState:
        """整理：写入 README.md 功能清单。"""
        state = self.get_state()
        state = prepare_readme(state)
        self.save(state)
        return state

    def run_h5_test(self) -> WorkflowState:
        """测试：HBuilderX 运行到 Chrome。"""
        state = self.get_state()
        state.h5_test_running = True
        state.h5_test_status = "running"
        state.h5_test_message = "正在通过 HBuilderX 编译并打开 Chrome…"
        self.save(state)
        state = run_h5_chrome_test(state)
        self.save(state)
        return state

    def stop_h5_test(self) -> WorkflowState:
        """关闭当前会话的 H5 测试进程与端口。"""
        from app.hbuilderx import stop_h5_session

        state = self.get_state()
        was_running = bool(state.h5_test_running)
        port = int(state.h5_test_port or 0)
        stop_h5_session(self.store.session_id)

        state.h5_test_running = False
        state.h5_test_url = ""
        state.h5_test_port = 0
        if was_running and state.h5_test_status == "running":
            state.h5_test_status = "failed"
            state.h5_test_message = "已关闭测试端口（编译/运行已中断）"
        else:
            # 保留 done，便于后续发布；仅提示端口已关
            state.h5_test_message = (
                f"已关闭测试端口{f' {port}' if port else ''}"
                if state.h5_test_status == "done"
                else "已关闭测试端口"
            )
        state.append_log(f"⏹ {state.h5_test_message}")
        self.save(state)
        return state

    def run_publish(self, repo_path: str, branch: str) -> WorkflowState:
        """发布：推送到目标 Git 仓库新分支。"""
        state = self.get_state()
        state.release_running = True
        state.release_status = "running"
        state.release_repo_path = (repo_path or "").strip()
        state.release_branch = (branch or "").strip()
        state.release_message = "正在拉取仓库并发布…"
        self.save(state)
        state = run_publish(state, repo_path=repo_path, branch=branch)
        self.save(state)
        return state

    def complete_workflow(self, force: bool = False) -> WorkflowState:
        """整理 + 测试 + 发布完成后结束。"""
        state = self.get_state()
        state = complete_release_step(state, force=force)
        self.save(state)
        return state

    def submit_feedback(self, rating: str, score: int, comment: str) -> WorkflowState:
        """兼容旧反馈接口：强制完成工作流。"""
        from datetime import datetime, timezone
        state = self.get_state()
        feedback = UserFeedback(
            rating=rating,
            score=score,
            comment=comment,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        state = submit_feedback(state, feedback)
        self.save(state)
        return state

    def enqueue_followup(self, message: str, codegen_tool: str = "") -> WorkflowState:
        """写入用户补充指令，标记对话进行中。"""
        from datetime import datetime, timezone

        state = self.get_state()
        if not state.project_path:
            raise ValueError("项目尚未初始化，请先完成代码生成")
        if state.codegen_running or state.followup_running:
            raise ValueError("已有代码任务进行中，请稍后再发")

        text = (message or "").strip()
        if not text:
            raise ValueError("请输入补充指令")
        if len(text) > 8000:
            raise ValueError("指令过长（最多 8000 字）")

        tool = (codegen_tool or state.codegen_tool or "claude").strip().lower()
        if tool not in ("codex", "claude", "cursor"):
            tool = "claude"

        state.codegen_tool = tool
        state.followup_messages.append(
            FollowupMessage(
                role="user",
                content=text,
                timestamp=datetime.now(timezone.utc).isoformat(),
                status="done",
                tool=tool,
            )
        )
        state.followup_messages.append(
            FollowupMessage(
                role="assistant",
                content="正在根据你的指令修改项目…",
                timestamp=datetime.now(timezone.utc).isoformat(),
                status="running",
                tool=tool,
            )
        )
        state.followup_running = True
        state.append_log(f"补充对话（{tool}）：{text[:80]}{'…' if len(text) > 80 else ''}")
        self.save(state)
        return state

    def run_followup(self) -> WorkflowState:
        """执行最近一条补充对话指令。"""
        from datetime import datetime, timezone

        cancel_hub.reset(self.store.session_id)
        state = self.get_state()
        project_path = state.project_path
        try:
            if not project_path:
                raise ValueError("项目路径为空")

            user_msgs = [m for m in state.followup_messages if m.role == "user"]
            if not user_msgs:
                raise ValueError("没有待执行的补充指令")
            latest = user_msgs[-1]
            tool = (latest.tool or state.codegen_tool or "claude").strip().lower()
            prompt = build_followup_prompt(state, latest.content)
            state.append_log("📡 补充对话：实时输出开始")
            self.save(state)

            ok, output, err = execute_codegen_tool(tool, project_path, prompt, state=state)

            state = self.get_state()
            now = datetime.now(timezone.utc).isoformat()
            for msg in reversed(state.followup_messages):
                if msg.role == "assistant" and msg.status == "running":
                    if ok:
                        msg.content = (
                            summarize_tool_output(output) or "已完成修改，请检查项目代码。"
                        )
                        msg.status = "done"
                    else:
                        msg.content = f"执行失败：{err or '未知错误'}"
                        msg.status = "error"
                    msg.timestamp = now
                    msg.tool = tool
                    break

            if ok:
                state.append_log(f"补充对话完成（{tool}）")
            else:
                state.append_log(f"补充对话失败（{tool}）：{err}")
                record_error("followup", f"{tool}_failed", err or "unknown")
        except WorkflowCancelled:
            state = self.get_state()
            for msg in reversed(state.followup_messages):
                if msg.role == "assistant" and msg.status == "running":
                    msg.content = "已终止"
                    msg.status = "error"
                    break
            state.append_log("⏹ 补充对话已终止")
        except Exception as exc:
            state = self.get_state()
            for msg in reversed(state.followup_messages):
                if msg.role == "assistant" and msg.status == "running":
                    msg.content = f"执行异常：{exc}"
                    msg.status = "error"
                    break
            state.append_log(f"补充对话异常：{exc}")
            record_error("followup", "exception", str(exc))
        finally:
            state = self.get_state()
            state.followup_running = False
            if len(state.followup_messages) > 80:
                state.followup_messages = state.followup_messages[-80:]
            self.save(state)
            cancel_hub.clear(self.store.session_id)
        return state
