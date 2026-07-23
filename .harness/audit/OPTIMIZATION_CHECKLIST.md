# 项目工程结构与规范梳理 · 优化清单

> 项目：workflow_uniapp · 检查日期：2026-07-09  
> 2026-07-14：根目录临时文档与重复工程 `workflow-server` 已清理；用户文档仅保留 `README.md`。  
> **2026-07-16**：主文档迁至 `docs/工作流说明.md`；新增 `docs/优化分析.md`。下文部分条目已过时，以优化分析文档为准。

---

## 一、项目骨架总览

```
workflow_uniapp/
├── .harness/                    # Harness 规范目录
│   ├── agents/workflow-agent.md # 主编排 Agent 定义
│   ├── rules/agent-behavior.md  # Agent 行为与文案规范
│   ├── wiki/
│   │   ├── ARCHITECTURE.md      # 工作流架构总纲
│   │   └── STITCH_MCP_SPEC.md   # Stitch MCP 集成规范
│   ├── skills/app-gen-workflow/SKILL.md  # 工作流 Skill 定义
│   └── changes/                 # 变更归档（仅 2 条记录）
│       ├── stitch-mcp-spec-20260709/CHANGE.md
│       └── stack-overflow-fix/CHANGE.md
├── app/                         # 核心代码
│   ├── main.py                  # FastAPI Web API + SSE
│   ├── config.py                # 配置读取（pydantic-settings）
│   ├── models.py                # 数据模型（WorkflowState 等）
│   ├── llm/client.py            # LLM 客户端（OpenAI 兼容）
│   ├── stitch/mcp_client.py     # Stitch MCP HTTP JSON-RPC 客户端
│   ├── workflow/
│   │   ├── orchestrator.py      # 工作流编排器（步骤状态机）
│   │   └── state.py             # 会话状态持久化
│   └── web/                     # 前端
│       ├── templates/index.html # 主页面
│       └── static/
│           ├── app.js           # 前端主逻辑 (~1007 行)
│           ├── logger.js        # 调试日志系统 (~297 行)
│           └── style.css        # 样式 (~615 行)
├── config.yaml                  # UI 风格、页面数等静态配置
├── .env                         # 环境变量（已配置真实密钥）
├── .env.example                 # 环境变量模板
├── run.py                       # Web 服务入口
├── cli.py                       # 命令行入口
├── .runtime/sessions/           # 运行时会话状态（gitignore）
└── *.md                         # 29 个根目录 Markdown 文件（严重问题）
```

---

## 二、规范对照：当前状态 vs Harness 约定

| 约定 | 来源 | 当前状态 |
|------|------|----------|
| 主编排 Agent：workflow-agent | `.harness/agents/workflow-agent.md` | ✅ 已定义 |
| 架构总纲 | `.harness/wiki/ARCHITECTURE.md` | ✅ 文档完善 |
| 变更归档：每次交付留 CHANGE.md | `.claude/CLAUDE.md` | ❌ 仅 2 条，缺失大量变更记录 |
| 规则优先级：.harness/rules/ > CLAUDE.md > 临时指令 | `.claude/CLAUDE.md` | ⚠️ 部分遵循 |
| Stitch 不自动重试 | `.harness/wiki/STITCH_MCP_SPEC.md` | ✅ 已实现 |
| 不暴露模型/厂商信息 | `.harness/rules/agent-behavior.md` | ⚠️ index.html 中有"分析模型"标签 |
| 禁止未经要求 git commit | `.harness/rules/agent-behavior.md` | ✅ 遵守 |
| 不泄露 .env | `.harness/rules/agent-behavior.md` | ✅ .gitignore 已排除 |

---

## 三、优化清单

### 🔴 P0 — 阻塞性问题（影响核心功能）

#### 1. Stitch MCP 缺少 OAuth 2 Token 支持
- **文件**：`app/stitch/mcp_client.py`
- **问题**：`_post()` 仅使用 `X-Goog-Api-Key` header，但 `generate_screen_from_text` 工具要求 OAuth 2 access token。`initialize()` 响应中应包含 token，但未提取使用。
- **影响**：创建项目成功，但生成设计稿失败（"Expected OAuth 2 access token"）
- **修复方向**：在 `initialize()` 中提取 OAuth token → 存入实例变量 → `_post()` 添加 `Authorization: Bearer <token>` header

#### 2. 分析重试按钮调用不存在的 API
- **文件**：`app/web/static/app.js` 第 702 行
- **问题**：`btnRetryAnalyze` 调用 `POST /api/session/{sessionId}/analyze`，但 `app/main.py` 中**不存在此路由**。重试按钮会返回 404/405。
- **修复方向**：在 `main.py` 添加 `@app.post("/api/session/{session_id}/analyze")` 路由，或改为调用已有的 `start`/`confirm-requirement` 流程

#### 3. `switchProject` 仍有潜在栈溢出风险
- **文件**：`app/web/static/app.js` 第 899-933 行
- **问题**：`switchProject` → `applyState` → `refreshAllContent` → `updateErrorDisplay` 链中，`attachProjectListeners` 的 cloneNode+replaceChild 方案掩盖了根本问题。5 秒定时器 `setInterval(loadProjectsList, 5000)` 持续触发 `renderProjectsList`，与用户点击切换产生竞态。
- **当前症状**：刷新页面后点击出错项目仍会弹窗 "Maximum call stack size exceeded"
- **修复方向**：将 `setTimeout(() => renderProjectsList(...), 0)` 改为仅在 `projectsCache` 实际变化时才触发；或者使用 requestAnimationFrame 确保 DOM 更新在单帧内完成

### 🟠 P1 — 高优先级（影响稳定性/可维护性）

#### 4. 根目录 29 个 .md 文件严重违反 Harness 约定
- **文件**：项目根目录下 29 个 Markdown 文件
- **问题**：Harness 约定变更文档应放在 `.harness/changes/<id>/CHANGE.md`，但这些文件散落在根目录。包括诊断报告、修复记录、实现指南等，大量内容重复且过时。
- **修复方向**：
  - 每个有意义的变更整理为一条 `.harness/changes/<id>/CHANGE.md`
  - 删除临时诊断/分析文件（LOG_ANALYSIS_REPORT.md、QUICK_DIAGNOSIS.md 等）
  - 保留 QUICK_START.md、README.md 等面向用户的文档
  - 考虑创建 `docs/` 目录集中管理用户文档

#### 5. 缺失变更归档记录
- **文件**：`.harness/changes/` 目录
- **问题**：已记录仅 2 次变更，但实际进行了大量功能开发：
  - 前端日志系统 ~300 行
  - 项目列表+切换
  - 新建项目按钮
  - 项目删除
  - 重试按钮
  - setImmediate 兼容性修复
  - 需求审查步骤
- **修复方向**：为每个已完成功能补写 CHANGE.md

#### 6. `applyState` 被 monkey-patch 覆盖
- **文件**：`app/web/static/app.js` 第 937-949 行
- **问题**：
  ```javascript
  const originalApplyState = applyState;
  applyState = function (newState, opts = {}) { ... };
  ```
  这种模式脆弱且不透明。函数声明在第 441 行，然后在加载时被覆盖，调试困难。
- **修复方向**：使用显式的状态变更回调/观察者模式替代 monkey-patching

#### 7. Python 版本不匹配
- **文件**：`.venv/`
- **问题**：`.claude/CLAUDE.md` 声明 "Python 3.11+"，但 `.venv` 实际是 Python 3.9（`lib/python3.9/`）。`from __future__ import annotations` 到处使用说明存在兼容性焦虑。
- **修复方向**：统一到 Python 3.11+ 并重建虚拟环境，或更新文档声明实际使用的版本

### 🟡 P2 — 中优先级（代码质量/架构改进）

#### 8. 前端 JS 超过 1000 行无模块化
- **文件**：`app/web/static/app.js`（~1007 行）
- **问题**：单个文件包含 UI 渲染、状态管理、API 调用、事件处理、项目列表管理等全部逻辑。无模块拆分、无组件化。
- **修复方向**：拆分为独立模块（state.js, api.js, ui-render.js, events.js 等），使用 ES modules

#### 9. 零测试覆盖
- **问题**：项目中无任何测试文件（`test_*.py`、`*.test.js` 等均不存在）
- **修复方向**：至少为核心模块添加单元测试：
  - `app/workflow/orchestrator.py` — 步骤状态机逻辑
  - `app/stitch/mcp_client.py` — JSON-RPC 解析
  - `app/llm/client.py` — JSON 解析回退逻辑
  - 前端关键函数（`getUnlockedPanels`、`defaultPanelForStep` 等）

#### 10. SSE 轮询效率低
- **文件**：`app/main.py` 第 188-215 行
- **问题**：`session_events` 每 2 秒轮询一次状态文件 (`asyncio.sleep(2)`)，即使无变化也会读文件+序列化+比较。对于长时间运行的设计步骤（数分钟），大部分轮询是浪费。
- **修复方向**：使用 `asyncio.Event` 或文件监控（watchdog/inotify）替代定时轮询

#### 11. `attachProjectListeners` 的 cloneNode 模式不必要
- **文件**：`app/web/static/app.js` 第 872-896 行
- **问题**：既然已使用事件委托（单一 listener 在容器上），cloneNode+replaceChild 去除旧 listener 的模式就不必要了。只需要确保不重复添加 listener（例如使用标志位或 `removeEventListener` 先移除再添加）。
- **修复方向**：简化为纯事件委托，移除 cloneNode 操作

#### 12. Stitch 客户端不支持异步
- **文件**：`app/stitch/mcp_client.py`
- **问题**：使用同步 `httpx.Client`，但在 FastAPI async 路由中通过 `asyncio.to_thread` 调用。每次调用阻塞线程池中的线程。
- **修复方向**：改为 `httpx.AsyncClient` + 原生 async/await，避免线程池开销

#### 13. 配置路径硬编码
- **文件**：`app/config.py` 第 30-31 行
- **问题**：`uniapp_template_dir` 和 `uniapp_output_dir` 默认值硬编码个人路径 `/Users/shihongwei/...`，无法在其他机器运行。
- **修复方向**：使用相对路径或环境变量，提供明确的配置文档

### 🟢 P3 — 低优先级（体验优化/代码整洁）

#### 14. HTML 中暴露"分析模型"标签
- **文件**：`app/web/templates/index.html` 第 63 行
- **问题**：Harness 规则要求"不展示具体模型名称"，但标签写的是"分析模型（可更换）"
- **修复方向**：改为"智能分析引擎"或类似产品语言

#### 15. debugLogger 初始化时机依赖
- **文件**：`app/web/static/app.js` 第 4 行
- **问题**：`const logger = window.debugLogger || { log: console.log }` — 如果 logger.js 加载失败，app.js 静默降级但可能丢失关键调试信息
- **修复方向**：在 app.js 中添加降级日志系统的启动警告

#### 16. `setInterval(loadProjectsList, 5000)` 持续运行
- **文件**：`app/web/static/app.js` 第 953 行
- **问题**：无论是否有项目、页面是否可见，每 5 秒发送一次请求
- **修复方向**：使用 Page Visibility API 暂停不可见时的轮询；有 SSE 连接时降低轮询频率

#### 17. 日志系统缺少性能监控
- **文件**：`app/web/static/logger.js`
- **问题**：每次 `log()` 调用都触发 `render()`（第 168 行），高频日志场景下（如 SSE 每秒多条消息）会产生大量 DOM 操作
- **修复方向**：使用 requestAnimationFrame 或 throttle 批量渲染

#### 18. `config.yaml` 的 `frameworks` 列表仅 vue
- **文件**：`config.yaml` 第 43-45 行
- **问题**：只有一个框架选项，且前端 HTML 硬编码了 Vue radio。扩展性差。
- **修复方向**：动态渲染框架选项，或至少明确文档说明当前仅支持 Vue

---

## 四、文件整理建议

### 根目录 .md 文件分类处理

| 保留（用户文档） | 归档到 .harness/changes/ | 直接删除（临时诊断） |
|---|---|---|
| README.md | STITCH_MCP_IMPLEMENTATION.md | LOG_ANALYSIS_REPORT.md |
| QUICK_START.md | REQUIREMENT_REVIEW_SPEC.md | QUICK_DIAGNOSIS.md |
| TERMINAL_GUIDE.md | REQUIREMENT_REVIEW_IMPLEMENTATION.md | ACTION_PLAN.md |
| | WORKFLOW_PROJECTS_SIDEBAR.md | FINAL_SUMMARY.md |
| | PROJECT_DELETE_FEATURE.md | ROOT_CAUSE_FOUND.md |
| | STITCH_BUG_FIX.md | STITCH_OAUTH_COMPLETE_DIAGNOSIS.md |
| | DESIGN_GENERATION_FIX.md | QUICK_TEST_GUIDE.md |
| | NEW_PROJECT_FEATURE.md | |
| | DEBUG_LOGGER_GUIDE.md | |
| | LOGGER_IMPLEMENTATION.md | |
| | LOGGER_SUMMARY.md | |
| | LOGGER_DELIVERY.md | |
| | LOGGER_QUICK_START.md | |
| | FETCH_ERROR_FIX.md | |
| | FETCH_ERROR_FIXED.md | |
| | STITCH_API_KEY_FIX.md | |
| | STITCH_OAUTH_ERROR.md | |
| | STITCH_OAUTH_FIX.md | |
| | RETRY_BUTTON_FEATURE.md | |
| | RETRY_BUTTON_IMPLEMENTATION_COMPLETE.md | |
| | STACK_OVERFLOW_FIX_COMPLETE.md | |
| | SETIMMEDIATE_FIX.md | |

---

## 五、总结

| 优先级 | 数量 | 说明 |
|--------|------|------|
| 🔴 P0 | 3 | Stitch OAuth、分析 API 缺失、栈溢出 |
| 🟠 P1 | 4 | 文件混乱、缺失变更记录、monkey-patch、Python 版本 |
| 🟡 P2 | 6 | 模块化、测试、SSE、cloneNode、异步、硬编码 |
| 🟢 P3 | 5 | 文案、加载时序、轮询优化、渲染性能、扩展性 |
| **合计** | **18** | |

**建议优先修复 P0 中的 #2（分析重试 API 缺失）— 这是改动最小、影响最直接的问题。然后依次处理 #1（Stitch OAuth）和 #3（栈溢出），这三个问题修复后核心工作流即可端到端运行。**
