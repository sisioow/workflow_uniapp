/**
 * 前端日志系统
 *
 * 功能：
 * - 记录所有 API 调用和响应
 * - 记录工作流状态变化
 * - 记录 SSE 事件
 * - 记录 JavaScript 错误
 * - 支持日志筛选和导出
 */

class DebugLogger {
  constructor() {
    this.logs = [];
    this.maxLogs = 500;
    this.filters = {
      info: true,
      debug: true,
      warn: true,
      error: true,
    };
    this.init();
  }

  init() {
    // 拦截 fetch 请求
    const originalFetch = window.fetch;
    window.fetch = function(...args) {  // ✅ 使用普通函数，不是箭头函数
      const [resource, config] = args;
      const method = (config?.method || 'GET').toUpperCase();

      debugLogger.log('API', `[${method}] ${resource}`, {
        level: 'api',
        config: config
      });

      return originalFetch.apply(this, args)  // ✅ this 正确指向调用者
        .then((response) => {
          const status = response.status;
          const statusText = response.statusText;
          debugLogger.log('API', `[${method}] ${resource} → ${status} ${statusText}`, {
            level: 'api',
            status
          });
          return response;
        })
        .catch((error) => {
          debugLogger.log('API', `[${method}] ${resource} → ERROR: ${error.message}`, {
            level: 'error',
            error: error.message
          });
          throw error;
        });
    };

    // 拦截 console 输出
    const originalLog = console.log;
    console.log = function(...args) {  // ✅ 使用普通函数
      debugLogger.log('CONSOLE', args.join(' '), { level: 'info' });
      originalLog.apply(console, args);  // ✅ 显式传入 console
    };

    const originalWarn = console.warn;
    console.warn = function(...args) {  // ✅ 使用普通函数
      debugLogger.log('CONSOLE', args.join(' '), { level: 'warn' });
      originalWarn.apply(console, args);  // ✅ 显式传入 console
    };

    const originalError = console.error;
    console.error = function(...args) {  // ✅ 使用普通函数
      debugLogger.log('CONSOLE', args.join(' '), { level: 'error' });
      originalError.apply(console, args);  // ✅ 显式传入 console
    };

    // 全局错误处理
    window.addEventListener('error', (event) => {
      this.log('ERROR', `${event.message} at ${event.filename}:${event.lineno}`, {
        level: 'error',
        error: event.error
      });
    });

    // 未捕获的 Promise 拒绝
    window.addEventListener('unhandledrejection', (event) => {
      this.log('ERROR', `Unhandled rejection: ${event.reason}`, {
        level: 'error',
        error: event.reason
      });
    });

    // 设置 UI 事件监听
    this.setupUI();
  }

  setupUI() {
    const btnShow = document.getElementById('btnShowDebugLog');
    const btnClose = document.getElementById('btnCloseLogs');
    const btnClear = document.getElementById('btnClearLogs');
    const btnCopy = document.getElementById('btnCopyLogs');

    if (btnShow) {
      btnShow.addEventListener('click', () => this.togglePanel());
    }

    if (btnClose) {
      btnClose.addEventListener('click', () => this.closePanel());
    }

    if (btnClear) {
      btnClear.addEventListener('click', () => this.clearLogs());
    }

    if (btnCopy) {
      btnCopy.addEventListener('click', () => this.copyLogs());
    }

    // 设置过滤器
    document.getElementById('filterInfo')?.addEventListener('change', (e) => {
      this.filters.info = e.target.checked;
      this.render();
    });

    document.getElementById('filterDebug')?.addEventListener('change', (e) => {
      this.filters.debug = e.target.checked;
      this.render();
    });

    document.getElementById('filterWarn')?.addEventListener('change', (e) => {
      this.filters.warn = e.target.checked;
      this.render();
    });

    document.getElementById('filterError')?.addEventListener('change', (e) => {
      this.filters.error = e.target.checked;
      this.render();
    });

    // 初始日志
    this.log('SYSTEM', '✅ 调试日志系统已启动', { level: 'info' });
  }

  log(category, message, options = {}) {
    const { level = 'info', ...extra } = options;

    const timestamp = new Date().toLocaleTimeString('zh-CN', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      fractionalSecondDigits: 3
    });

    const entry = {
      timestamp,
      category,
      message,
      level,
      ...extra
    };

    this.logs.push(entry);

    // 限制日志数量
    if (this.logs.length > this.maxLogs) {
      this.logs.shift();
    }

    this.render();
  }

  render() {
    const logEl = document.getElementById('debugLog');
    if (!logEl) return;

    const filtered = this.logs.filter((log) => {
      return this.filters[log.level] !== false;
    });

    logEl.innerHTML = filtered
      .map((log) => {
        const className = `log-${log.level}`;
        const arrow = this.getArrow(log.level);
        return `<span class="${className}">${arrow} [${log.timestamp}] [${log.category}] ${log.message}</span>`;
      })
      .join('\n');

    // 自动滚动到底部
    logEl.scrollTop = logEl.scrollHeight;
  }

  getArrow(level) {
    const arrows = {
      info: 'ℹ️',
      debug: '🔧',
      warn: '⚠️',
      error: '❌',
      api: '🌐',
      success: '✅'
    };
    return arrows[level] || '•';
  }

  togglePanel() {
    const panel = document.getElementById('debugPanel');
    if (panel) {
      panel.classList.toggle('visible');
    }
  }

  closePanel() {
    const panel = document.getElementById('debugPanel');
    if (panel) {
      panel.classList.remove('visible');
    }
  }

  clearLogs() {
    if (confirm('确定要清空所有日志吗？')) {
      this.logs = [];
      this.render();
      this.log('SYSTEM', '📋 日志已清空', { level: 'info' });
    }
  }

  copyLogs() {
    const text = this.logs
      .map((log) => `[${log.timestamp}] [${log.category}] ${log.message}`)
      .join('\n');

    navigator.clipboard.writeText(text).then(() => {
      this.log('SYSTEM', '✅ 日志已复制到剪贴板', { level: 'success' });
    }).catch((err) => {
      this.log('SYSTEM', `❌ 复制失败: ${err.message}`, { level: 'error' });
    });
  }

  // 导出日志文件
  exportLogs() {
    const text = this.logs
      .map((log) => `[${log.timestamp}] [${log.category}] [${log.level.toUpperCase()}] ${log.message}`)
      .join('\n');

    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `debug-log-${new Date().toISOString().slice(0, 19)}.txt`;
    a.click();
    URL.revokeObjectURL(url);

    this.log('SYSTEM', '💾 日志已导出', { level: 'success' });
  }

  // 记录工作流状态变化
  logStateChange(oldStep, newStep, state) {
    this.log('WORKFLOW', `状态转移: ${oldStep} → ${newStep}`, {
      level: 'info',
      state: state
    });
  }

  // 记录 SSE 事件
  logSSEEvent(eventType, data) {
    this.log('SSE', `事件: ${eventType}`, {
      level: 'api',
      data: data
    });
  }

  // 记录设计生成进度
  logDesignProgress(message, status) {
    this.log('DESIGN', message, {
      level: status === 'error' ? 'error' : 'info',
      status: status
    });
  }
}

// 创建全局日志实例
window.debugLogger = new DebugLogger();

// 导出为全局方法，便于在控制台调用
window.log = {
  info: (msg, extra) => window.debugLogger.log('APP', msg, { level: 'info', ...extra }),
  debug: (msg, extra) => window.debugLogger.log('APP', msg, { level: 'debug', ...extra }),
  warn: (msg, extra) => window.debugLogger.log('APP', msg, { level: 'warn', ...extra }),
  error: (msg, extra) => window.debugLogger.log('APP', msg, { level: 'error', ...extra }),
  api: (msg, extra) => window.debugLogger.log('API', msg, { level: 'api', ...extra }),
  workflow: (msg, extra) => window.debugLogger.log('WORKFLOW', msg, { level: 'info', ...extra }),
  sse: (msg, extra) => window.debugLogger.log('SSE', msg, { level: 'api', ...extra }),
  design: (msg, extra) => window.debugLogger.log('DESIGN', msg, { level: 'info', ...extra }),
  show: () => window.debugLogger.togglePanel(),
  hide: () => window.debugLogger.closePanel(),
  clear: () => window.debugLogger.clearLogs(),
  export: () => window.debugLogger.exportLogs(),
};
