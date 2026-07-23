# 修复：栈溢出问题 (Maximum call stack size exceeded)

## 问题现象
用户切换失败项目时收到栈溢出错误："Maximum call stack size exceeded"

## 根本原因分析

通过代码审查，发现了**三个导致栈溢出的循环调用链**：

### 1. 项目列表事件绑定循环 (主要问题)
**调用链：**
```
applyState(newState) 
  → refreshAllContent() 
  → updateStepNav() 
  → (内部包装后) renderProjectsList(projectsCache) 
  → querySelectorAll().forEach(addEventListener) ← 重复绑定 ✓
  → 用户点击项目 
  → switchProject(sid) 
  → applyState() ← 回到第一步，形成无限循环
```

**位置：** 第 910-922 行的 `applyState` 包装函数

**问题：** 每次 `applyState` 调用都会触发 `renderProjectsList`，而 `renderProjectsList` 在第 861-878 行通过 `forEach` 为每个项目卡片添加新的事件监听器，而不是替换旧的。导致：
- 事件处理函数堆积
- 点击项目时触发多个相同的 `switchProject` 调用
- 形成无限递归

### 2. 计划列表递归渲染 (次要问题)
**位置：** 第 344-349 行和 355-357 行

**问题：** 
```javascript
// 第 347 行
card.addEventListener("click", () => {
  selectedPlanIndex = Number(card.dataset.index);
  renderPlanList(tasks);  // ← 递归调用自己
});

// 第 357 行
renderPlanList(tasks);    // ← 又一次递归调用
```

当 `renderPlanList` 在初始化时，第 356 行的条件成立，导致直接调用自己，形成递归。

## 修复方案

### 修复 1: 移除 applyState 包装中的自动渲染
**文件：** `/Users/shihongwei/workflow_uniapp/app/web/static/app.js` (第 910-922 行)

```javascript
// 之前
applyState = function (newState, opts = {}) {
  originalApplyState.call(this, newState, opts);
  if (sessionId) {
    const idx = projectsCache.findIndex((s) => s.session_id === sessionId);
    if (idx >= 0) {
      projectsCache[idx] = { ...newState };
      renderProjectsList(projectsCache);  // ✗ 导致事件重复绑定
    }
  }
};

// 修复后
applyState = function (newState, opts = {}) {
  originalApplyState.call(this, newState, opts);
  if (sessionId) {
    const idx = projectsCache.findIndex((s) => s.session_id === sessionId);
    if (idx >= 0) {
      projectsCache[idx] = { ...newState };
      // ✓ 只更新缓存，由定时器刷新显示，防止事件重复绑定
    }
  }
};
```

### 修复 2: 使用事件委托代替逐个绑定
**文件：** `/Users/shihongwei/workflow_uniapp/app/web/static/app.js` (第 833-879 行)

```javascript
// 之前：逐个元素绑定 → 重复绑定问题
container.querySelectorAll(".project-item").forEach((el) => {
  el.addEventListener("click", () => switchProject(sid));
});
container.querySelectorAll(".project-delete").forEach((btn) => {
  btn.addEventListener("click", () => deleteProject(sessionId));
});

// 修复后：单个事件委托 → 避免重复绑定
function attachProjectListeners(container) {
  const newContainer = container.cloneNode(true);
  container.parentNode.replaceChild(newContainer, container);
  const updatedContainer = document.getElementById("projectsList");
  
  updatedContainer.addEventListener("click", (e) => {
    const deleteBtn = e.target.closest(".project-delete");
    if (deleteBtn) {
      deleteProject(deleteBtn.dataset.sessionId);
      return;
    }
    const projectItem = e.target.closest(".project-item");
    if (projectItem) {
      switchProject(projectItem.dataset.sessionId);
    }
  });
}
```

### 修复 3: 修复 renderPlanList 递归调用
**文件：** `/Users/shihongwei/workflow_uniapp/app/web/static/app.js` (第 326-359 行)

```javascript
// 之前：事件处理和初始化都调用 renderPlanList → 递归
el.querySelectorAll(".plan-card:not([data-disabled])").forEach((card) => {
  card.addEventListener("click", () => {
    selectedPlanIndex = Number(card.dataset.index);
    renderPlanList(tasks);  // ✗ 递归调用
  });
});

if (typeof state?.selected_task_index === "number") {
  selectedPlanIndex = state.selected_task_index;
  renderPlanList(tasks);  // ✗ 递归调用
}

// 修复后：只更新 UI，不调用自己
el.querySelectorAll(".plan-card:not([data-disabled])").forEach((card) => {
  card.addEventListener("click", () => {
    selectedPlanIndex = Number(card.dataset.index);
    // ✓ 只更新 DOM 类名，不调用 renderPlanList
    el.querySelectorAll(".plan-card").forEach((c) => {
      c.classList.toggle("selected", c.dataset.index == selectedPlanIndex);
    });
  });
});

if (typeof state?.selected_task_index === "number") {
  selectedPlanIndex = state.selected_task_index;
  // ✓ 只更新 DOM 类名
  el.querySelectorAll(".plan-card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.index == selectedPlanIndex);
  });
}
```

### 修复 4: 改进 switchProject 的调用顺序
**文件：** `/Users/shihongwei/workflow_uniapp/app/web/static/app.js` (第 881-907 行)

```javascript
// 之前：先渲染列表，再 applyState → 容易触发列表更新循环
sessionId = newSessionId;
state = newState;
renderProjectsList(projectsCache);  // ✗ 先渲染
applyState(newState, { autoNavigate: true });  // → 再次渲染

// 修复后：先更新状态，后延迟渲染列表
const oldSessionId = sessionId;
sessionId = newSessionId;
state = newState;

applyState(newState, { autoNavigate: true });  // 应用状态

// 延迟更新列表高亮，避免在 applyState 过程中修改 DOM
setImmediate(() => {
  renderProjectsList(projectsCache);
});
```

## 测试步骤

1. **创建多个失败项目**
   - 新建项目 A（手动让分析步骤失败）
   - 新建项目 B（手动让设计步骤失败）
   - 新建项目 C（正常项目）

2. **快速切换项目**
   - 项目 A → 项目 B → 项目 C → 项目 A
   - 观察是否有栈溢出错误

3. **检查项目列表状态**
   - 验证当前活跃项目高亮正确
   - 验证项目步骤显示正确
   - 验证删除功能不受影响

4. **验证错误状态处理**
   - 确认失败项目显示重试按钮
   - 确认可以正常重试操作

## 预期结果

✓ 项目切换流畅，无栈溢出错误
✓ 项目列表实时更新，高亮准确
✓ 事件处理不重复绑定
✓ 错误状态正确处理和恢复

## 文件变更

- `/Users/shihongwei/workflow_uniapp/app/web/static/app.js`
  - 第 326-359 行：修复 `renderPlanList` 递归
  - 第 833-879 行：重构事件委托系统
  - 第 881-907 行：改进 `switchProject` 调用顺序
  - 第 910-922 行：移除 `applyState` 包装中的自动渲染

## 相关配置

- 项目列表刷新间隔：5 秒（第 925 行 `setInterval(loadProjectsList, 5000)`)
- SSE 事件订阅管理：自动处理状态同步

---

**修复时间：** 2026-07-09
**优先级：** 高（功能阻塞）
**风险等级：** 低（修复明显的设计缺陷）
