export const TASK_STATUS = {
  QUEUED: "等待执行",
  RUNNING: "执行中",
  RETRY_WAIT: "等待重试",
  SUCCEEDED: "已成功",
  FAILED: "已失败",
  CANCEL_REQUESTED: "正在取消",
  CANCELED: "已取消",
};

export const STEP_STATUS = {
  BLOCKED: "依赖未满足",
  PENDING: "等待执行",
  RUNNING: "执行中",
  RETRY_WAIT: "等待重试",
  SUCCEEDED: "已成功",
  FAILED: "已失败",
  SKIPPED: "已跳过",
  CANCEL_REQUESTED: "正在取消",
  CANCELED: "已取消",
};

export const EVENT_LABELS = {
  TASK_CREATED: "任务已创建",
  TASK_STATUS_CHANGED: "任务状态变化",
  STEP_READY: "步骤依赖已满足",
  STEP_STARTED: "步骤开始执行",
  STEP_SUCCEEDED: "步骤执行成功",
  STEP_FAILED: "步骤最终失败",
  STEP_RETRY_SCHEDULED: "步骤等待重试",
  STEP_RECOVERED: "步骤已安排恢复",
  STEP_CANCELED: "步骤已取消",
  LEASE_EXPIRED: "Worker 租约过期",
  ARTIFACT_RECOVERED: "已恢复并登记产物",
  CACHE_HIT: "命中结果缓存",
  RESOURCE_WAIT: "等待模拟 GPU 资源",
  RESOURCE_ALLOCATED: "已分配模拟 GPU 资源",
  RESOURCE_RELEASED: "已释放模拟 GPU 资源",
  CANCEL_REQUESTED: "收到取消请求",
  MANUAL_RETRY_REQUESTED: "收到手动重试请求",
};

export const STEP_NAMES = {
  validate_asset: "素材校验",
  transcode_video: "视频预处理",
  extract_keyframes: "关键帧提取",
  model_inference: "模型推理",
  generate_report: "报告生成",
  package_result: "结果打包",
};

export function stepName(step) {
  return STEP_NAMES[step.step_key] || step.step_name || step.step_key;
}

export function statusLabel(status) {
  return TASK_STATUS[status] || STEP_STATUS[status] || `未知状态：${status}`;
}

export function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
}

export function setMessage(element, message = "", type = "") {
  element.textContent = message;
  element.className = `message${type ? ` ${type}` : ""}${message ? "" : " hidden"}`;
}

export function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  return node;
}
