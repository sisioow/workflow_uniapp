#!/bin/bash
# ═══════════════════════════════════════════════
# workflow_uniapp 一键启动脚本
# 用法：./start.sh [python|java|all]
# ═══════════════════════════════════════════════

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_DIR="$PROJECT_DIR"
JAVA_DIR="$PROJECT_DIR/backend-java"
LOG_DIR="$PROJECT_DIR/.runtime/logs"
mkdir -p "$LOG_DIR"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

print_ok()   { echo -e "${GREEN}✅ $1${NC}"; }
print_info() { echo -e "${CYAN}📌 $1${NC}"; }
print_warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_err()  { echo -e "${RED}❌ $1${NC}"; }

# ── 端口定义 ──
PYTHON_PORT=8765
JAVA_PORT=8766
MYSQL_PORT=3306

# ── 杀旧进程 ──
kill_port() {
  local port=$1
  local pid=$(lsof -ti :$port 2>/dev/null || true)
  if [ -n "$pid" ]; then
    echo "  端口 $port 被占用 (PID $pid)，正在清理…"
    kill -9 $pid 2>/dev/null || true
    sleep 1
  fi
}

check_port() {
  local port=$1
  local name=$2
  if lsof -i :$port >/dev/null 2>&1; then
    print_ok "$name 已在端口 $port 运行"
    return 0
  else
    print_err "$name 未在端口 $port 运行"
    return 1
  fi
}

# ═══════════════════════════════════════════════
# Python 后端
# ═══════════════════════════════════════════════
start_python() {
  print_info "启动 Python 后端 (FastAPI)…"
  kill_port $PYTHON_PORT

  cd "$PYTHON_DIR"

  # 检查 venv
  if [ ! -d ".venv" ]; then
    print_warn "未找到 .venv，正在创建…"
    python3 -m venv .venv
  fi

  # 安装依赖
  if ! .venv/bin/pip list 2>/dev/null | grep -q fastapi; then
    print_info "安装 Python 依赖…"
    .venv/bin/pip install -r requirements.txt -q
  fi

  # 检查本地配置（优先 local_config.yaml）
  if [ ! -f "local_config.yaml" ]; then
    if [ -f "local_config.example.yaml" ]; then
      print_warn "local_config.yaml 不存在，从 local_config.example.yaml 复制…"
      cp local_config.example.yaml local_config.yaml
      print_warn "请编辑 local_config.yaml 填入 api_key / base_url 后重新运行"
      exit 1
    fi
    if [ ! -f ".env" ]; then
      print_warn ".env 不存在，从 .env.example 复制…"
      cp .env.example .env
      print_warn "请编辑 .env 或创建 local_config.yaml 后重新运行"
      exit 1
    fi
  fi

  # 启动（直接用 venv python + disown，避免脚本退出后后台进程被收走）
  nohup "$PYTHON_DIR/.venv/bin/python" "$PYTHON_DIR/run.py" > "$LOG_DIR/python.log" 2>&1 &
  PYTHON_PID=$!
  disown "$PYTHON_PID" 2>/dev/null || true
  echo $PYTHON_PID > "$LOG_DIR/python.pid"
  print_info "Python PID: $PYTHON_PID, 等待启动…"

  # 等待就绪
  for i in $(seq 1 15); do
    if curl -s "http://127.0.0.1:$PYTHON_PORT/api/health" >/dev/null 2>&1; then
      print_ok "Python 后端就绪"
      echo "  本机：    http://127.0.0.1:$PYTHON_PORT"
      LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
      if [ -n "$LAN_IP" ]; then
        echo "  局域网：  http://$LAN_IP:$PYTHON_PORT"
      fi
      return 0
    fi
    sleep 1
  done
  print_err "Python 后端启动超时，查看日志: $LOG_DIR/python.log"
  return 1
}

# ═══════════════════════════════════════════════
# Java 后端
# ═══════════════════════════════════════════════
start_java() {
  print_info "启动 Java 后端 (Spring Boot)…"
  kill_port $JAVA_PORT

  cd "$JAVA_DIR"

  # 检查 Maven
  if ! command -v mvn &>/dev/null; then
    print_err "未安装 Maven (mvn)。请先安装: brew install maven"
    return 1
  fi

  # 检查 Java 17+
  if ! java -version 2>&1 | grep -qE 'version "17|version "2[0-9]'; then
    print_err "需要 Java 17+，当前: $(java -version 2>&1 | head -1)"
    return 1
  fi

  # 检查 MySQL（可选，可降级 H2）
  if ! nc -z 127.0.0.1 $MYSQL_PORT 2>/dev/null; then
    print_warn "MySQL 未运行（端口 $MYSQL_PORT），Java 后端将使用 H2 内存数据库"
    print_warn "如需 MySQL: brew services start mysql"
  else
    print_ok "MySQL 在端口 $MYSQL_PORT 运行"
    # 建库建表
    mysql -u root -proot -e "CREATE DATABASE IF NOT EXISTS workflow_uniapp CHARACTER SET utf8mb4;" 2>/dev/null || true
    mysql -u root -proot workflow_uniapp < src/main/resources/schema.sql 2>/dev/null || true
    print_ok "MySQL 数据库已初始化"
  fi

  # 编译 + 启动
  print_info "编译 Java 工程（首次较慢）…"
  nohup mvn spring-boot:run > "$LOG_DIR/java.log" 2>&1 &
  JAVA_PID=$!
  echo $JAVA_PID > "$LOG_DIR/java.pid"
  print_info "Java PID: $JAVA_PID, 等待启动…"

  # 等待就绪（Spring Boot 启动较慢）
  for i in $(seq 1 60); do
    if curl -s "http://127.0.0.1:$JAVA_PORT/api/health" >/dev/null 2>&1; then
      print_ok "Java 后端就绪: http://127.0.0.1:$JAVA_PORT"
      return 0
    fi
    sleep 2
  done
  print_err "Java 后端启动超时，查看日志: $LOG_DIR/java.log"
  print_warn "可能原因: 1) 首次编译下载依赖较慢 2) MySQL 连接失败（检查密码）"
  return 1
}

# ═══════════════════════════════════════════════
# 停止
# ═══════════════════════════════════════════════
stop_all() {
  print_info "停止所有服务…"
  for pidfile in "$LOG_DIR/python.pid" "$LOG_DIR/java.pid"; do
    if [ -f "$pidfile" ]; then
      pid=$(cat "$pidfile")
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        print_ok "已停止 PID $pid"
      fi
      rm -f "$pidfile"
    fi
  done
  kill_port $PYTHON_PORT
  kill_port $JAVA_PORT
  print_ok "所有服务已停止"
}

# ═══════════════════════════════════════════════
# 状态
# ═══════════════════════════════════════════════
status_all() {
  echo ""
  echo "═══════════════════════════════════════════"
  echo "  workflow_uniapp 服务状态"
  echo "═══════════════════════════════════════════"
  echo ""

  # Python
  if check_port $PYTHON_PORT "Python (FastAPI)"; then
    echo "  本机：http://127.0.0.1:$PYTHON_PORT"
    LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
    if [ -n "$LAN_IP" ]; then
      echo "  局域网：http://$LAN_IP:$PYTHON_PORT"
    fi
    curl -s "http://127.0.0.1:$PYTHON_PORT/api/health" | python3 -m json.tool 2>/dev/null || true
  fi
  echo ""

  # Java
  if check_port $JAVA_PORT "Java (Spring Boot)"; then
    echo "  URL: http://127.0.0.1:$JAVA_PORT"
    curl -s "http://127.0.0.1:$JAVA_PORT/api/health" | python3 -m json.tool 2>/dev/null || true
  fi
  echo ""

  # MySQL
  if nc -z 127.0.0.1 $MYSQL_PORT 2>/dev/null; then
    print_ok "MySQL 在端口 $MYSQL_PORT"
  else
    print_warn "MySQL 未运行"
  fi
  echo ""

  # Codex CLI
  if command -v codex &>/dev/null; then
    print_ok "Codex CLI: $(codex --version 2>&1)"
  else
    print_warn "Codex CLI 未安装"
  fi

  echo ""
}

# ═══════════════════════════════════════════════
# 主逻辑
# ═══════════════════════════════════════════════
case "${1:-all}" in
  python)
    start_python
    ;;
  java)
    start_java
    ;;
  all)
    start_python
    echo ""
    start_java
    echo ""
    status_all
    ;;
  stop)
    stop_all
    ;;
  status)
    status_all
    ;;
  *)
    echo "用法: $0 {python|java|all|stop|status}"
    echo ""
    echo "  python  启动 Python FastAPI 后端 (端口 $PYTHON_PORT)"
    echo "  java    启动 Java Spring Boot 后端 (端口 $JAVA_PORT)"
    echo "  all     启动全部"
    echo "  stop    停止全部"
    echo "  status  查看运行状态"
    exit 1
    ;;
esac
