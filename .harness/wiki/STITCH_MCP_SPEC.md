# Stitch MCP 集成规范

## 概述

本文档规范了 workflow_uniapp 中 Stitch MCP（Model Context Protocol）的使用方式。

---

## 1. 项目名称规范

### 命名格式
```
{应用名称}+{ui风格id}
```

### 示例
- 应用名称：`记账本`，UI 风格 ID：`glassmorphism`
  → 项目名称：`记账本+glassmorphism`

- 应用名称：`截图工具`，UI 风格 ID：`neon-tokyo`
  → 项目名称：`截图工具+neon-tokyo`

### 实现位置
`app/workflow/orchestrator.py` - `run_design()` 函数

```python
project_title = f"{state.app_name}+{style_id}"
project_id = stitch.create_project(project_title)
```

---

## 2. 设计提示词规范

### 提示词构成
产品方案 + UI 风格，采用分块式结构化提示。

### 提示词格式

```
【生成要求】
请一次性生成恰好 4 个独立移动端页面设计（page1~page4），不要只生成单个页面；一次请求完成全部 4 屏。
…

【产品方案】
产品定位：…
页面清单：
1. page1「…」
2. page2「…」
3. page3「…」
4. page4「…」

【UI 风格要求】
…

【设计约束】
- 必须产出 4 个页面，顺序对应 page1、page2、page3、page4
```

每个风格只调用一次 `generate_screen_from_text`；实现见 `build_design_prompt()` / `generate_screens()`。

---

## 3. Stitch MCP 调用流程

### 流程图

```
工作流开始
  ↓
[选择 UI 风格]
  ↓
[运行设计步骤 run_design_step]
  ├─ 初始化 Stitch 客户端
  ├─ For each 选中的风格：
  │  ├─ 创建项目（项目名：应用名+风格id）
  │  ├─ For each 页面（page1~page4）：
  │  │  ├─ 构建提示词（产品方案 + UI 风格）
  │  │  ├─ 调用 generate_screen_from_text
  │  │  └─ 保存 screen_id
  │  └─ 标记任务为 done
  ├─ 处理错误和统计结果
  └─ 进入 SELECT_PLAN 步骤
```

### 关键 API 调用

#### 1. 初始化
```python
stitch = StitchMcpClient()
stitch.initialize()
```

#### 2. 创建项目
```python
project_title = f"{app_name}+{style_id}"
project_id = stitch.create_project(project_title)
```

**返回**：项目 ID（字符串）

#### 3. 生成设计稿
```python
prompt = build_screen_prompt(
    requirement,
    page_key,    # "page1", "page2", "page3", "page4"
    style_name,  # UI 风格名称
    color_hint   # 可选的颜色建议
)
screen_id = stitch.generate_screen(
    project_id,
    prompt,
    device_type="MOBILE"  # 固定为移动设备
)
```

**返回**：屏幕 ID（字符串），用于后续获取 HTML/PNG

#### 4. 获取设计稿（在 Codex 阶段）
```python
screen_data = stitch.get_screen(project_id, screen_id)
# screen_data 包含 HTML、PNG 等设计输出
```

---

## 4. 错误处理

### 异常类型

| 错误 | 原因 | 处理方案 |
|------|------|--------|
| `StitchMcpError("未配置 STITCH_API_KEY")` | .env 中未填写 API Key | 填入 STITCH_API_KEY |
| `StitchMcpError(...)` | API 调用失败（超时、配额、网络） | 记录失败，继续处理其他风格 |
| `generate_screen` 未返回 screen_id | 响应格式异常 | 捕获异常，标记任务为 failed |

### 当前策略

- 单个风格失败 **不自动重试**（协议要求）
- 继续处理其他风格，最后统计成功/失败数
- 如果所有风格失败，工作流进入 ERROR 步骤

```python
except StitchMcpError as exc:
    task.status = "failed"
    task.error = str(exc)
    state.append_log(f"风格「{style_name}」失败：{exc}")
```

---

## 5. 文件组织

| 文件 | 职责 |
|------|------|
| `app/stitch/mcp_client.py` | Stitch MCP HTTP 客户端（JSON-RPC） |
| `app/workflow/orchestrator.py` | 工作流编排，调用 Stitch |
| `app/models.py` | 数据模型（RequirementPlan、StyleTask 等） |
| `app/config.py` | 配置读取（STITCH_API_KEY、STITCH_MCP_URL） |

---

## 6. 配置说明

### 环境变量（.env）

```bash
# Google Stitch MCP
STITCH_API_KEY=your-stitch-api-key
STITCH_MCP_URL=https://stitch.googleapis.com/mcp
```

### 默认配置（app/config.py）

```python
class Settings(BaseSettings):
    stitch_api_key: str = ""
    stitch_mcp_url: str = "https://stitch.googleapis.com/mcp"
```

---

## 7. 调试建议

### 查看完整提示词

在 `build_screen_prompt()` 中添加日志：

```python
logger.info(f"Stitch 提示词：\n{prompt_parts}")
```

### 验证项目名称

在 `run_design()` 中检查：

```python
state.append_log(f"创建项目：{project_title}")
```

### 检查 API 配置

```bash
# 终端验证
cat .env | grep STITCH
# 应该显示你的 API Key 和 MCP URL
```

---

## 8. 参考链接

- [Stitch 官方文档](https://stitch.withgoogle.com/docs/mcp/setup)
- [MCP 协议规范](https://modelcontextprotocol.io/)

---

## 修改记录

| 日期 | 修改内容 |
|------|--------|
| 2026-07-09 | 初版：统一项目名称规范为 `应用名+风格id`，改进提示词为结构化格式 |
