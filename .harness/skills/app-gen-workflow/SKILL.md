---
name: app-gen-workflow
description: >-
  Orchestrates app requirement analysis, Stitch UI design, and Claude/Codex/Cursor
  code generation for uni-app projects. Use when running the local workflow pipeline.
disable-model-invocation: true
---

# App 生成工作流

## 步骤（前端 8 步）

1. **input / analyze** — 应用信息与 LLM 需求分析（三套不同方向，每套 4 页完整需求）
   - 需求设计遵循 **pm-brainstorm**（JTBD/HMW 发散 → 三套方向收敛；每套须功能完整，禁止小品 Demo）
2. **review_requirement** — 审核三套方向方案，可编辑 / 带提示词重试后确认
3. **select_styles** — 选择 1~19 种风格；路线：Stitch 或 **跳过设计**
4. **design** — Stitch 并行设计（每风格 4 屏）；可暂停
5. **select_plan** — 选最终方案 + **代码生成工具** + 框架；可「去查看」Stitch
6. **init_project / codegen** — 拷贝模板 + CLI 生成；补充对话
7. **evaluate** — README、HBuilderX H5 测试、Git 发布、Cursor 打开项目
8. **done** — 完成

## 设计路线

| 路线 | 行为 |
|------|------|
| Stitch | 第 4→5 步设计，再第 6 步选方案 |
| 跳过设计 | 第 4 步提交后直达第 6 步（`skip_design`） |

## 代码生成工具

在第 **6** 步选择：`claude`（默认）| `codex` | `cursor`

## 相关 Skills

| Skill | 用途 |
|-------|------|
| `pm-brainstorm` | 产品经理头脑风暴；注入需求分析 LLM（`requirement-prompt.md`） |
| `app-gen-workflow` | 本流水线总览（本文件） |

## 参考

- 用户文档：`docs/工作流说明.md`
- 优化分析：`docs/优化分析.md`
- 架构：`.harness/wiki/ARCHITECTURE.md`
- Stitch：`.harness/wiki/STITCH_MCP_SPEC.md`

## 待办

- Open Design 双引擎集成（见 `docs/优化分析.md`）
