#!/bin/bash

# ============================================================
# workflow_uniapp 快速启动脚本
# ============================================================
# 使用方式：
#   1. 复制此脚本到项目根目录
#   2. chmod +x setup.sh
#   3. ./setup.sh
# ============================================================

set -e  # 遇到错误立即退出

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   workflow_uniapp 快速启动${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 步骤 1：检查 Python 版本
echo -e "${YELLOW}[1/6] 检查 Python 版本...${NC}"
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "发现 Python: $PYTHON_VERSION"

if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
    echo -e "${RED}错误: 需要 Python 3.11+${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python 版本检查通过${NC}"
echo ""

# 步骤 2：创建虚拟环境
echo -e "${YELLOW}[2/6] 创建虚拟环境...${NC}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo -e "${GREEN}✓ 虚拟环境创建完成${NC}"
else
    echo -e "${GREEN}✓ 虚拟环境已存在，跳过${NC}"
fi
echo ""

# 步骤 3：激活虚拟环境
echo -e "${YELLOW}[3/6] 激活虚拟环境...${NC}"
source .venv/bin/activate
echo -e "${GREEN}✓ 虚拟环境已激活${NC}"
echo ""

# 步骤 4：安装依赖
echo -e "${YELLOW}[4/6] 安装项目依赖（这可能需要 2-5 分钟）...${NC}"
pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements.txt
echo -e "${GREEN}✓ 依赖安装完成${NC}"
echo ""

# 步骤 5：验证配置
echo -e "${YELLOW}[5/6] 验证 LLM 配置...${NC}"
cat << 'EOF'
当前 LLM 配置：
EOF
grep "LLM_" .env | grep -v "^#"
echo -e "${GREEN}✓ 配置验证完成${NC}"
echo ""

# 步骤 6：提示编辑 .env
echo -e "${YELLOW}[6/6] 编辑环境变量...${NC}"
cat << 'EOF'

【重要】需要填入你的 API Key：

1. 打开 .env 文件
2. 找到这一行：LLM_API_KEY=your-api-key
3. 替换为你的真实 API Key，例如：
   LLM_API_KEY=sk-xxxxxxxxxxxxxxxxxxxx

编辑命令：
  nano .env

保存方法（nano）：
  Ctrl+X  → Y  → Enter
EOF

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}准备完毕！${NC}${BLUE}执行下一步：${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "1. 编辑 .env 文件填入 API Key："
echo -e "   ${YELLOW}nano .env${NC}"
echo ""
echo -e "2. 启动服务："
echo -e "   ${YELLOW}python run.py${NC}"
echo ""
echo -e "3. 打开浏览器访问："
echo -e "   本机：${YELLOW}http://127.0.0.1:8765${NC}"
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
if [ -n "$LAN_IP" ]; then
  echo -e "   局域网：${YELLOW}http://${LAN_IP}:8765${NC}"
fi
echo ""
echo -e "${BLUE}========================================${NC}"
