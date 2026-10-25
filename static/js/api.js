const DEFAULT_TIMEOUT_MS = 10000;

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) {
      const detail = typeof data?.detail === "string" ? data.detail : `请求失败（${response.status}）`;
      throw new ApiError(detail, response.status);
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new ApiError("请求超时，请稍后重试");
    if (error instanceof ApiError) throw error;
    throw new ApiError("无法连接后端服务");
  } finally {
    window.clearTimeout(timeout);
  }
}

export const api = {
  health: () => request("/health"),
  listTasks: ({ status = "", limit = 20, offset = 0 } = {}) => {
    const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (status) query.set("status", status);
    return request(`/api/tasks?${query}`);
  },
  createTask: (payload) => request("/api/tasks", { method: "POST", body: JSON.stringify(payload) }),
  getTask: (taskId) => request(`/api/tasks/${encodeURIComponent(taskId)}`),
  getSteps: (taskId) => request(`/api/tasks/${encodeURIComponent(taskId)}/steps`),
  getAttempts: (taskId, stepKey) => request(
    `/api/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepKey)}/attempts`,
  ),
  getEvents: (taskId) => request(`/api/tasks/${encodeURIComponent(taskId)}/events`),
  retryTask: (taskId) => request(`/api/tasks/${encodeURIComponent(taskId)}/retry`, { method: "POST" }),
  cancelTask: (taskId) => request(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" }),
};
