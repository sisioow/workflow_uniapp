const STYLES = window.__STYLES__ || [];

// 等待日志系统加载
const logger = window.debugLogger || { log: console.log };
if (window.debugLogger) {
  window.log.info('🚀 应用启动', { version: '1.0.0' });
}

const STEP_ORDER = ["input", "analyze", "review", "styles", "design", "plan", "codegen", "evaluate"];

const panels = Object.fromEntries(
  STEP_ORDER.map((k) => [k, document.getElementById(`panel-${k}`)])
);

let sessionId = null;
let state = null;
let selectedPlanIndex = 0;
let eventSource = null;
let currentPanel = "input";
let followWorkflow = true;

function stepIndex(name) {
  return STEP_ORDER.indexOf(name);
}

function defaultPanelForStep(step) {
  // 错误状态：停在出错环节的面板，不跳到其他步骤
  if (step === "error" && state?.error_step) {
    const panel = panelForStep(state.error_step);
    if (panel) return panel;
  }
  return panelForStep(step) || "input";
}

/** 将步骤值映射到面板名 */
function panelForStep(step) {
  const map = {
    input: "input",
    analyze: "analyze",
    review_requirement: "review",
    select_styles: "styles",
    design: "design",
    select_plan: "plan",
    init_project: "codegen",
    codegen: "codegen",
    evaluate: "evaluate",
    done: "evaluate",
    error: "codegen",
  };
  return map[step] || null;
}

function getUnlockedPanels(s) {
  const unlocked = new Set(["input"]);
  if (!s) return unlocked;

  if (s.session_id) {
    unlocked.add("analyze");
  }
  if (s.requirement) {
    unlocked.add("review");
  }
  const stepIdx = {
    input: 0,
    analyze: 1,
    review_requirement: 2,
    select_styles: 3,
    design: 4,
    select_plan: 5,
    init_project: 6,
    codegen: 6,
    done: 6,
    error: 6,
  };
  const progress = stepIdx[s.step] ?? 0;
  if (progress >= 3) unlocked.add("styles");
  if ((s.selected_styles?.length || 0) > 0 || (s.style_tasks?.length || 0) > 0 || progress >= 4) {
    unlocked.add("design");
  }
  if (s.style_tasks?.some((t) => t.status === "done") || progress >= 5) {
    unlocked.add("plan");
  }
  if (s.project_path || ["init_project", "codegen", "evaluate", "done", "error"].includes(s.step)) {
    unlocked.add("codegen");
  }
  if (["evaluate", "done"].includes(s.step) || s.readme_status || s.h5_test_status || s.user_feedback || s.project_path) {
    unlocked.add("evaluate");
  }
  return unlocked;
}

function showPanel(name, manual = false) {
  if (!panels[name]) return;
  const unlocked = getUnlockedPanels(state);
  if (manual && !unlocked.has(name)) return;

  currentPanel = name;
  if (manual) followWorkflow = false;

  Object.values(panels).forEach((p) => p.classList.add("hidden"));
  panels[name].classList.remove("hidden");
  updateStepNav(name);
  updatePager();
  refreshPanelContent(name);
}

function updateStepNav(current) {
  const unlocked = getUnlockedPanels(state);
  const curIdx = stepIndex(current);

  document.querySelectorAll(".step-item").forEach((el) => {
    const name = el.dataset.step;
    const idx = stepIndex(name);
    const isUnlocked = unlocked.has(name);

    el.classList.remove("active", "done", "locked");
    el.disabled = !isUnlocked;

    if (!isUnlocked) el.classList.add("locked");
    if (idx < curIdx && isUnlocked) el.classList.add("done");
    if (name === current) el.classList.add("active");
  });
}

function updatePager() {
  const idx = stepIndex(currentPanel);
  const unlocked = getUnlockedPanels(state);
  const label = document.getElementById("pagerLabel");
  const prev = document.getElementById("btnPrevStep");
  const next = document.getElementById("btnNextStep");

  label.textContent = `第 ${idx + 1} / ${STEP_ORDER.length} 步`;

  const prevName = STEP_ORDER[idx - 1];
  const nextName = STEP_ORDER[idx + 1];
  prev.disabled = idx <= 0;
  next.disabled = idx >= STEP_ORDER.length - 1 || !unlocked.has(nextName);
}

function refreshPanelContent(name) {
  if (!state) return;

  if (name === "input") restoreInputForm();
  if (name === "analyze") renderAnalyzeStatus();
  if (name === "review") renderReviewPanel();
  if (name === "styles") restoreStyleSelection();
  if (name === "design") renderDesignStatus();
  if (name === "plan") renderPlanList(state.style_tasks || []);
  if (name === "codegen") renderCodegenStatus();
  if (name === "evaluate") renderEvaluatePanel();
}

function restoreInputForm() {
  const form = document.getElementById("formStart");
  if (!state?.app_name) return;
  form.app_name.value = state.app_name;
  form.project_dir.value = state.project_dir || "";
  if (form.group_no) form.group_no.value = state.group_no || "";
  if (state.llm_model) form.llm_model.value = state.llm_model;
}

function renderAnalyzeStatus() {
  const el = document.getElementById("analyzeStatus");
  if (!el) return;
  if (!state) {
    el.innerHTML = "<p>尚未开始分析</p>";
    el.classList.remove("is-running");
    return;
  }
  const running = !!state.analyze_running || state.step === "analyze";
  if (running) {
    const model = escapeHtml(state.llm_model || "默认");
    el.classList.add("is-running");
    el.innerHTML = `
      <div class="analyze-running-banner">
        <span class="analyze-spinner" aria-hidden="true"></span>
        <div>
          <p class="analyze-running-title">需求分析进行中…</p>
          <p class="analyze-running-meta">模型：${model} · 正在生成三套方案，请查看下方日志</p>
        </div>
      </div>`;
    return;
  }
  el.classList.remove("is-running");
  if (state.requirement || (state.requirement_options || []).length) {
    el.innerHTML = `<p style="color:var(--success)">分析已完成 · 模型：${escapeHtml(state.llm_model || "默认")}</p>`;
  } else if (state.step === "error") {
    el.innerHTML = `<p style="color:var(--danger)">分析失败，可点击重试</p>`;
  } else {
    el.innerHTML = "<p>等待开始</p>";
  }
}

function setAnalyzeRetryBusy(busy, hintText) {
  const btnReview = document.getElementById("btnRetryAnalyzeFromReview");
  const btnErr = document.getElementById("btnRetryAnalyze");
  const promptEl = document.getElementById("retryAnalyzePrompt");
  const hint = document.getElementById("reviewHint");
  if (btnReview) {
    btnReview.disabled = !!busy;
    btnReview.textContent = busy ? "⏳ 分析中…" : "🔄 重试分析";
  }
  if (btnErr) {
    btnErr.disabled = !!busy;
    btnErr.textContent = busy ? "⏳ 分析中…" : "🔄 重试";
  }
  if (promptEl) promptEl.disabled = !!busy;
  if (busy && hint && hintText) {
    hint.textContent = hintText;
  }
}

function syncRequirementChrome(canEdit, canConfirm, readonly) {
  const hint = document.getElementById("reviewHint");
  const btnSave = document.getElementById("btnSaveRequirement");
  const btnConfirm = document.getElementById("btnConfirmRequirement");
  const retryBox = document.getElementById("reviewRetryBox");
  if (hint) {
    if (readonly) {
      hint.textContent = canEdit
        ? "请先选择一套功能方向方案，再查看并修改后确认。"
        : "当前为只读查看模式。可点击其他步骤切换查看。";
    } else {
      hint.textContent = canConfirm
        ? "已给出三套不同方向方案：点选上方卡片切换，编辑后可保存，再确认进入下一步。"
        : "方案已确认，仍可切换/修改并保存；前往「UI 风格」继续下一步。";
    }
  }
  if (btnSave) btnSave.style.display = canEdit && !readonly ? "" : "none";
  if (btnConfirm) btnConfirm.style.display = canConfirm ? "" : "none";
  if (retryBox) retryBox.style.display = canEdit && !readonly ? "" : "none";
}

function renderRequirementOptions() {
  const box = document.getElementById("requirementOptions");
  if (!box) return;
  const options = state?.requirement_options || [];
  if (!options.length) {
    box.innerHTML = "";
    return;
  }
  const selected = typeof state.selected_requirement_index === "number"
    ? state.selected_requirement_index
    : 0;
  const canSwitch = ["review_requirement", "select_styles"].includes(state?.step);
  box.innerHTML = options
    .map((opt, i) => {
      const title = opt.direction || `方案 ${i + 1}`;
      const summary = opt.summary || "（暂无定位说明）";
      const pages = (opt.pages || []).map((p) => p.title).filter(Boolean).join(" · ") || "—";
      return `
      <button type="button" class="req-option-card ${i === selected ? "selected" : ""}"
        data-index="${i}" ${canSwitch ? "" : "disabled"}>
        <strong>${i + 1}. ${escapeHtml(title)}</strong>
        <div class="req-option-summary">${escapeHtml(summary)}</div>
        <div class="req-option-pages">${escapeHtml(pages)}</div>
      </button>`;
    })
    .join("");

  if (!canSwitch) return;
  box.querySelectorAll(".req-option-card").forEach((card) => {
    card.addEventListener("click", async () => {
      const index = Number(card.dataset.index);
      if (index === selected) return;
      await switchRequirementOption(index);
    });
  });
}

async function switchRequirementOption(index) {
  if (!sessionId) return;
  // 先把当前表单写回服务端，再切换
  const updates = collectRequirementUpdates();
  try {
    if (updates && document.getElementById("editAppName")) {
      await fetch(`/api/session/${sessionId}/requirement`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
    }
    const res = await fetch(`/api/session/${sessionId}/requirement/select`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index }),
    });
    if (!res.ok) throw new Error(await res.text());
    applyState(await res.json(), { forceRequirement: true });
  } catch (err) {
    alert("切换方案失败：" + (err.message || err));
  }
}

function renderRequirementView(req, readonly = false, options = {}) {
  const force = options.force === true;
  const view = document.getElementById("requirementView");
  const formWrap = document.getElementById("requirementEditForm");

  renderRequirementOptions();

  if (!req) {
    view.innerHTML = "<p>暂无方案，请先完成需求分析</p>";
    formWrap.innerHTML = "";
    return;
  }

  const canEdit = ["review_requirement", "select_styles"].includes(state?.step);
  const canConfirm = state?.step === "review_requirement";
  readonly = readonly || !canEdit;

  // SSE 重连/状态刷新时勿重绘编辑表单，否则未保存修改会被冲掉
  if (!readonly && !force && document.getElementById("editAppName")) {
    syncRequirementChrome(canEdit, canConfirm, false);
    return;
  }

  const direction = req.direction ? `「${escapeHtml(req.direction)}」` : "";
  view.innerHTML = `
    <h3>${escapeHtml(req.app_name)} ${direction}</h3>
    <p><strong>产品定位：</strong>${escapeHtml(req.summary || "—")}</p>
    <ul>${(req.pages || [])
      .map(
        (p) =>
          `<li><strong>${escapeHtml(p.title)}</strong>（${escapeHtml(p.key)}）<br/>功能：${escapeHtml((p.features || []).join("、") || "—")}<br/>说明：${escapeHtml(p.description || "—")}</li>`
      )
      .join("")}</ul>
  `;

  if (readonly) {
    formWrap.innerHTML = "";
    syncRequirementChrome(canEdit, canConfirm, true);
    return;
  }

  syncRequirementChrome(canEdit, canConfirm, false);

  formWrap.innerHTML = `
    <div class="edit-form card">
      <label>应用名称<input type="text" id="editAppName" value="${escapeHtml(req.app_name)}" /></label>
      <label>功能方向<input type="text" id="editDirection" value="${escapeHtml(req.direction || "")}" /></label>
      <label>产品定位<textarea id="editSummary" rows="2">${escapeHtml(req.summary || "")}</textarea></label>
      ${(req.pages || [])
        .map(
          (p) => `
        <fieldset class="page-edit">
          <legend>${escapeHtml(p.key)} · ${escapeHtml(p.title)}</legend>
          <label>页面标题<input type="text" data-page="${escapeHtml(p.key)}" class="edit-page-title" value="${escapeHtml(p.title)}" /></label>
          <label>功能点（逗号分隔）<input type="text" data-page="${escapeHtml(p.key)}" class="edit-page-features" value="${escapeHtml((p.features || []).join("，"))}" /></label>
          <label>交互说明<textarea data-page="${escapeHtml(p.key)}" class="edit-page-desc" rows="2">${escapeHtml(p.description || "")}</textarea></label>
        </fieldset>`
        )
        .join("")}
    </div>
  `;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderReviewPanel() {
  const canEdit = ["review_requirement", "select_styles"].includes(state?.step);
  renderRequirementView(state?.requirement, !canEdit, { force: true });
}

function collectRequirementUpdates() {
  const req = state?.requirement;
  if (!req) return null;

  const pages = (req.pages || []).map((p) => {
    const titleEl = document.querySelector(`.edit-page-title[data-page="${p.key}"]`);
    const featEl = document.querySelector(`.edit-page-features[data-page="${p.key}"]`);
    const descEl = document.querySelector(`.edit-page-desc[data-page="${p.key}"]`);
    const featuresRaw = featEl?.value || "";
    const features = featuresRaw.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    return {
      key: p.key,
      title: titleEl?.value?.trim() || p.title,
      features,
      description: descEl?.value?.trim() || "",
    };
  });

  return {
    app_name: document.getElementById("editAppName")?.value?.trim() || req.app_name,
    direction: document.getElementById("editDirection")?.value?.trim() || req.direction || "",
    summary: document.getElementById("editSummary")?.value?.trim() || req.summary,
    pages,
    selected_index: state?.selected_requirement_index ?? 0,
  };
}

function initStyleGrid() {
  const grid = document.getElementById("styleGrid");
  grid.innerHTML = STYLES.map(
    (s) => `
    <label class="style-item">
      <input type="checkbox" name="style" value="${s.id}" />
      <span>${s.name}</span>
    </label>`
  ).join("");
}

function restoreStyleSelection() {
  if (!state?.selected_styles?.length) return;
  const ids = new Set(state.selected_styles.map((s) => s.id));
  document.querySelectorAll('input[name="style"]').forEach((el) => {
    el.checked = ids.has(el.value);
  });
  const colors = state.selected_styles.map((s) => s.color).filter(Boolean).join("；");
  if (colors) document.getElementById("colorInput").value = colors;
}

function getSelectedStyles() {
  return Array.from(document.querySelectorAll('input[name="style"]:checked')).map((el) => el.value);
}

function renderLogs(logs, targetId) {
  const el = document.getElementById(targetId);
  if (!el) return;
  const text = (logs || []).join("\n");
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  el.textContent = text;
  if (nearBottom || targetId === "codegenLog") {
    el.scrollTop = el.scrollHeight;
  }
}

function formatElapsedClient(seconds) {
  const sec = Math.max(0, Math.floor(Number(seconds) || 0));
  if (sec < 60) return `${sec} 秒`;
  const minutes = Math.floor(sec / 60);
  const rem = sec % 60;
  if (minutes < 60) return rem ? `${minutes} 分 ${rem} 秒` : `${minutes} 分`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins ? `${hours} 小时 ${mins} 分` : `${hours} 小时`;
}

function codegenElapsedSeconds(s = state) {
  const started = Number(s?.codegen_started_at || 0);
  if (!started || started <= 0) return 0;
  // 后端存的是 epoch 秒；兼容误传毫秒
  const startSec = started > 1e12 ? started / 1000 : started;
  return Math.max(0, Date.now() / 1000 - startSec);
}

/** 实施进度：仅展示提示词 / 生成中 / 完成+耗时；生成中耗时由前端按秒刷新 */
function renderCodegenProgress(s = state) {
  const lines = [...(s?.codegen_progress || [])];
  if (s?.codegen_running) {
    const label = CODEGEN_TOOL_LABELS[s.codegen_tool] || "代码生成";
    const live = `${label} 正在生成代码中…（已进行 ${formatElapsedClient(codegenElapsedSeconds(s))}）`;
    let replaced = false;
    for (let i = lines.length - 1; i >= 0; i -= 1) {
      if (String(lines[i]).includes("正在生成代码中")) {
        lines[i] = live;
        replaced = true;
        break;
      }
    }
    if (!replaced) lines.push(live);
  }
  renderLogs(lines, "codegenLog");
}

function updateCodegenLiveBadge() {
  const badge = document.getElementById("codegenLiveBadge");
  if (!badge) return;
  if (state?.codegen_running) {
    const elapsed = formatElapsedClient(codegenElapsedSeconds(state));
    badge.textContent = elapsed ? `生成中 · ${elapsed}` : "生成中";
    badge.className = "live-badge running";
  } else if (state?.followup_running) {
    badge.textContent = "续聊中";
    badge.className = "live-badge running";
  } else if ((state?.codegen_progress || []).some((l) => String(l).startsWith("生成完成"))) {
    badge.textContent = "已完成";
    badge.className = "live-badge idle";
  } else if (state?.project_path) {
    badge.textContent = "可续聊";
    badge.className = "live-badge idle";
  } else {
    badge.textContent = "待命";
    badge.className = "live-badge idle";
  }
}

let codegenElapsedTimer = null;

function stopCodegenElapsedTimer() {
  if (codegenElapsedTimer) {
    clearInterval(codegenElapsedTimer);
    codegenElapsedTimer = null;
  }
}

/** 生成进行中时每秒刷新耗时（Claude / Codex / Cursor 共用）；后端结束后停止 */
function ensureCodegenElapsedTimer() {
  if (!state?.codegen_running) {
    stopCodegenElapsedTimer();
    return;
  }
  if (codegenElapsedTimer) return;
  codegenElapsedTimer = setInterval(() => {
    if (!state?.codegen_running) {
      stopCodegenElapsedTimer();
      // 结束后再刷一次，展示最终「生成完成（耗时 …）」
      renderCodegenProgress(state);
      updateCodegenLiveBadge();
      renderCodegenStatus();
      return;
    }
    renderCodegenProgress(state);
    updateCodegenLiveBadge();
    // 状态区「正在使用 xxx 生成…（已进行）」同步刷新
    const el = document.getElementById("codegenStatus");
    if (el && state?.codegen_running) {
      const toolLabel =
        CODEGEN_TOOL_LABELS[state.codegen_tool] || state.codegen_tool || "代码生成";
      const elapsed = formatElapsedClient(codegenElapsedSeconds(state));
      el.innerHTML = `<p style="color:var(--accent)">正在使用 ${toolLabel} 生成代码…（已进行 ${elapsed}）</p>`;
    }
  }, 1000);
}

function renderDesignStatus() {
  const el = document.getElementById("designStatus");
  if (!state?.style_tasks?.length) {
    el.innerHTML = state?.step === "design"
      ? "<p style=\"color:var(--accent)\">设计生成进行中…</p>"
      : "<p>尚未开始设计生成</p>";
    return;
  }
  const done = state.style_tasks.filter((t) => t.status === "done").length;
  const total = state.style_tasks.length;
  const running = state.style_tasks.some((t) => t.status === "running");
  if (running) {
    el.innerHTML = `<p style="color:var(--accent)">进行中 ${done}/${total} 套</p>`;
  } else if (done === total) {
    el.innerHTML = `<p style="color:var(--success)">全部完成 ${done}/${total} 套</p>`;
  } else {
    el.innerHTML = `<p>已完成 ${done}/${total} 套，部分失败</p>`;
  }
}

function stitchProjectUrl(projectId) {
  if (!projectId) return "";
  return `https://stitch.withgoogle.com/projects/${encodeURIComponent(projectId)}`;
}

function renderDesignProgress(tasks) {
  const el = document.getElementById("designProgress");
  if (!tasks?.length) {
    el.innerHTML = "<p>等待开始…</p>";
    return;
  }
  el.innerHTML = tasks
    .map((t) => {
      const url = stitchProjectUrl(t.project_id);
      const viewBtn = url
        ? `<a class="btn btn-stitch-view" href="${url}" target="_blank" rel="noopener noreferrer">前往查看</a>`
        : "";
      return `<div class="progress-item ${t.status}">
        <span class="progress-text">${t.style_name} — ${t.status}${t.error ? "：" + t.error : ""}</span>
        ${viewBtn}
      </div>`;
    })
    .join("");
}

function renderPlanList(tasks) {
  const el = document.getElementById("planList");
  if (!tasks?.length) {
    el.innerHTML = "<p>暂无设计方案，请先完成设计生成</p>";
    return;
  }
  el.innerHTML = tasks
    .map((t, i) => {
      const ok = t.status === "done";
      const url = stitchProjectUrl(t.project_id);
      const viewBtn = url
        ? `<a class="btn btn-stitch-view" href="${url}" target="_blank" rel="noopener noreferrer">去查看</a>`
        : "";
      return `
      <div class="plan-card ${ok ? "" : "failed"} ${i === selectedPlanIndex ? "selected" : ""}"
           data-index="${i}" ${ok ? "" : 'data-disabled="1"'}>
        <div class="plan-card-main">
          <strong>${i + 1}. ${t.style_name}</strong>
          <div class="plan-meta">project: ${t.project_id || "-"} | screens: ${(t.screen_ids || []).length}</div>
        </div>
        ${viewBtn}
      </div>`;
    })
    .join("");

  // ⚠️ 避免递归：只更新选中状态，不再调用 renderPlanList
  el.querySelectorAll(".plan-card:not([data-disabled])").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest("a.btn-stitch-view")) return;
      selectedPlanIndex = Number(card.dataset.index);
      // 只更新 UI，不调用 renderPlanList 以避免递归
      el.querySelectorAll(".plan-card").forEach((c) => {
        c.classList.toggle("selected", c.dataset.index == selectedPlanIndex);
      });
    });
  });

  // ⚠️ 恢复选中状态，但不调用 renderPlanList 以避免递归
  if (state?.framework) {
    const radio = document.querySelector(`input[name="framework"][value="${state.framework}"]`);
    if (radio) radio.checked = true;
  }
  if (typeof state?.selected_task_index === "number") {
    selectedPlanIndex = state.selected_task_index;
    // 只更新 UI 类名，不调用 renderPlanList
    el.querySelectorAll(".plan-card").forEach((card) => {
      card.classList.toggle("selected", card.dataset.index == selectedPlanIndex);
    });
  }
  // 仅用本地偏好刷新 radio，禁止用 SSE 默认值覆盖用户选择
  applyPreferredCodegenToolToRadios();
}

const CODEGEN_TOOL_NAMES = ["codegenToolPlan", "codegenToolPanel"];
const CODEGEN_TOOL_LABELS = {
  codex: "Codex",
  claude: "Claude Code",
  cursor: "Cursor Agent",
};

/** 用户选择的工具（默认 Claude Code）；SSE 不得用后端默认值覆盖 */
let preferredCodegenTool = "claude";
let preferredCodegenToolTouched = false;

function getSelectedCodegenTool() {
  const order =
    currentPanel === "codegen"
      ? ["codegenToolPanel", "codegenToolPlan"]
      : ["codegenToolPlan", "codegenToolPanel"];
  for (const name of order) {
    const checked = document.querySelector(`input[name="${name}"]:checked`);
    if (checked?.value) return checked.value;
  }
  return preferredCodegenTool || "claude";
}

function setCodegenTool(tool, sourceName = null, { fromUser = true } = {}) {
  const value = tool || preferredCodegenTool || "claude";
  if (fromUser) {
    preferredCodegenTool = value;
    preferredCodegenToolTouched = true;
  }
  CODEGEN_TOOL_NAMES.forEach((name) => {
    if (sourceName && name === sourceName) return;
    const radio = document.querySelector(`input[name="${name}"][value="${value}"]`);
    if (radio) radio.checked = true;
  });
}

function applyPreferredCodegenToolToRadios() {
  setCodegenTool(preferredCodegenTool, null, { fromUser: false });
}

function initCodegenToolSync() {
  preferredCodegenTool = "claude";
  preferredCodegenToolTouched = false;
  applyPreferredCodegenToolToRadios();
  CODEGEN_TOOL_NAMES.forEach((name) => {
    document.querySelectorAll(`input[name="${name}"]`).forEach((radio) => {
      radio.addEventListener("change", () => {
        if (radio.checked) setCodegenTool(radio.value, name, { fromUser: true });
      });
    });
  });
}

/**
 * 仅在「会话恢复 / 生成已确认」时采用后端工具；
 * 交互选择阶段绝不把 state.codegen_tool（常为默认值）写回 UI。
 */
function adoptServerCodegenToolIfNeeded(s) {
  if (!s?.codegen_tool) return;
  const confirmed =
    !!s.codegen_running ||
    ["init_project", "codegen", "evaluate", "done"].includes(s.step);
  if (confirmed) {
    preferredCodegenTool = s.codegen_tool;
    preferredCodegenToolTouched = true;
    applyPreferredCodegenToolToRadios();
  } else if (!preferredCodegenToolTouched && s.codegen_tool === "claude") {
    preferredCodegenTool = "claude";
    applyPreferredCodegenToolToRadios();
  }
}

let codegenLaunching = false;

async function startCodegenWithSelectedTool() {
  if (!sessionId) {
    alert("请先创建会话");
    return;
  }
  if (codegenLaunching || state?.codegen_running) {
    window.log?.workflow("代码生成已在启动/进行中，忽略重复点击");
    startCodegenPoll();
    return;
  }
  const codegenTool = getSelectedCodegenTool();
  setCodegenTool(codegenTool, null, { fromUser: true });
  window.log?.workflow(`启动代码生成，工具=${codegenTool}`);

  codegenLaunching = true;
  // 乐观更新：立刻隐藏「开始生成」并显示进行中，避免等待首包轮询
  if (state) {
    state.codegen_running = true;
    state.codegen_tool = codegenTool;
    if (!state.codegen_started_at) {
      state.codegen_started_at = Date.now() / 1000;
    }
    if (["select_plan", "error", "evaluate"].includes(state.step)) {
      state.step = "init_project";
    }
    renderCodegenStatus();
    updateCodegenLiveBadge();
    updatePauseButton();
  }

  try {
    const res = await fetch(`/api/session/${sessionId}/generate-code`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codegen_tool: codegenTool }),
    });
    if (!res.ok) throw new Error(await res.text());
    const payload = await res.json();
    if (payload.data) {
      applyState(payload.data, { autoNavigate: true });
    }
    subscribeEvents();
    startCodegenPoll();
  } catch (err) {
    codegenLaunching = false;
    if (state) {
      state.codegen_running = false;
      renderCodegenStatus();
      updatePauseButton();
    }
    throw err;
  }
}

function renderCodegenStatus() {
  const el = document.getElementById("codegenStatus");
  const startActions = document.getElementById("codegenStartActions");
  const toolBox = document.getElementById("codegenToolInCodegen");
  if (!state) {
    el.innerHTML = "<p>尚未开始</p>";
    renderFollowupChat();
    stopCodegenElapsedTimer();
    return;
  }

  // 刷新 UI 时保持本地偏好，不被 SSE 打回 Codex
  applyPreferredCodegenToolToRadios();

  const running = !!state.codegen_running;
  const canStart =
    !running &&
    ["init_project", "codegen", "select_plan", "error"].includes(state.step) &&
    (state.style_tasks || []).some((t) => t.status === "done");

  if (startActions) startActions.style.display = canStart ? "flex" : "none";
  if (toolBox) toolBox.style.display = running ? "none" : "block";

  const displayTool = running || ["evaluate", "done"].includes(state.step)
    ? state.codegen_tool || preferredCodegenTool
    : preferredCodegenTool;
  const toolLabel = CODEGEN_TOOL_LABELS[displayTool] || displayTool || "Claude Code";

  if (state.step === "done") {
    el.innerHTML = `<p style="color:var(--success)">完成！工具：${toolLabel} · 路径：${state.project_path || ""}</p>`;
  } else if (state.step === "evaluate") {
    el.innerHTML = `<p style="color:var(--success)">代码生成完成（${toolLabel}），请进入「整理测试发布」</p>`;
  } else if (state.step === "error") {
    el.innerHTML = `<p style="color:var(--danger)">错误：${state.error || "未知"}</p><p class="hint">可更换工具后点击「开始生成代码」或「重试」</p>`;
  } else if (running || (["init_project", "codegen"].includes(state.step) && state.project_path && state.codegen_running)) {
    const elapsed = formatElapsedClient(codegenElapsedSeconds(state));
    el.innerHTML = `<p style="color:var(--accent)">正在使用 ${toolLabel} 生成代码…（已进行 ${elapsed}）</p>`;
  } else if (canStart) {
    el.innerHTML = `<p>当前工具：<strong>${toolLabel}</strong>。确认后点击「开始生成代码」。</p>`;
  } else if (state.project_path) {
    el.innerHTML = `<p>项目路径：${state.project_path}</p>`;
  } else {
    el.innerHTML = "<p>请先在「选择方案」中确认方案与工具</p>";
  }

  updateCodegenLiveBadge();
  renderCodegenProgress(state);
  ensureCodegenElapsedTimer();
  renderFollowupChat();
}

let followupPollTimer = null;
let codegenPollTimer = null;

function stopCodegenPoll() {
  if (codegenPollTimer) {
    clearInterval(codegenPollTimer);
    codegenPollTimer = null;
  }
  if (!state?.codegen_running) stopCodegenElapsedTimer();
}

function startCodegenPoll() {
  stopCodegenPoll();
  let sawRunning = !!(state && (state.codegen_running || state.followup_running));
  let ticks = 0;
  codegenPollTimer = setInterval(async () => {
    if (!sessionId) return;
    ticks += 1;
    try {
      const res = await fetch(`/api/session/${sessionId}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.codegen_running || data.followup_running) {
        sawRunning = true;
        codegenLaunching = false;
      }
      state = data;
      renderCodegenProgress(state);
      renderCodegenStatus();
      updateCodegenLiveBadge();
      renderFollowupChat();
      updatePauseButton();

      const finished =
        !data.codegen_running &&
        !data.followup_running &&
        (sawRunning ||
          ["evaluate", "done", "error"].includes(data.step) ||
          ticks >= 3);
      if (finished) {
        codegenLaunching = false;
        stopCodegenElapsedTimer();
        stopCodegenPoll();
        applyState(data, { autoNavigate: true });
      }
    } catch (_) {
      /* ignore */
    }
  }, 1000);
}

function escapeChatHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderFollowupChat() {
  const box = document.getElementById("followupChatMessages");
  const empty = document.getElementById("followupChatEmpty");
  const hint = document.getElementById("followupChatHint");
  const btn = document.getElementById("btnSendFollowup");
  const input = document.getElementById("followupChatInput");
  if (!box || !btn || !input) return;

  const msgs = state?.followup_messages || [];
  const canChat =
    !!state?.project_path &&
    !state.codegen_running &&
    !state.followup_running;

  btn.disabled = !canChat;
  input.disabled = !state?.project_path || !!state?.codegen_running;
  if (hint) {
    if (!state?.project_path) {
      hint.textContent = "需先完成初始化并生成项目";
    } else if (state.codegen_running) {
      hint.textContent = "首轮代码生成进行中…";
    } else if (state.followup_running) {
      hint.textContent = "正在执行补充指令…";
    } else {
      const tool = CODEGEN_TOOL_LABELS[getSelectedCodegenTool()] || "Claude Code";
      hint.textContent = `使用 ${tool} 执行`;
    }
  }

  // 清理旧气泡（保留 empty 节点）
  box.querySelectorAll(".chat-bubble").forEach((n) => n.remove());
  if (empty) empty.style.display = msgs.length ? "none" : "block";

  msgs.forEach((m) => {
    const div = document.createElement("div");
    const role = m.role === "user" ? "user" : "assistant";
    div.className = `chat-bubble ${role}`;
    if (m.status === "error") div.classList.add("error");
    if (m.status === "running") div.classList.add("running");
    const tool = m.tool ? CODEGEN_TOOL_LABELS[m.tool] || m.tool : "";
    div.innerHTML = `${escapeChatHtml(m.content)}<span class="chat-meta">${
      role === "user" ? "你" : "助手"
    }${tool ? " · " + escapeChatHtml(tool) : ""}${
      m.status === "running" ? " · 执行中" : m.status === "error" ? " · 失败" : ""
    }</span>`;
    box.appendChild(div);
  });
  box.scrollTop = box.scrollHeight;
}

function stopFollowupPoll() {
  if (followupPollTimer) {
    clearInterval(followupPollTimer);
    followupPollTimer = null;
  }
}

function startFollowupPoll() {
  stopFollowupPoll();
  followupPollTimer = setInterval(async () => {
    if (!sessionId) return;
    try {
      const res = await fetch(`/api/session/${sessionId}`);
      if (!res.ok) return;
      const data = await res.json();
      state = data;
      renderFollowupChat();
      renderCodegenProgress(state);
      if (!data.followup_running) {
        stopFollowupPoll();
        applyState(data);
      }
    } catch (_) {
      /* ignore transient poll errors */
    }
  }, 2000);
}

async function sendFollowupMessage() {
  if (!sessionId) {
    alert("请先创建会话");
    return;
  }
  const input = document.getElementById("followupChatInput");
  const btn = document.getElementById("btnSendFollowup");
  const text = (input?.value || "").trim();
  if (!text) return;
  if (!state?.project_path) {
    alert("请先完成代码生成，再使用补充对话");
    return;
  }

  btn.disabled = true;
  try {
    const res = await fetch(`/api/session/${sessionId}/followup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        codegen_tool: getSelectedCodegenTool(),
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    const payload = await res.json();
    if (payload.data) {
      state = payload.data;
      renderFollowupChat();
    }
    input.value = "";
    startFollowupPoll();
    startCodegenPoll();
  } catch (err) {
    alert("发送失败：" + err.message);
  } finally {
    renderFollowupChat();
  }
}

function initFollowupChat() {
  const btn = document.getElementById("btnSendFollowup");
  const input = document.getElementById("followupChatInput");
  if (btn) btn.addEventListener("click", sendFollowupMessage);
  if (input) {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (!btn?.disabled) sendFollowupMessage();
      }
    });
  }
}

// ══════════════════════════════════════════════════
// 第 8 步：整理测试发布
// ══════════════════════════════════════════════════

function statusClass(status) {
  if (status === "done") return "ok";
  if (status === "failed") return "fail";
  if (status === "running") return "running";
  return "";
}

function defaultPublishBranch(s = state) {
  let group = (s?.group_no || "").trim();
  if (group && !/^g/i.test(group)) group = `g${group}`;
  if (!group) group = "g0";
  const slug = (s?.project_dir || "").trim().toLowerCase() || "app";
  return `${group}/${slug}`;
}

function ensurePublishFormDefaults() {
  const repoEl = document.getElementById("publishRepoPath");
  const branchEl = document.getElementById("publishBranch");
  if (!repoEl || !branchEl) return;

  if (!repoEl.dataset.touched) {
    repoEl.value =
      state?.release_repo_path ||
      window.__PUBLISH_DEFAULT_REPO__ ||
      "/Users/shihongwei/uniapp1/uniapp";
  }
  if (!branchEl.dataset.touched) {
    branchEl.value = state?.release_branch || defaultPublishBranch(state);
  }
}

function renderEvaluatePanel() {
  const hint = document.getElementById("evaluateHint");
  const readmeEl = document.getElementById("readmeStatus");
  const testEl = document.getElementById("h5TestStatus");
  const urlBox = document.getElementById("h5TestUrlBox");
  const urlLink = document.getElementById("h5TestUrlLink");
  const portEl = document.getElementById("h5TestPort");
  const publishEl = document.getElementById("publishStatus");
  const publishBox = document.getElementById("publishResultBox");
  const publishBranchText = document.getElementById("publishResultBranch");
  const publishCommit = document.getElementById("publishResultCommit");
  const btnReadme = document.getElementById("btnPrepareReadme");
  const btnOpenCursor = document.getElementById("btnOpenInCursor");
  const btnTest = document.getElementById("btnRunH5Test");
  const btnStopTest = document.getElementById("btnStopH5Test");
  const btnPublish = document.getElementById("btnPublish");
  const btnDone = document.getElementById("btnCompleteWorkflow");
  const branchHint = document.getElementById("publishBranchHint");
  if (!readmeEl || !testEl) return;

  ensurePublishFormDefaults();

  const done = state?.step === "done";
  const readmeStatus = state?.readme_status || "";
  const testStatus = state?.h5_test_status || "";
  const releaseStatus = state?.release_status || "";
  const testing = !!state?.h5_test_running || testStatus === "running";
  const publishing = !!state?.release_running || releaseStatus === "running";

  if (hint) {
    hint.textContent = done
      ? "整理、测试与发布已完成。"
      : "整理 README → HBuilderX 测到 Chrome → 推送到 Git 分支。";
  }

  if (branchHint) {
    branchHint.textContent = `默认规则：组号/产品目录全拼（当前建议 ${defaultPublishBranch(state)}）`;
  }

  if (readmeStatus === "done") {
    readmeEl.textContent = state.readme_path
      ? `已生成：${state.readme_path}`
      : "README.md 功能清单已生成";
  } else if (readmeStatus === "failed") {
    readmeEl.textContent = `失败：${state.readme_error || "未知错误"}`;
  } else if (readmeStatus === "running") {
    readmeEl.textContent = "正在生成…";
  } else {
    readmeEl.textContent = "尚未生成";
  }
  readmeEl.className = `release-status ${statusClass(readmeStatus)}`;

  if (testing) {
    testEl.textContent = state?.h5_test_message || "正在编译并打开 Chrome…";
  } else if (testStatus === "done") {
    testEl.textContent = state?.h5_test_message || "编译成功";
  } else if (testStatus === "failed") {
    testEl.textContent = `失败：${state?.h5_test_message || "未知错误"}`;
  } else {
    testEl.textContent = "尚未测试";
  }
  testEl.className = `release-status ${statusClass(testing ? "running" : testStatus)}`;

  if (urlBox && urlLink && portEl) {
    if (state?.h5_test_url) {
      urlBox.classList.remove("hidden");
      urlLink.href = state.h5_test_url;
      urlLink.textContent = state.h5_test_url;
      portEl.textContent = state.h5_test_port ? `端口 ${state.h5_test_port}` : "";
    } else {
      urlBox.classList.add("hidden");
    }
  }

  if (publishEl) {
    if (publishing) {
      publishEl.textContent = state?.release_message || "正在发布…";
    } else if (releaseStatus === "done") {
      publishEl.textContent = state?.release_message || "发布成功";
    } else if (releaseStatus === "branch_exists") {
      publishEl.textContent = state?.release_message || "分支已存在，请修改分支名称";
    } else if (releaseStatus === "failed") {
      publishEl.textContent = `失败：${state?.release_message || "未知错误"}`;
    } else {
      publishEl.textContent = "尚未发布";
    }
    const cls =
      releaseStatus === "branch_exists"
        ? "fail"
        : statusClass(publishing ? "running" : releaseStatus);
    publishEl.className = `release-status ${cls}`;
  }

  if (publishBox && publishBranchText && publishCommit) {
    if (releaseStatus === "done" && state?.release_branch) {
      publishBox.classList.remove("hidden");
      publishBranchText.textContent = state.release_branch;
      publishCommit.textContent = state.release_commit
        ? `commit ${state.release_commit}`
        : state.release_remote_url || "";
    } else {
      publishBox.classList.add("hidden");
    }
  }

  const repoEl = document.getElementById("publishRepoPath");
  const branchEl = document.getElementById("publishBranch");
  if (repoEl) repoEl.disabled = done || publishing;
  if (branchEl) branchEl.disabled = done || publishing;

  if (btnReadme) btnReadme.disabled = done || readmeStatus === "running";
  if (btnOpenCursor) btnOpenCursor.disabled = done || !state?.project_path;
  if (btnTest) btnTest.disabled = done || testing;
  // 编译中或已有测试地址/端口时可关闭
  if (btnStopTest) {
    const hasPort = !!(state?.h5_test_url || state?.h5_test_port);
    btnStopTest.disabled = done || (!testing && !hasPort);
  }
  if (btnPublish) btnPublish.disabled = done || publishing || !state?.project_path;
  if (btnDone) {
    const canFinish =
      readmeStatus === "done" &&
      testStatus === "done" &&
      releaseStatus === "done" &&
      !done;
    btnDone.disabled = !canFinish;
    if (done) btnDone.textContent = "已完成";
    else btnDone.textContent = "完成工作流";
  }
}

async function prepareReadmeAction() {
  if (!sessionId) return;
  const btn = document.getElementById("btnPrepareReadme");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`/api/session/${sessionId}/prepare-readme`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    applyState(await res.json(), { autoNavigate: true });
  } catch (err) {
    alert("整理失败：" + err.message);
  } finally {
    renderEvaluatePanel();
  }
}

async function runH5TestAction() {
  if (!sessionId) return;
  const btn = document.getElementById("btnRunH5Test");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`/api/session/${sessionId}/run-h5-test`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    const payload = await res.json();
    applyState(payload.data || payload, { autoNavigate: true });
    // 后台编译时轮询状态
    pollH5TestUntilDone();
  } catch (err) {
    alert("测试启动失败：" + err.message);
    renderEvaluatePanel();
  }
}

async function stopH5TestAction() {
  if (!sessionId) return;
  const btn = document.getElementById("btnStopH5Test");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`/api/session/${sessionId}/stop-h5-test`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    const payload = await res.json();
    if (h5TestPollTimer) {
      clearInterval(h5TestPollTimer);
      h5TestPollTimer = null;
    }
    applyState(payload.data || payload, { autoNavigate: false });
  } catch (err) {
    alert("关闭测试端口失败：" + err.message);
    renderEvaluatePanel();
  }
}

let h5TestPollTimer = null;
function pollH5TestUntilDone() {
  if (h5TestPollTimer) clearInterval(h5TestPollTimer);
  let ticks = 0;
  h5TestPollTimer = setInterval(async () => {
    ticks += 1;
    if (!sessionId || ticks > 120) {
      clearInterval(h5TestPollTimer);
      h5TestPollTimer = null;
      return;
    }
    try {
      const res = await fetch(`/api/session/${sessionId}`);
      if (!res.ok) return;
      const data = await res.json();
      applyState(data, { autoNavigate: false });
      if (!data.h5_test_running && data.h5_test_status && data.h5_test_status !== "running") {
        clearInterval(h5TestPollTimer);
        h5TestPollTimer = null;
      }
    } catch (_) {
      /* ignore */
    }
  }, 1500);
}

async function publishAction() {
  if (!sessionId) return;
  const repoEl = document.getElementById("publishRepoPath");
  const branchEl = document.getElementById("publishBranch");
  const repoPath = (repoEl?.value || "").trim();
  const branch = (branchEl?.value || "").trim();
  if (!repoPath) {
    alert("请填写目标仓库路径");
    return;
  }
  if (!branch) {
    alert("请填写分支名称");
    return;
  }

  const btn = document.getElementById("btnPublish");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`/api/session/${sessionId}/publish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_path: repoPath, branch }),
    });
    if (!res.ok) throw new Error(await res.text());
    const payload = await res.json();
    applyState(payload.data || payload, { autoNavigate: true });
    pollPublishUntilDone();
  } catch (err) {
    alert("发布启动失败：" + err.message);
    renderEvaluatePanel();
  }
}

let publishPollTimer = null;
function pollPublishUntilDone() {
  if (publishPollTimer) clearInterval(publishPollTimer);
  let ticks = 0;
  publishPollTimer = setInterval(async () => {
    ticks += 1;
    if (!sessionId || ticks > 180) {
      clearInterval(publishPollTimer);
      publishPollTimer = null;
      return;
    }
    try {
      const res = await fetch(`/api/session/${sessionId}`);
      if (!res.ok) return;
      const data = await res.json();
      applyState(data, { autoNavigate: false });
      if (!data.release_running && data.release_status && data.release_status !== "running") {
        clearInterval(publishPollTimer);
        publishPollTimer = null;
        if (data.release_status === "branch_exists") {
          alert(data.release_message || "分支已存在，请修改分支名称后再发布");
          const branchEl = document.getElementById("publishBranch");
          if (branchEl) {
            branchEl.dataset.touched = "1";
            branchEl.focus();
            branchEl.select();
          }
        } else if (data.release_status === "failed") {
          alert("发布失败：" + (data.release_message || "未知错误"));
        }
      }
    } catch (_) {
      /* ignore */
    }
  }, 1500);
}

async function completeWorkflowAction() {
  if (!sessionId) return;
  const btn = document.getElementById("btnCompleteWorkflow");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`/api/session/${sessionId}/complete-workflow`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    applyState(await res.json(), { autoNavigate: true });
  } catch (err) {
    alert("完成失败：" + err.message);
    renderEvaluatePanel();
  }
}

async function openInCursorAction() {
  if (!sessionId) return;
  const btn = document.getElementById("btnOpenInCursor");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`/api/session/${sessionId}/open-in-cursor`, { method: "POST" });
    if (!res.ok) {
      let detail = await res.text();
      try {
        const err = JSON.parse(detail);
        detail = err.detail || detail;
      } catch (_) {
        /* ignore */
      }
      throw new Error(detail);
    }
    const payload = await res.json();
    window.log?.workflow(payload.message || "已在 Cursor 打开项目");
  } catch (err) {
    alert("打开 Cursor 失败：" + err.message);
  } finally {
    renderEvaluatePanel();
  }
}

function initEvaluateEvents() {
  document.getElementById("btnPrepareReadme")?.addEventListener("click", () => {
    prepareReadmeAction();
  });
  document.getElementById("btnOpenInCursor")?.addEventListener("click", () => {
    openInCursorAction();
  });
  document.getElementById("btnRunH5Test")?.addEventListener("click", () => {
    runH5TestAction();
  });
  document.getElementById("btnStopH5Test")?.addEventListener("click", () => {
    stopH5TestAction();
  });
  document.getElementById("btnPublish")?.addEventListener("click", () => {
    publishAction();
  });
  document.getElementById("btnCompleteWorkflow")?.addEventListener("click", () => {
    completeWorkflowAction();
  });

  const repoEl = document.getElementById("publishRepoPath");
  const branchEl = document.getElementById("publishBranch");
  repoEl?.addEventListener("input", () => {
    repoEl.dataset.touched = "1";
  });
  branchEl?.addEventListener("input", () => {
    branchEl.dataset.touched = "1";
  });
}

function refreshAllContent(options = {}) {
  renderAnalyzeStatus();
  setAnalyzeRetryBusy(!!state?.analyze_running || state?.step === "analyze");
  renderRequirementView(
    state?.requirement,
    !["review_requirement", "select_styles"].includes(state?.step),
    { force: options.forceRequirement === true }
  );
  renderDesignStatus();
  renderDesignProgress(state?.style_tasks);
  renderPlanList(state?.style_tasks || []);
  renderCodegenStatus();
  renderLogs(state?.logs, "analyzeLog");
  renderLogs(state?.logs, "logView");
  renderCodegenProgress(state);
  renderEvaluatePanel();

  // ✅ 添加：显示错误信息和重试按钮
  updateErrorDisplay();

  updateStepNav(currentPanel);
  updatePager();
}

// ✅ 新增：显示错误信息和重试按钮
function updateErrorDisplay() {
  if (state?.step === "error" && state?.error) {
    // 根据当前面板显示相应的错误
    if (currentPanel === "analyze") {
      const box = document.getElementById("analyzeError");
      const msg = document.getElementById("analyzeErrorMsg");
      if (box && msg) {
        msg.textContent = state.error;
        box.classList.remove("hidden");
      }
    } else if (currentPanel === "design") {
      const box = document.getElementById("designError");
      const msg = document.getElementById("designErrorMsg");
      if (box && msg) {
        msg.textContent = state.error;
        box.classList.remove("hidden");
      }
    } else if (currentPanel === "codegen") {
      const box = document.getElementById("codegenError");
      const msg = document.getElementById("codegenErrorMsg");
      if (box && msg) {
        msg.textContent = state.error;
        box.classList.remove("hidden");
      }
    }
  } else {
    // 隐藏所有错误框
    ["analyzeError", "designError", "codegenError"].forEach((id) => {
      const box = document.getElementById(id);
      if (box) box.classList.add("hidden");
    });
  }
}

function isWorkflowIdle(s = state) {
  if (!s) return true;
  if (s.analyze_running || s.design_running || s.codegen_running || s.followup_running) return false;
  if ((s.style_tasks || []).some((t) => t.status === "running")) return false;
  return [
    "review_requirement",
    "select_styles",
    "select_plan",
    "evaluate",
    "done",
    "error",
  ].includes(s.step);
}

function closeEventSourceIfIdle(s = state) {
  // EventSource 在服务端关闭后会自动重连；审核/选风格等静默步骤若反复推送会冲掉表单
  if (!eventSource || !isWorkflowIdle(s)) return;
  eventSource.close();
  eventSource = null;
  window.log?.sse?.("SSE 已关闭（当前为等待用户操作步骤）");
}

const DEFAULT_PAGE_TITLE = "App 生成工作流";

function updatePageTitle(appName) {
  const name = (appName || "").trim();
  const title = name || DEFAULT_PAGE_TITLE;
  const el = document.getElementById("pageTitle");
  if (el) el.textContent = title;
  document.title = title;
}

function applyState(s, options = {}) {
  const { autoNavigate = false, resetToolPreference = false, forceRequirement = false } = options;
  const oldStep = state?.step;
  const sessionChanged = sessionId && s.session_id && sessionId !== s.session_id;
  state = s;
  sessionId = s.session_id;

  window.log?.workflow(`状态更新: 会话=${sessionId}, 步骤=${s.step}${oldStep ? `, 来自=${oldStep}` : ''}`);

  updatePageTitle(s?.app_name);
  document.getElementById("sessionInfo").textContent = sessionId
    ? `会话 ${sessionId}`
    : "";

  if (resetToolPreference || sessionChanged) {
    preferredCodegenTool = "claude";
    preferredCodegenToolTouched = false;
  }
  adoptServerCodegenToolIfNeeded(s);

  refreshAllContent({ forceRequirement: forceRequirement || sessionChanged });
  updatePauseButton();
  closeEventSourceIfIdle(s);

  if (autoNavigate || followWorkflow) {
    const target = defaultPanelForStep(s.step);
    showPanel(target, false);
    followWorkflow = true;
  } else {
    updateStepNav(currentPanel);
    refreshPanelContent(currentPanel);
  }
}

function subscribeEvents() {
  if (eventSource) eventSource.close();

  window.log?.api(`订阅 SSE 事件流: /api/session/${sessionId}/events`);

  eventSource = new EventSource(`/api/session/${sessionId}/events`);

  eventSource.onopen = () => {
    window.log?.sse(`SSE 连接已建立`);
  };

  eventSource.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      window.log?.sse(`收到 SSE 消息: type=${msg.type}, step=${msg.data?.step}`);

      if (msg.type === "state") {
        const prevStep = state?.step;
        state = msg.data;
        adoptServerCodegenToolIfNeeded(state);
        refreshAllContent(); // 审核表单已有编辑内容时不会重绘
        updatePauseButton();
        closeEventSourceIfIdle(state);
        if (followWorkflow) {
          const target = defaultPanelForStep(state.step);
          if (target !== currentPanel || prevStep !== state.step) {
            showPanel(target, false);
          }
        } else {
          refreshPanelContent(currentPanel);
        }
      }
    } catch (err) {
      window.log?.error(`SSE 消息解析失败: ${err.message}`, { error: err });
    }
  };

  eventSource.onerror = (err) => {
    window.log?.error(`SSE 连接错误: ${err.message || '未知错误'}`, { error: err });
  };
}

async function fetchConfig() {
  const res = await fetch("/api/config");
  const cfg = await res.json();
  document.getElementById("modelInfo").textContent = `默认分析模型：${cfg.llm_model}`;
  const sel = document.getElementById("llmModelSelect");
  const models = cfg.llm_models?.length ? cfg.llm_models : [cfg.llm_model];
  sel.innerHTML = models
    .map((m) => `<option value="${m}" ${m === cfg.llm_model ? "selected" : ""}>${m}</option>`)
    .join("");
  window.__PUBLISH_DEFAULT_REPO__ =
    cfg.publish_default_repo || "/Users/shihongwei/uniapp1/uniapp";
}

document.querySelectorAll(".step-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    const step = btn.dataset.step;
    if (!btn.disabled) showPanel(step, true);
  });
});

document.getElementById("btnPrevStep").addEventListener("click", () => {
  const idx = stepIndex(currentPanel);
  const unlocked = getUnlockedPanels(state);
  for (let i = idx - 1; i >= 0; i--) {
    if (unlocked.has(STEP_ORDER[i])) {
      showPanel(STEP_ORDER[i], true);
      break;
    }
  }
});

document.getElementById("btnNextStep").addEventListener("click", () => {
  const idx = stepIndex(currentPanel);
  const unlocked = getUnlockedPanels(state);
  for (let i = idx + 1; i < STEP_ORDER.length; i++) {
    if (unlocked.has(STEP_ORDER[i])) {
      showPanel(STEP_ORDER[i], true);
      break;
    }
  }
});

document.getElementById("formStart").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  followWorkflow = true;

  const appName = fd.get("app_name");
  const projectDir = fd.get("project_dir");
  const groupNo = (fd.get("group_no") || "").toString().trim();
  const llmModel = fd.get("llm_model") || "";

  window.log?.workflow(
    `开始新项目: app_name=${appName}, group_no=${groupNo || "—"}, project_dir=${projectDir}, llm_model=${llmModel}`
  );

  try {
    const res = await fetch("/api/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        app_name: appName,
        project_dir: projectDir,
        group_no: groupNo,
        llm_model: llmModel,
      }),
    });
    if (!res.ok) throw new Error(await res.text());

    const result = await res.json();
    window.log?.workflow(`✅ 项目已创建，需求分析进行中: session_id=${result.session_id}`);

    applyState(result, { autoNavigate: true, resetToolPreference: true });
    subscribeEvents();
  } catch (err) {
    window.log?.error(`❌ 启动失败: ${err.message}`, { error: err });
    alert("启动失败：" + err.message);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("btnSaveRequirement").addEventListener("click", async () => {
  const updates = collectRequirementUpdates();
  if (!updates || !sessionId) return;
  const btn = document.getElementById("btnSaveRequirement");
  btn.disabled = true;
  try {
    const res = await fetch(`/api/session/${sessionId}/requirement`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    if (!res.ok) throw new Error(await res.text());
    applyState(await res.json(), { forceRequirement: true });
    alert("已保存修改");
  } catch (err) {
    alert("保存失败：" + err.message);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("btnConfirmRequirement").addEventListener("click", async () => {
  const btn = document.getElementById("btnConfirmRequirement");
  btn.disabled = true;
  followWorkflow = true;
  try {
    if (["review_requirement", "select_styles"].includes(state?.step) && document.getElementById("editAppName")) {
      const updates = collectRequirementUpdates();
      await fetch(`/api/session/${sessionId}/requirement`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
    }
    const res = await fetch(`/api/session/${sessionId}/confirm-requirement`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) throw new Error(await res.text());
    applyState(await res.json(), { autoNavigate: true });
  } catch (err) {
    alert("确认失败：" + err.message);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("btnStartDesign").addEventListener("click", async () => {
  const ids = getSelectedStyles();
  if (!ids.length) {
    alert("请至少选择一种风格");
    return;
  }
  const colors = document.getElementById("colorInput").value;
  const btn = document.getElementById("btnStartDesign");
  btn.disabled = true;
  followWorkflow = true;

  // 判断设计路线
  const routeRadio = document.querySelector('input[name="designRoute"]:checked');
  const route = routeRadio ? routeRadio.value : "stitch";

  if (route === "codex") {
    // 路线 B：跳过 Stitch，直接进入第 6 步「选择方案」
    window.log?.workflow(
      `跳过设计: 风格=${ids.join(",")}, 配色=${colors || "默认"}`
    );

    try {
      await fetch(`/api/session/${sessionId}/styles`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ style_ids: ids, colors }),
      });

      const skipRes = await fetch(`/api/session/${sessionId}/skip-design`, { method: "POST" });
      if (!skipRes.ok) throw new Error(await skipRes.text());
      const skipData = await skipRes.json();
      window.log?.workflow("✅ 已跳过设计，进入选择方案");

      applyState(skipData, { autoNavigate: true });
      showPanel("plan", false);
      updateStepNav("plan");
    } catch (err) {
      window.log?.error(`❌ 启动失败: ${err.message}`, { error: err });
      alert("启动失败：" + err.message);
    } finally {
      btn.disabled = false;
    }
  } else {
    // 路线 A：Stitch MCP 设计工具
    window.log?.design(`开始设计生成: 风格=${ids.join(',')}, 配色=${colors || '默认'}`);
    showPanel("design", false);

    try {
      await fetch(`/api/session/${sessionId}/styles`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ style_ids: ids, colors }),
      });
      window.log?.design(`✅ 风格配置已提交`);

      const designRes = await fetch(`/api/session/${sessionId}/design`, { method: "POST" });
      const designData = await designRes.json();
      window.log?.design(`✅ 设计任务已启动: ${designData.message}`);

      subscribeEvents();
    } catch (err) {
      window.log?.error(`❌ 设计生成失败: ${err.message}`, { error: err });
      alert("设计生成失败：" + err.message);
    } finally {
      btn.disabled = false;
    }
  }
});

document.getElementById("btnConfirmPlan").addEventListener("click", async () => {
  const framework = document.querySelector('input[name="framework"]:checked')?.value || "vue";
  const codegenTool = getSelectedCodegenTool();
  setCodegenTool(codegenTool);
  const btn = document.getElementById("btnConfirmPlan");
  btn.disabled = true;
  const prevLabel = btn.textContent;
  btn.textContent = "下载设计图中…";
  followWorkflow = true;

  try {
    const planRes = await fetch(`/api/session/${sessionId}/select-plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task_index: selectedPlanIndex,
        framework,
        codegen_tool: codegenTool,
      }),
    });
    if (!planRes.ok) throw new Error(await planRes.text());
    applyState(await planRes.json(), { autoNavigate: true });

    btn.textContent = "正在启动代码生成…";
    await startCodegenWithSelectedTool();
  } catch (err) {
    window.log?.error(`❌ 确认方案失败: ${err.message}`, { error: err });
    alert("确认失败：" + err.message);
    showPanel("plan", false);
  } finally {
    btn.disabled = false;
    btn.textContent = prevLabel || "确认并生成代码";
  }
});

document.getElementById("btnStartCodegen")?.addEventListener("click", async () => {
  const btn = document.getElementById("btnStartCodegen");
  if (codegenLaunching || state?.codegen_running) return;
  btn.disabled = true;
  const prevLabel = btn.textContent;
  followWorkflow = true;
  try {
    // 若尚未 select-plan（例如从代码生成页直接点开始），先确认方案
    if (state?.step === "select_plan") {
      btn.textContent = "下载设计图中…";
      const framework = document.querySelector('input[name="framework"]:checked')?.value || "vue";
      const planRes = await fetch(`/api/session/${sessionId}/select-plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_index: selectedPlanIndex,
          framework,
          codegen_tool: getSelectedCodegenTool(),
        }),
      });
      if (!planRes.ok) throw new Error(await planRes.text());
      applyState(await planRes.json(), { autoNavigate: true });
    }
    btn.textContent = "正在启动…";
    await startCodegenWithSelectedTool();
  } catch (err) {
    alert("启动代码生成失败：" + err.message);
  } finally {
    btn.textContent = prevLabel || "开始生成代码";
    // running 时由 renderCodegenStatus 隐藏按钮；未启动成功则重新可点
    btn.disabled = !!(state?.codegen_running || codegenLaunching);
  }
});

initStyleGrid();
initCodegenToolSync();
initFollowupChat();
fetchConfig();
updatePager();
initProjectsList();
initPauseButtons();
initEvaluateEvents();

// ✅ 添加：重试按钮事件监听
async function postRetryAnalyze(extraPrompt = "") {
  if (!sessionId) return;
  followWorkflow = true;

  const stamp = new Date().toLocaleTimeString();
  const localLine = extraPrompt
    ? `[${stamp}] 🔄 已提交重试：按补充提示词重新分析…`
    : `[${stamp}] 🔄 已提交重试：重新分析需求方案…`;

  // 先本地切换 UI，避免等接口返回时感觉无响应
  if (!state) state = {};
  state.step = "analyze";
  state.analyze_running = true;
  state.error = "";
  state.logs = [...(state.logs || []), localLine];
  setAnalyzeRetryBusy(true, "正在重新分析需求方案，请稍候…");
  showPanel("analyze", true);
  renderAnalyzeStatus();
  renderLogs(state.logs, "analyzeLog");

  const res = await fetch(`/api/session/${sessionId}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ extra_prompt: extraPrompt || "" }),
  });
  if (!res.ok) throw new Error(await res.text());
  const result = await res.json();
  if (result.data) {
    applyState(result.data, { autoNavigate: true, forceRequirement: true });
  }
  subscribeEvents();
}

document.getElementById("btnRetryAnalyze").addEventListener("click", async () => {
  if (!sessionId) return;
  if (state?.analyze_running) return;
  const btn = document.getElementById("btnRetryAnalyze");
  btn.disabled = true;
  window.log?.workflow(`开始重试: 需求分析`);
  try {
    await postRetryAnalyze("");
  } catch (err) {
    window.log?.error(`❌ 重试失败: ${err.message}`, { error: err });
    if (state) state.analyze_running = false;
    setAnalyzeRetryBusy(false);
    renderAnalyzeStatus();
    alert("重试失败：" + err.message);
    btn.disabled = false;
  }
});

document.getElementById("btnRetryAnalyzeFromReview").addEventListener("click", async () => {
  if (!sessionId) return;
  if (state?.analyze_running) return;
  const btn = document.getElementById("btnRetryAnalyzeFromReview");
  const promptEl = document.getElementById("retryAnalyzePrompt");
  const extraPrompt = (promptEl?.value || "").trim();
  btn.disabled = true;
  window.log?.workflow(
    extraPrompt ? `审核方案重试分析，补充提示词长度=${extraPrompt.length}` : "审核方案重试分析（无补充提示词）"
  );
  try {
    await postRetryAnalyze(extraPrompt);
  } catch (err) {
    window.log?.error(`❌ 重试失败: ${err.message}`, { error: err });
    if (state) state.analyze_running = false;
    setAnalyzeRetryBusy(false);
    renderAnalyzeStatus();
    alert("重试失败：" + err.message);
    btn.disabled = false;
  }
});

document.getElementById("btnRetryDesign").addEventListener("click", async () => {
  if (!sessionId) return;
  const btn = document.getElementById("btnRetryDesign");
  btn.disabled = true;
  followWorkflow = true;

  window.log?.workflow(`开始重试: 设计生成`);

  try {
    // 重新启动设计流程
    await fetch(`/api/session/${sessionId}/design`, { method: "POST" });
    subscribeEvents();
  } catch (err) {
    window.log?.error(`❌ 重试失败: ${err.message}`, { error: err });
    alert("重试失败：" + err.message);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("btnRetryCodegen").addEventListener("click", async () => {
  if (!sessionId) return;
  const btn = document.getElementById("btnRetryCodegen");
  btn.disabled = true;
  followWorkflow = true;
  const codegenTool = getSelectedCodegenTool();
  setCodegenTool(codegenTool);
  window.log?.workflow(`开始重试代码生成，工具=${codegenTool}`);

  try {
    await startCodegenWithSelectedTool();
  } catch (err) {
    window.log?.error(`❌ 重试失败: ${err.message}`, { error: err });
    alert("重试失败：" + err.message);
  } finally {
    btn.disabled = false;
  }
});

const params = new URLSearchParams(location.search);
const resumeId = params.get("session");
if (resumeId) {
  fetch(`/api/session/${resumeId}`)
    .then((r) => r.json())
    .then((s) => {
      applyState(s, { autoNavigate: true });
      subscribeEvents();
    });
}

// ============================================================
// 项目列表管理
// ============================================================

const STEP_NAMES = {
  input: "📋 应用信息",
  analyze: "🔍 需求分析",
  review_requirement: "✏️ 审核方案",
  select_styles: "🎨 UI 风格",
  design: "🖼️ 设计生成",
  select_plan: "✅ 选择方案",
  init_project: "🚀 项目初始化",
  codegen: "💻 代码生成",
  evaluate: "📦 整理测试发布",
  done: "✅ 完成",
  error: "❌ 错误",
};

let projectsCache = [];

function isDesignBusy(s = state) {
  if (!s) return false;
  if (s.design_running) return true;
  return (s.style_tasks || []).some((t) => t.status === "running");
}

function isCodegenBusy(s = state) {
  if (!s) return false;
  return !!(s.codegen_running || s.followup_running);
}

function updatePauseButton() {
  const designBtn = document.getElementById("btnPauseDesign");
  const codegenBtn = document.getElementById("btnPauseCodegen");
  if (designBtn) {
    const busy = !!sessionId && isDesignBusy();
    designBtn.disabled = !busy;
    designBtn.title = busy ? "暂停当前设计生成" : "设计生成未在进行中";
  }
  if (codegenBtn) {
    const busy = !!sessionId && isCodegenBusy();
    codegenBtn.disabled = !busy;
    codegenBtn.title = busy ? "暂停当前代码生成" : "代码生成未在进行中";
  }
}

async function pauseCurrentStep(kind) {
  if (!sessionId) {
    alert("请先选中一个项目");
    return;
  }
  const busy = kind === "design" ? isDesignBusy() : isCodegenBusy();
  if (!busy) {
    alert(kind === "design" ? "设计生成未在进行中" : "代码生成未在进行中");
    return;
  }
  if (!confirm(kind === "design" ? "确定暂停当前设计生成？" : "确定暂停当前代码生成？")) {
    return;
  }

  const btn = document.getElementById(kind === "design" ? "btnPauseDesign" : "btnPauseCodegen");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`/api/session/${sessionId}/cancel`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || data.message || `HTTP ${res.status}`);
    }
    if (data.data) {
      applyState(data.data);
    } else {
      const res2 = await fetch(`/api/session/${sessionId}`);
      if (res2.ok) applyState(await res2.json());
    }
    window.log?.workflow?.("⏹ 已请求暂停当前环节");
  } catch (err) {
    alert(`暂停失败：${err.message || err}`);
  } finally {
    updatePauseButton();
  }
}

function initPauseButtons() {
  const designBtn = document.getElementById("btnPauseDesign");
  if (designBtn) {
    designBtn.addEventListener("click", () => pauseCurrentStep("design"));
  }
  const codegenBtn = document.getElementById("btnPauseCodegen");
  if (codegenBtn) {
    codegenBtn.addEventListener("click", () => pauseCurrentStep("codegen"));
  }
  updatePauseButton();
}

function initProjectsList() {
  const newBtn = document.getElementById("btnNewProject");
  if (newBtn) {
    newBtn.addEventListener("click", createNewProject);
  }

  const refreshBtn = document.getElementById("btnRefreshProjects");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", loadProjectsList);
  }

  const groupFilter = document.getElementById("groupFilter");
  if (groupFilter) {
    groupFilter.addEventListener("change", () => {
      projectsGroupFilter = groupFilter.value;
      renderProjectsList(projectsCache);
    });
  }

  initProjectsListScrollIsolation();
  loadProjectsList();
}

function createNewProject() {
  // 停止当前的事件流
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }

  // 重置状态，回到初始界面
  sessionId = null;
  state = null;
  currentPanel = "input";
  followWorkflow = true;
  preferredCodegenTool = "claude";
  preferredCodegenToolTouched = false;
  applyPreferredCodegenToolToRadios();

  // 清空表单
  const form = document.getElementById("formStart");
  if (form) {
    form.reset();
  }

  // 更新项目列表高亮
  renderProjectsList(projectsCache);

  // 显示输入面板
  showPanel("input", true);

  // 更新会话信息
  updatePageTitle("");
  document.getElementById("sessionInfo").textContent = "未选择项目";
  updatePauseButton();

  // 自动焦点到应用名称输入框
  const appNameInput = form?.querySelector('input[name="app_name"]');
  if (appNameInput) {
    appNameInput.focus();
  }
}

/**
 * 比较两个会话列表是否相同（按 session_id + step + group_no 判断）。
 */
function _sessionsEqual(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  return a.every((sa, i) => {
    const sb = b[i];
    return (
      sa.session_id === sb.session_id &&
      sa.step === sb.step &&
      (sa.group_no || "") === (sb.group_no || "") &&
      (sa.app_name || "") === (sb.app_name || "") &&
      (sa.created || "") === (sb.created || "")
    );
  });
}

/** 当前组号筛选值；空字符串表示全部 */
let projectsGroupFilter = "";

/** 固定筛选项：全部 / g1 / g5 / g7 / g10 / g11 / 未分组 */
const FIXED_GROUP_FILTERS = ["", "g1", "g5", "g7", "g10", "g11", "__ungrouped__"];

function syncGroupFilterOptions(_sessions) {
  const select = document.getElementById("groupFilter");
  if (!select) return;
  const prev = projectsGroupFilter;
  if (FIXED_GROUP_FILTERS.includes(prev)) {
    select.value = prev;
  } else {
    select.value = "";
  }
  projectsGroupFilter = select.value;
}

function getFilteredSessions(sessions) {
  const list = sessions || [];
  if (!projectsGroupFilter) return list;
  if (projectsGroupFilter === "__ungrouped__") {
    return list.filter((s) => !(s.group_no || "").trim());
  }
  const target = projectsGroupFilter.toLowerCase();
  return list.filter((s) => (s.group_no || "").trim().toLowerCase() === target);
}

/** 悬停左侧列表时，滚轮只滚动列表，不带动页面/右侧主区 */
function initProjectsListScrollIsolation() {
  const list = document.getElementById("projectsList");
  if (!list || list.dataset.scrollIsolated === "1") return;
  list.dataset.scrollIsolated = "1";
  list.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      e.stopPropagation();
      list.scrollTop += e.deltaY;
    },
    { passive: false }
  );
}

async function loadProjectsList() {
  try {
    const response = await fetch("/api/sessions");
    const sessions = await response.json();

    // 仅在数据有变化时才重新渲染，避免不必要的 DOM 操作
    if (!_sessionsEqual(projectsCache, sessions)) {
      projectsCache = sessions;
      syncGroupFilterOptions(sessions);
      renderProjectsList(sessions);
    }
  } catch (err) {
    console.error("加载项目列表失败:", err);
  }
}

let _renderingProjects = false;
let _switchingProject = false;

function formatProjectCreated(ts) {
  const t = Number(ts);
  if (!Number.isFinite(t) || t <= 0) return "";
  const d = new Date(t * 1000);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  const now = new Date();
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (d.toDateString() === now.toDateString()) return `今天 ${hm}`;
  if (d.getFullYear() === now.getFullYear()) {
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
  }
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function formatProjectCreatedTitle(ts) {
  const t = Number(ts);
  if (!Number.isFinite(t) || t <= 0) return "创建时间未知";
  const d = new Date(t * 1000);
  if (Number.isNaN(d.getTime())) return "创建时间未知";
  const pad = (n) => String(n).padStart(2, "0");
  return `创建于 ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function renderProjectsList(sessions) {
  if (_renderingProjects) return; // 防止重入
  _renderingProjects = true;

  try {
    const container = document.getElementById("projectsList");
    const filtered = getFilteredSessions(sessions);
    if (!sessions || sessions.length === 0) {
      container.innerHTML = '<div class="projects-empty">暂无项目</div>';
      return;
    }
    if (!filtered.length) {
      container.innerHTML = '<div class="projects-empty">该组号下暂无项目</div>';
      attachProjectListeners(container);
      return;
    }

    container.innerHTML = filtered
      .map((session) => {
        const isActive = sessionId === session.session_id;
        const stepName = STEP_NAMES[session.step] || session.step;
        const groupNo = (session.group_no || "").trim();
        const groupBadge = groupNo
          ? `<span class="group-badge" title="组号：${escapeHtml(groupNo)}">${escapeHtml(groupNo)}</span>`
          : `<span class="group-badge ungrouped" title="未填写组号">未分组</span>`;
        const createdLabel = formatProjectCreated(session.created);
        const createdTitle = formatProjectCreatedTitle(session.created);
        const timeHtml = createdLabel
          ? `<span class="project-time" title="${escapeHtml(createdTitle)}">${escapeHtml(createdLabel)}</span>`
          : "";
        return `
        <div class="project-item ${isActive ? "active" : ""}" data-session-id="${session.session_id}" data-group-no="${escapeHtml(groupNo)}">
          <div class="project-top">
            <div class="project-title-row">
              ${groupBadge}
              <div class="project-name" title="${escapeHtml(session.app_name)}">${escapeHtml(session.app_name)}</div>
            </div>
            ${timeHtml}
          </div>
          <div class="project-bottom">
            <div class="project-step">
              <span class="step-badge">${escapeHtml(stepName)}</span>
            </div>
            <button type="button" class="project-delete" data-session-id="${session.session_id}" title="删除项目">
              删除
            </button>
          </div>
        </div>
        `;
      })
      .join("");

    // 绑定点击事件 - 使用事件委托避免重复绑定
    attachProjectListeners(container);
  } finally {
    _renderingProjects = false;
  }
}

// ⚠️ 独立的事件委托函数，避免在 renderProjectsList 中重复添加多个监听器
function attachProjectListeners(container) {
  // 移除旧的监听器
  const newContainer = container.cloneNode(true);
  container.parentNode.replaceChild(newContainer, container);

  // 重新获取容器引用（DOM 已更新）
  const updatedContainer = document.getElementById("projectsList");

  // 使用事件委托：只在容器上添加一个监听器
  updatedContainer.addEventListener("click", (e) => {
    const deleteBtn = e.target.closest(".project-delete");
    if (deleteBtn) {
      e.stopPropagation();
      const sessionIdToDelete = deleteBtn.dataset.sessionId;
      deleteProject(sessionIdToDelete);
      return;
    }

    const projectItem = e.target.closest(".project-item");
    if (projectItem) {
      const sid = projectItem.dataset.sessionId;
      switchProject(sid);
    }
  });
}

/**
 * 仅更新项目列表中的高亮状态，不重新渲染整个列表。
 * 替代 renderProjectsList 用于切换项目后的轻量级 UI 更新。
 */
function highlightProjectInList(sid) {
  const container = document.getElementById("projectsList");
  if (!container) return;
  container.querySelectorAll(".project-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.sessionId === sid);
  });
}

async function switchProject(newSessionId) {
  // 防止并发切换
  if (_switchingProject) return;
  _switchingProject = true;

  try {
    // 停止当前的事件流
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }

    // 加载新会话的状态
    const response = await fetch(`/api/session/${newSessionId}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const newState = await response.json();

    // ⚠️ 重要：先更新 sessionId，再调用 applyState
    // 这样 applyState 内部的 refreshAllContent 能够正确识别当前会话
    sessionId = newSessionId;
    state = newState;

    // 应用状态（这会触发 UI 更新，包括项目列表高亮）
    applyState(newState, { autoNavigate: true });

    // ✅ 只更新高亮 class，不重新渲染整个列表
    // 避免 renderProjectsList → attachProjectListeners → cloneNode 链
    // 以及避免与 5 秒定时器的竞态
    highlightProjectInList(newSessionId);

    // 重新订阅事件
    subscribeEvents();
  } catch (err) {
    console.error("切换项目失败:", err);
    alert("切换项目失败: " + err.message);
  } finally {
    _switchingProject = false;
  }
}

// 监听状态变化，更新项目列表（只更新缓存和步骤标签，不重新渲染）
const originalApplyState = applyState;
applyState = function (newState, opts = {}) {
  originalApplyState.call(this, newState, opts);

  // 更新缓存中的当前项目状态
  if (sessionId) {
    const idx = projectsCache.findIndex((s) => s.session_id === sessionId);
    if (idx >= 0) {
      projectsCache[idx] = { ...newState };
    }
  }

  // 轻量更新步骤 / 组号标签（不重新渲染整个列表）
  if (sessionId) {
    const item = document.querySelector(
      `.project-item[data-session-id="${sessionId}"]`
    );
    if (item) {
      const badge = item.querySelector(".step-badge");
      if (badge) {
        badge.textContent = STEP_NAMES[newState.step] || newState.step;
      }
      const groupNo = (newState.group_no || "").trim();
      item.dataset.groupNo = groupNo;
      const groupBadge = item.querySelector(".group-badge");
      if (groupBadge) {
        if (groupNo) {
          groupBadge.className = "group-badge";
          groupBadge.title = `组号：${groupNo}`;
          groupBadge.textContent = groupNo;
        } else {
          groupBadge.className = "group-badge ungrouped";
          groupBadge.title = "未填写组号";
          groupBadge.textContent = "未分组";
        }
      }
    }
  }
};

// 定期刷新项目列表（每 5 秒）
setInterval(loadProjectsList, 5000);

// ============================================================
// 项目删除功能
// ============================================================

async function deleteProject(sessionIdToDelete) {
  // 确认删除
  const appName = projectsCache.find((p) => p.session_id === sessionIdToDelete)?.app_name || "项目";
  if (!confirm(`确定要删除项目 "${appName}" 吗？此操作无法撤销。`)) {
    return;
  }

  try {
    // 调用删除 API
    const response = await fetch(`/api/session/${sessionIdToDelete}`, {
      method: "DELETE",
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.detail || `HTTP ${response.status}`);
    }

    // 如果删除的是当前项目，切换到第一个项目或重置
    if (sessionId === sessionIdToDelete) {
      eventSource?.close();
      eventSource = null;
      sessionId = null;
      state = null;
      currentPanel = "input";

      // 重新加载列表
      await loadProjectsList();

      // 如果还有其他项目，切换到第一个
      if (projectsCache.length > 0) {
        switchProject(projectsCache[0].session_id);
      } else {
        // 没有项目了，显示初始界面
        showPanel("input", true);
        updatePageTitle("");
        document.getElementById("sessionInfo").textContent = "未选择项目";
      }
    } else {
      // 刷新列表，移除已删除的项目
      await loadProjectsList();
    }

    console.log(`项目 "${appName}" 已删除`);
  } catch (err) {
    console.error("删除项目失败:", err);
    alert("删除项目失败: " + err.message);
  }
}
