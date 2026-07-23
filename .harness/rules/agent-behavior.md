# Agent 行为与文案规范

## 用户可见界面

- 不展示具体模型名称、厂商/API、「大模型」等实现细节
- 功能说明用产品语言（如「智能分析」「自动生成」）

## 工具使用

- Stitch `generate_screen_from_text` **超时后不自动重试**（生成可能仍在后台）
- 对 `Request contains an invalid argument` 等瞬时拒识，允许有限次自动重试（默认 2 次）
- 能自己执行的命令不甩给用户

## 禁止

- 未经要求主动 git commit / push
- 泄露 `.env` 或本地密钥
