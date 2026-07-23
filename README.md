# App 生成本地工作流

将 n8n 工作流重构为本地 Web 应用：**需求分析 → 审核方案 → UI 风格 → Stitch 设计（可跳过）→ 选择方案 → 代码生成 → 整理测试发布**。

完整说明见 **[docs/工作流说明.md](docs/工作流说明.md)**；性能与功能优化见 **[docs/优化分析.md](docs/优化分析.md)**。

## 快速开始

```bash
./setup.sh                  # 首次：创建 venv、安装依赖
cp local_config.example.yaml local_config.yaml
# 编辑 local_config.yaml：至少填写 llm.api_key、stitch.api_key、uniapp 路径
./start.sh python           # 启动 Web（默认 0.0.0.0:8765）
# 或：source .venv/bin/activate && python run.py
```

| 访问 | 地址 |
|------|------|
| 本机 | http://127.0.0.1:8765 |
| 局域网 | http://\<本机 IP\>:8765 |

## 核心能力

- **8 步向导**：应用信息 → 需求分析 → 审核 → 风格 → 设计 → 选方案 → 代码生成 → 整理/测试/发布
- **侧栏项目列表**：组号筛选、创建时间、删除项目（会终止相关进程）
- **设计路线**：Stitch MCP 设计 / 跳过设计直接进选方案
- **代码生成**：Claude Code（默认）/ Codex / Cursor Agent，可暂停、补充对话
- **第 8 步**：README 功能清单、HBuilderX H5 测试（独立端口）、Git 发布、用 Cursor 打开项目

## 配置要点

主配置：**`local_config.yaml`**（密钥与路径，勿提交仓库）。静态风格列表：**`config.yaml`**。

| 区块 | 说明 |
|------|------|
| `llm.*` | OpenAI 兼容接口与模型 |
| `stitch.*` | Google Stitch MCP |
| `uniapp.*` | 模板目录与输出目录 |
| `codegen.*` | Claude / Codex / Cursor CLI |
| `hbuilderx.*` | 第 8 步 H5 测试 |
| `publish.*` | 默认 Git 仓库与 base 分支 |
| `server.*` | 监听 host / port |

兼容：无 `local_config.yaml` 时可使用 `.env`（见 `.env.example`）；**两者并存时以 yaml 为准**。

## 目录结构

```
workflow_uniapp/
├── app/
│   ├── main.py              # FastAPI、SSE、后台任务
│   ├── llm/                 # 需求分析
│   ├── stitch/              # Stitch MCP 客户端
│   ├── hbuilderx/           # H5 编译测试
│   ├── publish/             # Git 发布
│   ├── ide/                 # Cursor 打开项目等
│   ├── workflow/            # 编排、状态、取消
│   └── web/                 # 前端向导
├── docs/
│   ├── 工作流说明.md
│   └── 优化分析.md
├── .harness/                # Agent 规范、wiki、变更记录
├── .runtime/sessions/       # 会话 state.json
├── local_config.yaml        # 本地密钥（gitignore）
├── config.yaml              # UI 风格等静态配置
└── run.py / cli.py
```

## 常用命令

以下默认在项目根目录执行；Python 命令建议先 `source .venv/bin/activate`，或使用 `.venv/bin/python`。

### 安装与配置

```bash
./setup.sh                                          # 创建 .venv、安装 requirements.txt
cp local_config.example.yaml local_config.yaml      # 复制本地配置（首次）
# 编辑 local_config.yaml：llm / stitch / uniapp / codegen / hbuilderx / publish / server
```

### 启停服务（推荐）

```bash
./start.sh python     # 启动 Python Web（端口 8765，后台 + 日志）
./start.sh java       # 启动可选 Java 后端（端口 8766）
./start.sh all        # 同时启动 Python + Java
./start.sh status     # 查看端口、健康检查、Codex CLI 等
./start.sh stop       # 停止全部服务
```

### 前台启动（调试）

```bash
source .venv/bin/activate
python run.py         # 前台跑 FastAPI，日志打在终端
```

### 浏览器与健康检查

```bash
open "http://127.0.0.1:8765"                        # 打开向导首页
open "http://127.0.0.1:8765/?session=<session_id>"  # 恢复指定会话
curl -s http://127.0.0.1:8765/api/health | python3 -m json.tool
curl -s http://127.0.0.1:8765/api/config | python3 -m json.tool
curl -s http://127.0.0.1:8765/api/sessions | python3 -m json.tool
```

### CLI 工作流（无 UI）

```bash
# 查看帮助
python cli.py -h
python cli.py start -h

# 1) 创建会话并做需求分析
python cli.py start "应用名" project_dir [--group-no g1]

# 2) 为会话跑 Stitch 设计（风格 id 见 config.yaml）
python cli.py design <session_id> --styles ai-native,glacier [--colors "紫色；蓝色"]

# 3) 选方案并生成代码（工具：claude | codex | cursor）
python cli.py generate <session_id> [--plan 0] [--framework vue] [--tool claude]

# 4) 查看会话状态
python cli.py status <session_id>
```

### 会话与日志

```bash
ls .runtime/sessions/                    # 会话目录列表
ls .runtime/sessions/<session_id>/       # 含 state.json、stitch/ 等
tail -f .runtime/logs/python.log         # start.sh 后台启动时的服务日志
cat .runtime/logs/python.pid             # 后台 Python PID
```

### 常用 HTTP API（curl 示例）

```bash
SID=<session_id>

# 查会话
curl -s "http://127.0.0.1:8765/api/session/$SID" | python3 -m json.tool

# 暂停进行中的设计 / 代码生成
curl -s -X POST "http://127.0.0.1:8765/api/session/$SID/cancel"

# 跳过设计 → 进入选方案
curl -s -X POST "http://127.0.0.1:8765/api/session/$SID/skip-design"

# 第 8 步：README / H5 测试 / 关端口 / Cursor 打开 / 发布
curl -s -X POST "http://127.0.0.1:8765/api/session/$SID/prepare-readme"
curl -s -X POST "http://127.0.0.1:8765/api/session/$SID/run-h5-test"
curl -s -X POST "http://127.0.0.1:8765/api/session/$SID/stop-h5-test"
curl -s -X POST "http://127.0.0.1:8765/api/session/$SID/open-in-cursor"
curl -s -X POST "http://127.0.0.1:8765/api/session/$SID/publish" \
  -H "Content-Type: application/json" \
  -d '{"repo_path":"/path/to/uniapp","branch":"g1/project_dir"}'

# 删除会话（会终止相关进程）
curl -s -X DELETE "http://127.0.0.1:8765/api/session/$SID"
```

### 本机依赖自检（代码生成 / 测试）

```bash
which claude && claude --version     # Claude Code
which codex && codex --version       # Codex CLI
which agent || which cursor          # Cursor Agent / Cursor IDE
which cli || ls /Applications/HBuilderX.app   # HBuilderX（第 8 步测试）
```

完整 API 列表见 [docs/工作流说明.md](docs/工作流说明.md) 第 6 节。

## 相关文档

| 文档 | 内容 |
|------|------|
| [docs/工作流说明.md](docs/工作流说明.md) | 架构、流程、API、环境 |
| [docs/优化分析.md](docs/优化分析.md) | 性能与功能优化建议 |
| [.harness/wiki/ARCHITECTURE.md](.harness/wiki/ARCHITECTURE.md) | 架构备忘 |
| [.harness/wiki/STITCH_MCP_SPEC.md](.harness/wiki/STITCH_MCP_SPEC.md) | Stitch 集成规范 |

可选：`backend-java/`（Spring Boot，端口 8766）与 Python 会话数据不互通。
