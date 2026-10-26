import { api } from "./api.js";
import {
  EVENT_LABELS,
  element,
  formatTime,
  setMessage,
  statusLabel,
  stepName,
} from "./common.js";

const ACTIVE_STATUSES = new Set(["QUEUED", "RUNNING", "RETRY_WAIT", "CANCEL_REQUESTED"]);
const POLL_INTERVAL_MS = 2500;
const EVENT_PAGE_LIMIT = 200;
const MAX_EVENT_PAGES_PER_REFRESH = 5;
const taskId = decodeURIComponent(window.location.pathname.split("/").filter(Boolean).at(-1) || "");
const state = {
  loading: false,
  actionLoading: false,
  pendingAction: null,
  timer: null,
  task: null,
  steps: [],
  attempts: new Map(),
  events: [],
  lastEventId: 0,
};

const nodes = {
  message: document.querySelector("#page-message"),
  overview: document.querySelector("#task-overview"),
  title: document.querySelector("#task-title"),
  taskId: document.querySelector("#task-id"),
  refresh: document.querySelector("#refresh-button"),
  health: document.querySelector("#health-indicator"),
  healthText: document.querySelector("#health-text"),
  steps: document.querySelector("#step-flow"),
  attempts: document.querySelector("#attempt-sections"),
  events: document.querySelector("#event-timeline"),
  artifacts: document.querySelector("#artifact-list"),
  retry: document.querySelector("#retry-button"),
  cancel: document.querySelector("#cancel-button"),
  confirmDialog: document.querySelector("#confirm-dialog"),
  confirmTitle: document.querySelector("#confirm-title"),
  confirmText: document.querySelector("#confirm-text"),
  confirmSubmit: document.querySelector("#confirm-submit"),
  actionMessage: document.querySelector("#action-message"),
};

function statusNode(status) {
  return element("span", `status status-${status}`, statusLabel(status));
}

function setOverview(task) {
  nodes.title.textContent = task.task_no;
  nodes.taskId.textContent = task.id;
  document.title = `${task.task_no}｜动画任务观测平台`;
  document.querySelector("#task-status").replaceChildren(statusNode(task.status));
  document.querySelector("#task-progress").textContent = `${task.progress}%`;
  document.querySelector("#task-type").textContent = task.task_type;
  document.querySelector("#task-created").textContent = formatTime(task.created_at);
  document.querySelector("#task-started").textContent = formatTime(task.started_at);
  document.querySelector("#task-finished").textContent = formatTime(task.finished_at);
  document.querySelector("#task-error").textContent = task.error_message
    ? `${task.error_code || "未分类错误"}：${task.error_message}` : "—";
  nodes.overview.classList.remove("hidden");
  nodes.retry.classList.toggle("hidden", task.status !== "FAILED");
  nodes.cancel.classList.toggle(
    "hidden",
    !new Set(["QUEUED", "RUNNING", "RETRY_WAIT"]).has(task.status),
  );
}

function latestAttempt(stepKey) {
  const attempts = state.attempts.get(stepKey) || [];
  return attempts.at(-1) || null;
}

function renderSteps() {
  nodes.steps.replaceChildren();
  state.steps.forEach((step, index) => {
    const attempt = latestAttempt(step.step_key);
    const card = element("article", `step-card status-${step.status}`);
    card.append(
      element("span", "step-index", `步骤 ${index + 1}`),
      element("h3", "", stepName(step)),
      statusNode(step.status),
    );
    const meta = element("div", "step-meta");
    const duration = attempt?.duration_ms == null ? "—" : `${attempt.duration_ms} ms`;
    const dependency = step.depends_on.length ? step.depends_on.join("、") : "无";
    [
      `标识：${step.step_key}`,
      `依赖：${dependency}`,
      `尝试：${step.attempt_count} / ${step.max_attempts}`,
      `最近耗时：${duration}`,
      `开始：${formatTime(step.started_at)}`,
      `完成：${formatTime(step.finished_at)}`,
    ].forEach((text) => meta.append(element("span", "", text)));
    if (attempt?.error_message) meta.append(element("span", "error-text", `错误：${attempt.error_message}`));
    card.append(meta);
    nodes.steps.append(card);
  });
}

function tableCell(text) { return element("td", "", text); }

function renderAttempts() {
  nodes.attempts.replaceChildren();
  for (const step of state.steps) {
    const group = element("section", "attempt-group");
    group.append(element("h3", "", `${stepName(step)} · ${step.step_key}`));
    const attempts = state.attempts.get(step.step_key) || [];
    if (!attempts.length) {
      group.append(element("div", "empty-inline", "尚无执行尝试"));
      nodes.attempts.append(group);
      continue;
    }
    const wrap = element("div", "table-wrap");
    const table = document.createElement("table");
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    ["次数", "状态", "Worker", "开始", "完成", "耗时", "错误", "产物地址"].forEach(
      (label) => headRow.append(element("th", "", label)),
    );
    head.append(headRow);
    const body = document.createElement("tbody");
    for (const attempt of attempts) {
      const row = document.createElement("tr");
      const statusCell = document.createElement("td");
      statusCell.append(statusNode(attempt.status));
      row.append(
        tableCell(String(attempt.attempt_no)), statusCell, tableCell(attempt.worker_id),
        tableCell(formatTime(attempt.started_at)), tableCell(formatTime(attempt.finished_at)),
        tableCell(attempt.duration_ms == null ? "—" : `${attempt.duration_ms} ms`),
        tableCell(attempt.error_message || "—"), tableCell(attempt.output_uri || "—"),
      );
      body.append(row);
    }
    table.append(head, body); wrap.append(table); group.append(wrap); nodes.attempts.append(group);
  }
}

function renderEvents() {
  nodes.events.replaceChildren();
  const stepNames = new Map(state.steps.map((step) => [step.id, stepName(step)]));
  for (const event of state.events) {
    const item = element("li", "timeline-item");
    item.append(element("div", "timeline-title", EVENT_LABELS[event.event_type] || event.event_type));
    const context = [formatTime(event.created_at)];
    if (event.step_id) context.push(stepNames.get(event.step_id) || event.step_id);
    if (event.old_status || event.new_status) {
      context.push(`${event.old_status || "—"} → ${event.new_status || "—"}`);
    }
    item.append(element("div", "timeline-meta", context.join(" · ")));
    if (Object.keys(event.details_json || {}).length) {
      item.append(element("pre", "timeline-details", JSON.stringify(event.details_json, null, 2)));
    }
    nodes.events.append(item);
  }
}

function artifactCard(title, content) {
  const card = element("article", "artifact-card");
  card.append(element("h3", "", title), element("pre", "", content));
  return card;
}

function renderArtifacts() {
  nodes.artifacts.replaceChildren();
  nodes.artifacts.append(artifactCard("任务输入", JSON.stringify(state.task.input_json, null, 2)));
  if (state.task.result_json) {
    nodes.artifacts.append(artifactCard("最终结果", JSON.stringify(state.task.result_json, null, 2)));
  }
  for (const step of state.steps) {
    const attempt = latestAttempt(step.step_key);
    if (attempt?.output_uri) {
      nodes.artifacts.append(artifactCard(`${stepName(step)}产物`, attempt.output_uri));
    }
  }
  const cacheHits = state.events.filter((event) => event.event_type === "CACHE_HIT").length;
  const resourceEvents = state.events.filter((event) => event.event_type.startsWith("RESOURCE_"));
  nodes.artifacts.append(artifactCard("缓存信息", `缓存命中 ${cacheHits} 次`));
  nodes.artifacts.append(
    artifactCard(
      "模拟 GPU 资源",
      resourceEvents.length ? resourceEvents.map((event) => EVENT_LABELS[event.event_type] || event.event_type).join("\n") : "无资源事件",
    ),
  );
}

async function checkHealth() {
  try {
    await api.health(); nodes.health.className = "health health-ok"; nodes.healthText.textContent = "后端连接正常";
  } catch {
    nodes.health.className = "health health-error"; nodes.healthText.textContent = "后端不可用";
  }
}

async function fetchNewEvents() {
  const fresh = [];
  let afterId = state.lastEventId;
  for (let page = 0; page < MAX_EVENT_PAGES_PER_REFRESH; page += 1) {
    const batch = await api.getEvents(taskId, { afterId, limit: EVENT_PAGE_LIMIT });
    fresh.push(...batch);
    if (!batch.length) break;
    afterId = batch.at(-1).id;
    if (batch.length < EVENT_PAGE_LIMIT) break;
  }
  if (fresh.length) {
    const known = new Set(state.events.map((event) => event.id));
    state.events.push(...fresh.filter((event) => !known.has(event.id)));
    state.events.sort((left, right) => left.id - right.id);
    state.lastEventId = Math.max(state.lastEventId, ...fresh.map((event) => event.id));
  }
}

async function loadDetail({ quiet = false } = {}) {
  if (state.loading) return;
  state.loading = true; nodes.refresh.disabled = true;
  if (!quiet) setMessage(nodes.message, "正在读取任务详情…");
  try {
    const [task, steps] = await Promise.all([
      api.getTask(taskId), api.getSteps(taskId), fetchNewEvents(),
    ]);
    const attemptPairs = await Promise.all(
      steps.map(async (step) => [step.step_key, await api.getAttempts(taskId, step.step_key)]),
    );
    state.task = task; state.steps = steps; state.attempts = new Map(attemptPairs);
    setOverview(task); renderSteps(); renderAttempts(); renderEvents(); renderArtifacts();
    document.querySelector("#refresh-time").textContent = `最近刷新：${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    setMessage(nodes.message);
    if (!ACTIVE_STATUSES.has(task.status)) {
      window.clearInterval(state.timer);
      state.timer = null;
    }
  } catch (error) {
    setMessage(nodes.message, error.status === 404 ? "任务不存在或已被删除" : error.message, "error");
  } finally {
    state.loading = false; nodes.refresh.disabled = false; await checkHealth();
  }
}

function askForAction(action) {
  state.pendingAction = action;
  const retrying = action === "retry";
  nodes.confirmTitle.textContent = retrying ? "确认重试任务" : "确认取消任务";
  nodes.confirmText.textContent = retrying
    ? "后端只允许重试已失败任务。历史尝试会保留，并创建新的执行尝试。"
    : "取消是协作式操作。正在运行的步骤会在处理器返回后转为已取消。";
  nodes.confirmSubmit.textContent = retrying ? "确认重试" : "确认取消";
  nodes.confirmSubmit.className = retrying ? "button button-warning" : "button button-danger";
  setMessage(nodes.actionMessage);
  nodes.confirmDialog.showModal();
}

async function performAction(event) {
  event.preventDefault();
  if (!state.pendingAction || state.actionLoading) return;
  state.actionLoading = true;
  nodes.confirmSubmit.disabled = true;
  const action = state.pendingAction;
  setMessage(nodes.actionMessage, action === "retry" ? "正在提交重试请求…" : "正在提交取消请求…");
  try {
    if (action === "retry") await api.retryTask(taskId);
    else await api.cancelTask(taskId);
    nodes.confirmDialog.close();
    setMessage(nodes.message, action === "retry" ? "重试请求已提交" : "取消请求已提交", "success");
    await loadDetail({ quiet: true });
    if (ACTIVE_STATUSES.has(state.task.status) && !state.timer) {
      state.timer = window.setInterval(() => {
        if (document.visibilityState === "visible") loadDetail({ quiet: true });
      }, POLL_INTERVAL_MS);
    }
  } catch (error) {
    setMessage(nodes.actionMessage, error.message, "error");
  } finally {
    state.actionLoading = false;
    nodes.confirmSubmit.disabled = false;
  }
}

nodes.refresh.addEventListener("click", () => loadDetail());
nodes.retry.addEventListener("click", () => askForAction("retry"));
nodes.cancel.addEventListener("click", () => askForAction("cancel"));
nodes.confirmSubmit.addEventListener("click", performAction);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") loadDetail({ quiet: true });
});
window.addEventListener("beforeunload", () => window.clearInterval(state.timer));
loadDetail();
state.timer = window.setInterval(() => {
  if (document.visibilityState === "visible") loadDetail({ quiet: true });
}, POLL_INTERVAL_MS);
