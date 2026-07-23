# 后端架构设计与重构建议

> 项目：workflow_uniapp · 日期：2026-07-10

---

## 一、现状诊断

当前后端是典型的「快速原型」阶段架构——能用，但随功能增长开始显现结构性债务。

### 当前结构

```
app/
├── main.py          # 所有 API 路由 + SSE（~300 行）
├── config.py        # 配置读取
├── models.py        # 数据模型（Pydantic）
├── llm/
│   └── client.py    # LLM 客户端
├── stitch/
│   └── mcp_client.py # Stitch MCP 客户端
└── workflow/
    ├── orchestrator.py  # 状态机 + 所有步骤逻辑（~550 行）
    ├── state.py         # 状态持久化
    └── quality.py       # 质量检查 + 反馈
```

### 问题清单

| 问题 | 严重度 | 表现 |
|------|--------|------|
| 编排器臃肿 | 🔴 | `orchestrator.py` 集状态管理、步骤逻辑、质量检查、反馈于一身，550+ 行 |
| API 层与业务层耦合 | 🔴 | `main.py` 直接实例化 `WorkflowOrchestrator`，无依赖注入 |
| 后台任务管理缺失 | 🟠 | `asyncio.create_task` 裸调，无取消/超时/重试机制 |
| CLI 与 API 逻辑重复 | 🟠 | `cli.py` 复制了 API 层的编排调用链 |
| 错误处理分散 | 🟠 | 部分在 orchestrator 里设 `state.error`，部分在 `main.py` 的 except 里 |
| 无服务层抽象 | 🟡 | API 路由直接操作 orchestrator，扩展新入口（如 WebSocket、gRPC）需重写 |
| 状态机逻辑硬编码 | 🟡 | `run_codegen` 里写死 `state.step = EVALUATE`，步骤跳转散落各处 |

---

## 二、推荐架构：分层 + 服务化

```
app/
├── api/                          # ★ 表示层（只做路由、校验、序列化）
│   ├── __init__.py
│   ├── deps.py                   # FastAPI 依赖注入
│   ├── router/
│   │   ├── sessions.py           # 会话 CRUD
│   │   ├── workflow.py           # 工作流步骤
│   │   ├── design.py             # 设计生成
│   │   ├── codegen.py            # 代码生成
│   │   └── feedback.py           # 评估反馈
│   └── sse.py                    # SSE 事件流
│
├── services/                     # ★ 业务逻辑层（与传输协议无关）
│   ├── __init__.py
│   ├── session_service.py        # 会话管理
│   ├── workflow_service.py       # 工作流编排（原 orchestrator 核心）
│   ├── design_service.py         # Stitch 设计服务
│   ├── codegen_service.py        # Codex 代码生成服务
│   ├── quality_service.py        # 质量评估服务
│   └── feedback_service.py       # 反馈收集服务
│
├── domain/                       # ★ 领域模型（纯数据，无业务逻辑）
│   ├── __init__.py
│   ├── models.py                 # Pydantic 模型（原 models.py）
│   ├── state_machine.py          # 步骤状态机定义
│   ├── events.py                 # 领域事件定义
│   └── errors.py                 # 领域异常
│
├── infrastructure/               # ★ 基础设施（外部依赖封装）
│   ├── __init__.py
│   ├── config.py                 # 配置（原 config.py）
│   ├── llm_client.py             # LLM 客户端
│   ├── stitch_client.py          # Stitch MCP 客户端
│   ├── codex_client.py           # Codex CLI 封装
│   ├── state_store.py            # 状态持久化（原 state.py）
│   └── feedback_store.py         # 反馈持久化
│
├── background/                   # ★ 后台任务管理
│   ├── __init__.py
│   ├── task_manager.py           # 任务生命周期管理
│   └── workers.py                # 长时间运行任务的 worker
│
└── main.py                       # FastAPI 应用组装
```

---

## 三、核心设计决策

### 1. 服务层是核心，协议层是可插拔的

**为什么**：你已经有 Web UI + CLI 两个入口。未来可能还有 WebSocket、MCP Server 模式。业务逻辑不应该绑定到 HTTP 路由上。

**实现**：

```python
# app/services/workflow_service.py
class WorkflowService:
    def __init__(self, state_store, llm_client, stitch_client, codex_client, quality_service):
        self.state_store = state_store
        self.llm = llm_client
        self.stitch = stitch_client
        self.codex = codex_client
        self.quality = quality_service

    def start(self, app_name: str, project_dir: str, llm_model: str = "") -> WorkflowState:
        """启动新工作流（纯业务逻辑，不涉及 HTTP）。"""
        ...

    def advance_to_design(self, session_id: str) -> WorkflowState:
        """推进到设计步骤。"""
        ...
```

```python
# app/api/router/workflow.py（API 层只做薄包装）
@router.post("/api/session/start")
async def start_session(body: StartRequest, service: WorkflowService = Depends(get_workflow_service)):
    try:
        state = service.start(body.app_name, body.project_dir, body.llm_model)
        return state.to_public_dict()
    except DomainError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

```python
# cli.py（CLI 入口复用同一服务）
def cmd_start(args):
    service = build_workflow_service()
    state = service.start(args.app_name, args.project_dir)
    print(json.dumps(state.to_public_dict(), indent=2))
```

### 2. 显式状态机替代隐式步骤跳转

**为什么**：当前 `state.step = EVALUATE` 散落在 `run_codegen`、`submit_feedback` 等函数里，修改步骤顺序需要改多处代码。

**实现**：

```python
# app/domain/state_machine.py
from enum import Enum
from dataclasses import dataclass
from typing import Callable

class Step(Enum):
    INPUT = "input"
    ANALYZE = "analyze"
    REVIEW_REQUIREMENT = "review_requirement"
    SELECT_STYLES = "select_styles"
    DESIGN = "design"
    SELECT_PLAN = "select_plan"
    INIT_PROJECT = "init_project"
    CODEGEN = "codegen"
    EVALUATE = "evaluate"
    DONE = "done"
    ERROR = "error"

# 步骤转换表（单一真相源）
TRANSITIONS: dict[Step, list[Step]] = {
    Step.INPUT:           [Step.ANALYZE],
    Step.ANALYZE:         [Step.REVIEW_REQUIREMENT, Step.ERROR],
    Step.REVIEW_REQUIREMENT: [Step.SELECT_STYLES],
    Step.SELECT_STYLES:   [Step.DESIGN, Step.INIT_PROJECT],  # 双路线
    Step.DESIGN:          [Step.SELECT_PLAN, Step.ERROR],
    Step.SELECT_PLAN:     [Step.INIT_PROJECT],
    Step.INIT_PROJECT:    [Step.CODEGEN, Step.ERROR],
    Step.CODEGEN:         [Step.EVALUATE, Step.ERROR],
    Step.EVALUATE:        [Step.DONE],
    Step.DONE:            [],
    Step.ERROR:           [Step.ANALYZE, Step.DESIGN, Step.CODEGEN],  # 可重试
}

def can_transition(from_step: Step, to_step: Step) -> bool:
    return to_step in TRANSITIONS.get(from_step, [])

@dataclass
class StepHandler:
    """每个步骤绑定的处理函数和回滚函数。"""
    execute: Callable[[WorkflowState], WorkflowState]
    rollback: Callable[[WorkflowState], WorkflowState] | None = None
    validate: Callable[[WorkflowState], list[str]] | None = None  # 进入前校验

STEP_HANDLERS: dict[Step, StepHandler] = {
    Step.ANALYZE: StepHandler(execute=run_analyze, validate=validate_input),
    Step.DESIGN:  StepHandler(execute=run_design, validate=validate_styles, rollback=cleanup_stitch_projects),
    # ...
}
```

### 3. 后台任务管理器

**为什么**：当前 `asyncio.create_task` 裸调，没有取消机制、没有超时控制、没有重试策略。设计生成可能跑 10 分钟，用户中途离开或刷新页面，任务泄漏。

**实现**：

```python
# app/background/task_manager.py
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class TaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class BackgroundTask:
    task_id: str
    session_id: str
    step: str
    status: TaskStatus = TaskStatus.QUEUED
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None

class TaskManager:
    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._registry: dict[str, BackgroundTask] = {}

    async def submit(self, session_id: str, step: str, coro) -> str:
        """提交后台任务，返回 task_id。"""
        task_id = f"{session_id}-{step}"
        if task_id in self._tasks:
            self.cancel(task_id)  # 自动取消旧任务
        bg_task = BackgroundTask(task_id=task_id, session_id=session_id, step=step, ...)
        self._registry[task_id] = bg_task
        self._tasks[task_id] = asyncio.create_task(self._run(task_id, coro))
        return task_id

    async def cancel(self, task_id: str):
        """取消任务（优雅关闭子进程）。"""
        ...

    async def status(self, task_id: str) -> BackgroundTask:
        """查询任务状态。"""
        ...

    async def shutdown(self):
        """优雅关闭所有任务。"""
        ...
```

### 4. 领域事件替代硬编码回调

**为什么**：当前步骤完成后直接在代码里写 `state.step = EVALUATE`、`record_error(...)`，每加一个副作用都要改编排器代码。

**实现**：

```python
# app/domain/events.py
@dataclass
class WorkflowEvent:
    session_id: str
    timestamp: str

class StepCompleted(WorkflowEvent):
    step: Step
    next_step: Step

class StepFailed(WorkflowEvent):
    step: Step
    error_type: str
    error_detail: str

class CodegenFinished(WorkflowEvent):
    project_path: str
    quality_score: int | None

# 在状态机中发布事件
def advance(state: WorkflowState, to_step: Step) -> WorkflowState:
    from_step = state.step
    state.step = to_step
    # 发布事件，订阅者各自处理
    event_bus.publish(StepCompleted(session_id=state.session_id, step=from_step, next_step=to_step))
    return state

# 订阅者独立注册（在 app/main.py 启动时）
event_bus.subscribe(StepCompleted, on_step_completed_notify_sse)
event_bus.subscribe(StepFailed, on_step_failed_record_error)
event_bus.subscribe(CodegenFinished, on_codegen_finished_run_quality_check)
```

### 5. 配置分层

**为什么**：当前 `config.py` 把所有配置（LLM、Stitch、Codex、路径）全放一个 `Settings` 类。未来新增配置项时定位困难。

**实现**：

```python
# app/infrastructure/config.py
class LLMSettings(BaseSettings):
    provider: str = "openai"
    base_url: str = "http://192.168.100.231:8080/v1"
    api_key: str = ""
    model: str = "deepseek/deepseek-v4-flash"
    model_config = SettingsConfigDict(env_prefix="LLM_")

class StitchSettings(BaseSettings):
    api_key: str = ""
    mcp_url: str = "https://stitch.googleapis.com/mcp"
    oauth_token: str = ""
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    model_config = SettingsConfigDict(env_prefix="STITCH_")

class CodexSettings(BaseSettings):
    bin: str = "codex"
    model: str = ""
    sandbox: str = "workspace-write"
    model_config = SettingsConfigDict(env_prefix="CODEX_")

class AppSettings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8765
    template_dir: str = ""
    output_dir: str = ""

class Settings(BaseSettings):
    llm: LLMSettings = LLMSettings()
    stitch: StitchSettings = StitchSettings()
    codex: CodexSettings = CodexSettings()
    app: AppSettings = AppSettings()
```

---

## 四、重构优先级

| 优先级 | 改动 | 收益 | 风险 |
|--------|------|------|------|
| **P0 即刻** | 拆分 `orchestrator.py` 步骤到独立文件 | 可维护性↑ | 低（内部重构） |
| **P0 即刻** | 抽象 `TaskManager` 管理后台任务 | 资源泄漏↓ | 中（异步逻辑） |
| **P1 近期** | 引入服务层（`app/services/`） | CLI/API 逻辑复用 | 中（架构变动） |
| **P1 近期** | 显式状态机 + 转换表 | 步骤跳转可预测 | 低 |
| **P2 后续** | 领域事件 + 发布订阅 | 副作用解耦 | 低（新增代码） |
| **P2 后续** | 按 env_prefix 分层配置 | 配置可读性↑ | 低（改名兼容） |
| **P3 远期** | 依赖注入框架（如 dependency-injector） | 测试性↑ | 中（学习曲线） |

---

## 五、建议先从这两步开始

**第一步**：拆 `orchestrator.py`（风险最低、收益立竿见影）

```
app/workflow/
├── orchestrator.py      # 保留：WorkflowOrchestrator（对外门面 + 状态机调度）
├── steps/
│   ├── __init__.py
│   ├── analyze.py       # run_analyze
│   ├── review.py        # confirm_requirement, update_requirement
│   ├── design.py        # run_design, build_screen_prompt, skip_design
│   ├── codegen.py       # run_init_project, run_codegen, build_codex_prompt
│   └── evaluate.py      # run_evaluate, submit_feedback
├── state.py             # 不变
└── quality.py           # 不变
```

**第二步**：引入 `TaskManager`

让 `POST /api/session/{id}/design` 和 `POST /api/session/{id}/generate-code` 通过 `TaskManager.submit()` 提交任务，支持取消、状态查询和优雅关闭。
