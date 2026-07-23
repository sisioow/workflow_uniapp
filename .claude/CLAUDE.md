# 项目上下文（Agent 入口）

## 项目概览

- **项目名称**：workflow_uniapp — App 生成本地工作流
- **业务域**：工具类 uni-app 应用自动化生成
- **技术栈**：Python 3.11+ / FastAPI / Stitch MCP / Codex CLI

## Harness 约定

1. **主编排 Agent**：`workflow-agent`（见 `.harness/agents/workflow-agent.md`）
2. **规范总纲**：`.harness/wiki/ARCHITECTURE.md`
3. **规则优先级**：`.harness/rules/` > 本文件 > 对话临时指令
4. **变更归档**：每次交付须在 `.harness/changes/<id>/` 留下 `CHANGE.md`

## 代码目录

| 目录 | 职责 |
|------|------|
| `app/` | Python 工作流引擎、Web API、Stitch/LLM |
| `backend-java/` | 可选 Java 后端 |
| `config.yaml` | UI 风格等静态配置 |
| `.runtime/` | 运行时状态（gitignore） |

## 常用命令

```bash
./setup.sh && ./start.sh python
# 或：source .venv/bin/activate && python run.py
```
