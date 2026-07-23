# Harness 四层体系对照分析

> 项目：workflow_uniapp · 分析日期：2026-07-10

---

## 一、总览

| 层 | 核心职责 | 当前状态 |
|----|----------|----------|
| 指令与工具层 | 明确任务目标、划定工具和权限 | ⚠️ 部分实现 |
| 状态与观测层 | 记录进度和操作，方便回溯 | ✅ 已实现 |
| 评估与约束层 | 验收标准、高危操作拦截 | ❌ 几乎空白 |
| 反馈层 | 从错误中学习，优化规则 | ❌ 完全空白 |

---

## 二、逐层分析

### 层 1：指令与工具层

**做了什么**：

- `.harness/agents/workflow-agent.md` — 定义了 Agent 角色、职责、工作流步骤
- `.harness/skills/app-gen-workflow/SKILL.md` — 定义了 6 步工作流 Skill
- `.harness/rules/agent-behavior.md` — 3 条行为约束（不暴露模型名、不自动重试 Stitch、不主动 git commit）
- `.claude/CLAUDE.md` — 项目入口，声明了技术栈、代码目录、常用命令
- `config.yaml` — 19 种 UI 风格、页面数量等静态配置
- `app/main.py` — 15 个 API 路由，覆盖完整工作流生命周期
- `app/workflow/orchestrator.py` — 10 步状态机，每步有明确的输入/输出

**缺失了什么**：

1. **工具权限声明不完整** — `SKILL.md` 没有列出 Agent 可用的 MCP 工具（`create_project`、`generate_screen_from_text`、`get_screen`）及其调用约束
2. **Codex 工具权限未定义** — 没有 `.harness/rules/codex-tools.md` 声明 Codex 可用的操作（文件读写、执行命令等）和禁止的操作
3. **LLM 调用策略未文档化** — `analyze_requirement` 的 system prompt 写在 `app/llm/client.py` 里，没有提到 `.harness/` 规范层
4. **双路线（Stitch/Codex 直出）选择规则未文档化** — 新增的 `skip-design` 路由在 SKILL.md 和 ARCHITECTURE.md 中都没有体现

**如何实现**：

```markdown
# .harness/rules/tool-permissions.md（新增）

## MCP 工具权限
| 工具 | 调用阶段 | 约束 |
|------|----------|------|
| create_project | SELECT_STYLES → DESIGN | 仅 API Key 认证 |
| generate_screen_from_text | DESIGN | 需 OAuth token；失败不重试 |
| get_screen | CODEGEN | 需 OAuth token |

## Codex 工具权限
| 操作 | 允许 | 约束 |
|------|------|------|
| 文件读写 | workspace-write | 仅限项目目录内 |
| 执行命令 | 否 | 沙箱限制 |
| 网络请求 | 否 | 沙箱限制 |
```

---

### 层 2：状态与观测层

**做了什么**：

- `app/models.py` — 完整的 `WorkflowState` 数据模型（含 `step`、`error`、`error_step`、`logs` 等字段）
- `app/workflow/state.py` — 会话状态持久化到 `.runtime/sessions/<id>/state.json`，支持刷新恢复
- `app/main.py` — SSE 事件流实时推送状态变更
- `app/web/static/logger.js` — 前端调试日志系统（拦截 fetch、console、SSE、全局错误）
- `app/web/static/app.js` — 前端状态管理（`applyState`、`refreshAllContent`、`renderProjectsList` 等）
- 侧边栏项目列表实时显示每个会话的步骤状态

**缺失了什么**：

1. **无操作审计日志** — 当前 `state.logs` 只记录工作流级别日志（"开始需求分析"、"设计完成"），没有记录每次 API 调用、工具调用、状态变更的详细信息
2. **无结构化日志** — 日志是纯文本列表，没有时间戳、调用方、结果码等结构化字段
3. **无日志持久化** — `state.logs` 限制 200 条，超出即丢弃；没有按时间归档的日志文件
4. **无性能观测** — 没有记录每步耗时、LLM token 消耗、Stitch API 调用次数
5. **前端日志与后端日志分离** — 前端 `logger.js` 和后端 Python `logging` 各自独立，无关联 ID

**如何实现**：

```python
# app/models.py — 结构化日志
class AuditEntry(BaseModel):
    timestamp: str
    level: str  # info | warn | error
    category: str  # api | llm | stitch | codex | workflow
    action: str  # 如 "call_tool/generate_screen_from_text"
    detail: dict[str, Any] = {}  # 结构化字段
    duration_ms: int = 0

# 在 orchestrator 中埋点
def run_design(state):
    entry = AuditEntry(
        timestamp=datetime.now().isoformat(),
        level="info",
        category="stitch",
        action="initialize",
    )
    t0 = time.time()
    stitch.initialize()
    entry.duration_ms = int((time.time() - t0) * 1000)
    state.audit_log.append(entry)
```

```javascript
// logger.js 新增关联 ID
const sessionTraceId = sessionId + '-' + Date.now();
window.log.info('状态变更', { traceId: sessionTraceId, step: s.step });
```

---

### 层 3：评估与约束层

**做了什么**：

- `.harness/rules/agent-behavior.md` — 3 条软约束（不暴露模型名、不自动重试 Stitch、不主动 git commit）
- `config.yaml` — 页面数量限制为 4 页
- `app/workflow/orchestrator.py` — 页面数量硬编码为 4（`page_keys = ["page1", "page2", "page3", "page4"]`）
- `app/models.py` — `app_name` 限制 60 字符，`project_dir` 限制 40 字符 + 正则校验
- `app/workflow/state.py` — `project_dir` 合法性校验（仅允许字母数字下划线连字符）
- Codex 沙箱限制为 `workspace-write`（禁止网络和命令执行）

**缺失了什么**：

1. **无需求方案质量评估** — LLM 输出的 `RequirementPlan` 没有任何客观评分或校验（如页面数是否 4 个、每个页面是否有 2-4 个功能点）
2. **无设计稿质量检查** — Stitch 返回的 `screen_id` 没有验证是否真正可用
3. **无代码生成质量检查** — Codex 输出后没有自动检查（如文件是否生成、页面路由是否注册、是否有语法错误）
4. **无高危操作拦截** — 没有定义哪些操作是"高危"的（如删除 `.runtime/` 目录、覆盖已有项目等）
5. **无完成验收标准** — 工作流 `DONE` 状态没有定义什么算"完成"（如所有 4 个页面文件存在、路由注册正确等）
6. **约束规则分散** — 页面数 4 的约束散落在 `config.yaml`、`orchestrator.py`、`llm/client.py` 三处，不一致时容易遗漏

**如何实现**：

```yaml
# .harness/rules/quality-gates.yaml（新增）
quality_gates:
  analyze:
    - rule: "pages_count == 4"
      fail_action: "block"
      message: "需求方案必须包含 4 个页面"
    - rule: "all(p.features for p in pages)"
      fail_action: "warn"
      message: "每个页面应有至少 2 个功能点"

  design:
    - rule: "screen_ids_count == 4"
      fail_action: "block"
      message: "必须为 4 个页面各生成一个设计稿"

  codegen:
    - rule: "all_files_exist(['page1', 'page2', 'page3', 'page4'])"
      fail_action: "block"
      message: "4 个页面文件必须全部生成"
    - rule: "pages_json_has_all_routes"
      fail_action: "block"
      message: "pages.json 必须包含所有页面路由"

high_risk_operations:
  - action: "rm -rf .runtime"
    require: "manual_approval"
  - action: "delete session"
    require: "confirmation_dialog"
```

```python
# app/workflow/quality.py（新增）
def validate_requirement(req: RequirementPlan) -> list[str]:
    issues = []
    if len(req.pages) != 4:
        issues.append(f"页面数应为4，实际为{len(req.pages)}")
    for p in req.pages:
        if len(p.features) < 2:
            issues.append(f"{p.key} 功能点不足（至少2个）")
    return issues

def validate_codegen_output(project_path: str) -> list[str]:
    issues = []
    for key in ["page1", "page2", "page3", "page4"]:
        if not (project_path / "pages" / key / f"{key}.vue").exists():
            issues.append(f"缺失 {key}.vue")
    return issues
```

---

### 层 4：反馈层

**做了什么**：

- 无。（仅有前端的错误弹窗和重试按钮，但没有系统化的错误分析→规则改进闭环）

**缺失了什么**：

1. **无错误统计与分析** — 没有记录各类错误的频率、模式（如"Stitch API 超时 3 次"、"LLM JSON 解析失败 2 次"）
2. **无自动规则优化** — 错误发生后没有自动调整策略（如"Stitch 连续失败 3 次 → 建议切换到 Codex 直出路线"）
3. **无变更影响追踪** — `.harness/changes/` 只有 2 条记录，且没有记录每次变更对后续工作流的影响
4. **无 A/B 测试框架** — 无法对比不同提示词、不同风格的生成质量
5. **无用户反馈收集** — 没有让用户对生成结果评分/标记好坏的机制

**如何实现**：

```python
# app/workflow/feedback.py（新增）
class FeedbackCollector:
    def __init__(self):
        self.stats_path = ROOT_DIR / ".runtime" / "feedback.json"

    def record_error(self, step: str, error_type: str, detail: str):
        """记录错误到统计文件"""
        stats = self._load()
        key = f"{step}:{error_type}"
        stats.setdefault(key, {"count": 0, "last_seen": None, "samples": []})
        stats[key]["count"] += 1
        stats[key]["last_seen"] = datetime.now().isoformat()
        stats[key]["samples"].append(detail[:200])
        if len(stats[key]["samples"]) > 10:
            stats[key]["samples"] = stats[key]["samples"][-10:]
        self._save(stats)

    def suggest_improvement(self, step: str) -> str | None:
        """根据历史错误给出建议"""
        stats = self._load()
        stitch_errors = sum(
            v["count"] for k, v in stats.items()
            if k.startswith("design:") or k.startswith("stitch:")
        )
        if stitch_errors >= 3:
            return "建议：Stitch 设计多次失败，可尝试「跳过设计，直接代码生成」路线"
        return None
```

```markdown
# .harness/rules/feedback-rules.md（新增）
## 自动优化规则

| 触发条件 | 建议动作 |
|----------|----------|
| Stitch MCP 连续失败 ≥ 3 次 | 前端提示切换到 Codex 直出路线 |
| LLM JSON 解析失败 ≥ 2 次 | 降低 temperature 到 0.3 |
| Codex 超时 ≥ 1 次 | 提示拆分任务或简化需求 |
| 同一风格设计失败率 > 50% | 标记该风格为"不稳定" |
```

---

## 三、优先级路线图

| 优先级 | 层 | 内容 | 工作量 |
|--------|----|------|--------|
| 🔴 P0 | 层 3 | 代码生成后质量检查（文件存在、路由注册） | 小 |
| 🔴 P0 | 层 2 | 结构化操作日志（AuditEntry） | 中 |
| 🟠 P1 | 层 3 | 需求方案质量校验（4 页、功能点） | 小 |
| 🟠 P1 | 层 1 | 工具权限声明文档 | 小 |
| 🟡 P2 | 层 4 | 错误统计与改进建议 | 中 |
| 🟡 P2 | 层 3 | 质量门禁 YAML 配置 | 中 |
| 🟢 P3 | 层 4 | A/B 测试框架 | 大 |
| 🟢 P3 | 层 2 | 前端-后端日志关联 ID | 小 |