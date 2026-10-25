import { api } from "./api.js";
import { element, formatTime, setMessage, statusLabel } from "./common.js";

const PAGE_SIZE = 20;
const POLL_INTERVAL_MS = 5000;
const state = { offset: 0, status: "", total: 0, loading: false, timer: null };

const nodes = {
  body: document.querySelector("#task-table-body"),
  empty: document.querySelector("#empty-state"),
  message: document.querySelector("#page-message"),
  refresh: document.querySelector("#refresh-button"),
  refreshTime: document.querySelector("#refresh-time"),
  filter: document.querySelector("#status-filter"),
  previous: document.querySelector("#previous-page"),
  next: document.querySelector("#next-page"),
  pageInfo: document.querySelector("#page-info"),
  health: document.querySelector("#health-indicator"),
  healthText: document.querySelector("#health-text"),
  dialog: document.querySelector("#create-dialog"),
  form: document.querySelector("#create-form"),
  formMessage: document.querySelector("#form-message"),
  submit: document.querySelector("#submit-create"),
};

function renderStatus(status) {
  return element("span", `status status-${status}`, statusLabel(status));
}

function renderProgress(value) {
  const wrapper = element("div", "progress");
  const track = element("div", "progress-track");
  const bar = element("div", "progress-bar");
  bar.style.width = `${Math.max(0, Math.min(100, value))}%`;
  track.append(bar);
  wrapper.append(track, element("span", "", `${value}%`));
  return wrapper;
}

function renderTasks(tasks) {
  nodes.body.replaceChildren();
  nodes.empty.classList.toggle("hidden", tasks.length !== 0);
  for (const task of tasks) {
    const row = document.createElement("tr");
    const taskCell = document.createElement("td");
    const taskLink = element("a", "task-link", task.task_no);
    taskLink.href = `/tasks/${encodeURIComponent(task.id)}`;
    taskLink.title = task.id;
    taskCell.append(taskLink);
    const values = [
      taskCell,
      element("td", "", task.task_type),
      (() => { const cell = document.createElement("td"); cell.append(renderStatus(task.status)); return cell; })(),
      (() => { const cell = document.createElement("td"); cell.append(renderProgress(task.progress)); return cell; })(),
      element("td", "", formatTime(task.created_at)),
      element("td", "", formatTime(task.started_at)),
      element("td", "", formatTime(task.finished_at)),
      (() => {
        const cell = document.createElement("td");
        const link = element("a", "button button-small button-secondary", "查看详情");
        link.href = `/tasks/${encodeURIComponent(task.id)}`;
        cell.append(link);
        return cell;
      })(),
    ];
    row.append(...values);
    nodes.body.append(row);
  }
}

function renderCounts(total, counts) {
  document.querySelector("#count-total").textContent = String(total);
  document.querySelector("#count-queued").textContent = String(counts.QUEUED || 0);
  document.querySelector("#count-running").textContent = String(counts.RUNNING || 0);
  document.querySelector("#count-retry").textContent = String(counts.RETRY_WAIT || 0);
  document.querySelector("#count-succeeded").textContent = String(counts.SUCCEEDED || 0);
  document.querySelector("#count-failed").textContent = String(counts.FAILED || 0);
}

async function checkHealth() {
  try {
    await api.health();
    nodes.health.className = "health health-ok";
    nodes.healthText.textContent = "后端连接正常";
  } catch {
    nodes.health.className = "health health-error";
    nodes.healthText.textContent = "后端不可用";
  }
}

async function loadTasks({ quiet = false } = {}) {
  if (state.loading) return;
  state.loading = true;
  nodes.refresh.disabled = true;
  if (!quiet) setMessage(nodes.message, "正在加载任务…");
  try {
    const data = await api.listTasks({ status: state.status, limit: PAGE_SIZE, offset: state.offset });
    state.total = data.total;
    renderTasks(data.items);
    renderCounts(Object.values(data.status_counts).reduce((sum, count) => sum + count, 0), data.status_counts);
    const page = Math.floor(state.offset / PAGE_SIZE) + 1;
    const pageCount = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
    nodes.pageInfo.textContent = `第 ${page} / ${pageCount} 页，共 ${data.total} 条`;
    nodes.previous.disabled = state.offset === 0;
    nodes.next.disabled = state.offset + PAGE_SIZE >= data.total;
    nodes.refreshTime.textContent = `最近刷新：${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    setMessage(nodes.message);
  } catch (error) {
    setMessage(nodes.message, error.message, "error");
  } finally {
    state.loading = false;
    nodes.refresh.disabled = false;
    await checkHealth();
  }
}

function schedulePolling() {
  window.clearInterval(state.timer);
  state.timer = window.setInterval(() => {
    if (document.visibilityState === "visible") loadTasks({ quiet: true });
  }, POLL_INTERVAL_MS);
}

function createPayload() {
  const sleep = Number(document.querySelector("#sleep-input").value || 0);
  const sleepSeconds = Object.fromEntries([
    "validate_asset", "transcode_video", "extract_keyframes",
    "model_inference", "generate_report", "package_result",
  ].map((key) => [key, sleep]));
  const idempotency = document.querySelector("#idempotency-input").value.trim();
  return {
    task_type: "animation",
    input: {
      source: document.querySelector("#source-input").value.trim(),
      model_version: document.querySelector("#model-version-input").value.trim(),
      mock: { sleep_seconds: sleepSeconds },
    },
    idempotency_key: idempotency || null,
  };
}

nodes.refresh.addEventListener("click", () => loadTasks());
nodes.filter.addEventListener("change", () => { state.status = nodes.filter.value; state.offset = 0; loadTasks(); });
nodes.previous.addEventListener("click", () => { state.offset = Math.max(0, state.offset - PAGE_SIZE); loadTasks(); });
nodes.next.addEventListener("click", () => { state.offset += PAGE_SIZE; loadTasks(); });
document.querySelector("#create-button").addEventListener("click", () => nodes.dialog.showModal());
document.querySelector("#close-dialog").addEventListener("click", () => nodes.dialog.close());
document.querySelector("#cancel-create").addEventListener("click", () => nodes.dialog.close());
nodes.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  nodes.submit.disabled = true;
  setMessage(nodes.formMessage, "正在创建任务…");
  try {
    const task = await api.createTask(createPayload());
    window.location.assign(`/tasks/${encodeURIComponent(task.id)}`);
  } catch (error) {
    setMessage(nodes.formMessage, error.message, "error");
  } finally {
    nodes.submit.disabled = false;
  }
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") loadTasks({ quiet: true });
});
window.addEventListener("beforeunload", () => window.clearInterval(state.timer));

loadTasks();
schedulePolling();
