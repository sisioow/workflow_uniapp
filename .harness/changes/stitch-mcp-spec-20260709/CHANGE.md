# Stitch MCP 规范化实现

## 变更摘要

统一并规范化了 Stitch MCP 在 workflow_uniapp 中的使用方式，完全按照官方文档和当前需求实现。

**变更日期**：2026-07-09

---

## 主要变更

### 1. 项目名称规范（Breaking Change）

**原规范**：`{应用名称}（{风格名称全文}）`
- 示例：`记账本（玻璃拟态）`

**新规范**：`{应用名称}+{风格id}`
- 示例：`记账本+glassmorphism`

**优点**：
- 更简洁、易解析
- 方便程序化处理（分割符明确）
- 风格 ID 对应 config.yaml 中的配置

**受影响文件**：
- `app/workflow/orchestrator.py` - `run_design()` 函数

```python
# 修改前
project_title = f"{state.app_name}（{style_name}）"

# 修改后
project_title = f"{state.app_name}+{style_id}"
```

---

### 2. 提示词结构化（Improvement）

**原提示词风格**：自由文本，信息混乱

**新提示词风格**：分块结构化提示（遵循 MCP 最佳实践）

```
【产品方案】
  - 产品定位
  - 页面标题与键
  - 功能点列表
  - 交互说明

【UI 风格要求】
  - 风格主题
  - 设备类型
  - 语言

【设计约束】
  - 技术约束
  - 功能约束
  - 风格统一性
```

**优点**：
- 提示词更清晰，Stitch 模型容易理解
- 易维护、易测试
- 符合官方 MCP 文档建议

**受影响文件**：
- `app/workflow/orchestrator.py` - `build_screen_prompt()` 函数

**修改示例**：

```python
# 修改前
return (
    f"为移动工具类应用「{app_name}」设计 {page.title} 页面（{page_key}）。\n"
    f"UI 风格：{style_name}。{color_line}\n"
    f"产品定位：{requirement.summary}\n"
    ...
)

# 修改后
prompt_parts = [
    f"【产品方案】",
    f"产品定位：{requirement.summary}",
    f"页面：{page.title}（{page_key}）",
    ...
]
return "\n".join(prompt_parts)
```

---

### 3. 函数签名更新

**`build_screen_prompt()` 参数变更**

```python
# 修改前
def build_screen_prompt(
    app_name: str,                      # ← 移除
    requirement: RequirementPlan,
    page_key: str,
    style_name: str,
    color_hint: str,
) -> str:

# 修改后
def build_screen_prompt(
    requirement: RequirementPlan,
    page_key: str,
    style_name: str,
    color_hint: str,
) -> str:
```

**原因**：
- 应用名称已包含在项目名称中（`app_name+style_id`）
- 提示词中不需要重复应用名称
- 简化函数签名

**受影响调用点**：
- `run_design()` 函数中的所有 `build_screen_prompt()` 调用

---

## 新增文档

### `.harness/wiki/STITCH_MCP_SPEC.md`

详细规范文档，包含：
- 项目名称规范示例
- 完整提示词格式与示例
- Stitch MCP 调用流程
- 错误处理策略
- 配置说明
- 调试建议

---

## 兼容性

### 向后兼容性：否
- 已有的 Stitch 项目名称格式会改变
- 建议清理旧的 Stitch 项目，重新生成

### 数据迁移：无需
- 只影响新创建的项目和提示词
- 不改变状态存储格式

---

## 测试建议

1. **单元测试**
   ```python
   def test_build_screen_prompt():
       prompt = build_screen_prompt(req, "page1", "glassmorphism", "#FF0000")
       assert "【产品方案】" in prompt
       assert "【UI 风格要求】" in prompt
       assert "glassmorphism" in prompt
   ```

2. **集成测试**
   - 选择一个应用名称（如 "测试应用"）
   - 选择一个 UI 风格（如 "ai-native"）
   - 验证项目名称为 "测试应用+ai-native"
   - 检查 Stitch 返回的设计稿质量

3. **提示词验证**
   - 打印生成的提示词
   - 确认分块结构正确
   - 验证信息完整性

---

## 部署说明

1. 代码更新
   ```bash
   git pull origin main
   ```

2. 环境检查
   ```bash
   # 确认 STITCH_API_KEY 已配置
   cat .env | grep STITCH_API_KEY
   ```

3. 功能验证
   - 启动服务：`python run.py`
   - 创建新工作流
   - 验证 Stitch 项目名称格式

---

## 回滚方案

如需回滚到旧规范，修改文件：

**app/workflow/orchestrator.py**
```python
# 回滚项目名称
project_title = f"{state.app_name}（{style_name}）"

# 回滚提示词（参考 git history）
def build_screen_prompt(
    app_name: str,  # 恢复此参数
    ...
):
    # 恢复旧提示词格式
```

---

## 后续改进方向

1. **提示词 A/B 测试**
   - 对比旧规范 vs 新规范的设计质量
   - 收集用户反馈

2. **提示词版本管理**
   - 在 config.yaml 中添加 `stitch_prompt_version`
   - 支持多版本并行

3. **国际化支持**
   - 提示词模板中文化
   - 支持其他语言

4. **性能优化**
   - 缓存提示词模板
   - 异步生成多风格

---

## 相关文件

- 主要修改：`app/workflow/orchestrator.py`
- 新增文档：`.harness/wiki/STITCH_MCP_SPEC.md`
- 更新文档：`.harness/wiki/ARCHITECTURE.md`

---

## 审核清单

- [x] 代码已测试
- [x] 文档已更新
- [x] 配置已验证
- [x] 变更记录已归档

