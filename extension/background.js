// 서버 통신 전담. content script는 AI 사이트 Origin으로 요청하게 되어 CORS에 막히므로
// /api/scan, /api/logs 호출은 모두 이 service worker에서 한다.
// 원문 텍스트는 저장·로깅하지 않고, 검사 요청 본문으로만 서버에 보낸다.

const DEFAULTS = { serverUrl: "http://localhost:8000", failClosed: true };
const TIMEOUT_MS = 5000;
const SCAN_LIMIT = 50000; // /api/scan 텍스트 상한(자)
const OVERLAP = 200; // 조각 경계에 걸린 값을 놓치지 않도록 겹치는 길이

async function settings() {
  const saved = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...saved, serverUrl: String(saved.serverUrl || DEFAULTS.serverUrl).replace(/\/+$/, "") };
}

async function call(path, init) {
  const { serverUrl } = await settings();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(serverUrl + path, {
      ...init,
      headers: { "Content-Type": "application/json", "X-Source": "extension" },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error("HTTP " + response.status);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

function chunks(text) {
  if (text.length <= SCAN_LIMIT) return [text];
  const parts = [];
  for (let start = 0; start < text.length; start += SCAN_LIMIT - OVERLAP) {
    parts.push(text.slice(start, start + SCAN_LIMIT));
    if (start + SCAN_LIMIT >= text.length) break;
  }
  return parts;
}

async function scan(text) {
  const types = new Set();
  for (const part of chunks(text)) {
    const result = await call("/api/scan", { method: "POST", body: JSON.stringify({ text: part }) });
    for (const finding of result.findings) types.add(finding.type);
  }
  return { ok: true, blocked: types.size > 0, types: [...types] };
}

async function log(types) {
  // 유형만 보낸다. 실패해도 차단 동작에는 영향을 주지 않는다.
  try {
    await call("/api/logs", { method: "POST", body: JSON.stringify({ event: "BLOCK", types, source: "extension" }) });
    return { ok: true };
  } catch {
    return { ok: false };
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const task =
    message.kind === "scan"
      ? scan(String(message.text ?? "")).catch(() => ({ ok: false }))
      : message.kind === "log"
        ? log(message.types)
        : message.kind === "health"
          ? call("/api/health", { method: "GET" }).then(() => ({ ok: true }), () => ({ ok: false }))
          : null;
  if (!task) return false;
  task.then(sendResponse);
  return true; // 비동기 응답
});
