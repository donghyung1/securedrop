// 공통 UI 도우미
let toastTimer = null;

export function toast(message, ms = 2500) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

export function showModeBadge(config) {
  const badge = document.getElementById("api-mode");
  if (!badge) return;
  badge.textContent = config.USE_MOCK ? "MOCK API" : "API " + config.API_BASE.replace(/^https?:\/\//, "");
  badge.classList.toggle("badge-mock", config.USE_MOCK);
}

export const fmtSize = (b) =>
  b < 1024 * 1024 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1024 / 1024).toFixed(1)} MB`;
