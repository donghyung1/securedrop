// SecureDrop Cloud API 클라이언트
// - 요청·응답 형식은 A의 백엔드(backend/README.md "API 계약")를 따른다.
// - USE_MOCK 이 true 이면 서버 없이 mock 응답을 돌려준다 (서버 없이 화면 개발용).
//   전환: URL에 ?api=real 또는 ?api=mock 을 붙이면 localStorage에 기억된다.
// - 서버는 Origin 헤더로 웹 요청을 인증하므로 프론트 origin이 서버 ALLOWED_ORIGINS에 있어야 한다.

const params = new URLSearchParams(location.search);
const apiParam = params.get("api");
if (apiParam === "real" || apiParam === "mock") {
  try { localStorage.setItem("sd.api", apiParam); } catch { /* 저장 불가 시 무시 */ }
}
let savedMode = null;
try { savedMode = localStorage.getItem("sd.api"); } catch { /* 저장소 차단 */ }

export const CONFIG = {
  BASE_URL: "http://localhost:8000",
  USE_MOCK: (apiParam || savedMode || "mock") === "mock",
  MAX_FILE_MB: 20,        // 업로드 상한 (서버 MAX_FILE_MB)
  SCAN_FILE_MAX_MB: 5,    // 파일 검사 상한 (서버 SCAN_FILE_MAX_MB)
  SCANNABLE_EXTS: ["txt", "csv"],
  // 서버 ALLOWED_EXTENSIONS와 동일하게 유지
  ALLOWED_EXTS: ["txt", "csv", "json", "md", "log", "xml", "yaml", "yml", "pdf", "png", "jpg", "jpeg", "gif", "webp"],
  EXPIRES_OPTIONS: [
    { sec: 300, label: "5분" },
    { sec: 600, label: "10분" },
    { sec: 3600, label: "60분" },
  ],
  DEFAULT_EXPIRES_SEC: 600,
};

// 탐지 유형 코드 → 화면 라벨 (서버 detector.TYPES)
export const TYPE_LABELS = {
  RRN: "주민등록번호",
  PHONE: "휴대전화번호",
  EMAIL: "이메일",
  AWS_ACCESS_KEY: "AWS Access Key",
  API_KEY: "API Key·토큰",
  CARD: "카드번호",
};
export const typeLabel = (t) => TYPE_LABELS[t] || t;

// 오류 코드 → 사용자용 메시지. 서버 message가 있으면 그것을 우선 쓴다.
const ERROR_MESSAGES = {
  VALIDATION_ERROR: "입력값이 올바르지 않습니다.",
  FILE_TOO_LARGE: "파일 크기 상한을 초과했습니다.",
  UNAUTHORIZED: "인증에 실패했습니다.",
  APPROVAL_REQUIRED: "전송 전 승인이 필요합니다.",
  NOT_FOUND: "존재하지 않거나 폐기된 전송입니다.",
  EXPIRED: "만료되었습니다.",
  SENSITIVE_DATA: "민감정보가 감지되었습니다.",
  SENSITIVE_REMAINS: "처리본에 민감정보가 남아 있습니다.",
  UNSUPPORTED_TYPE: "민감정보 검사를 지원하지 않는 파일 형식입니다.",
  RATE_LIMITED: "요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
  STORAGE_ERROR: "파일 저장소 오류가 발생했습니다.",
  CAPACITY_EXCEEDED: "서버가 바쁩니다. 잠시 후 다시 시도하세요.",
  INTERNAL_ERROR: "서버 오류가 발생했습니다.",
  NETWORK_ERROR: "서버에 연결할 수 없습니다.",
};

// body: 오류 응답 전체. 409/415는 findings, confirmation_required 등을 함께 준다.
export class ApiError extends Error {
  constructor(code, message, status, body = null) {
    super(message || ERROR_MESSAGES[code] || "알 수 없는 오류가 발생했습니다.");
    this.code = code;
    this.status = status;
    this.body = body;
  }
  get userMessage() {
    return this.message || ERROR_MESSAGES[this.code];
  }
}

async function request(path, { method = "GET", json, form } = {}) {
  const opts = { method, headers: {} };
  if (json !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(json);
  } else if (form) {
    opts.body = form; // multipart 경계는 브라우저가 채운다
  }
  let res;
  try {
    res = await fetch(CONFIG.BASE_URL + path, opts);
  } catch {
    throw new ApiError("NETWORK_ERROR", null, 0);
  }
  let body = null;
  try { body = await res.json(); } catch { /* 본문 없음 */ }
  if (!res.ok) {
    const err = body?.error;
    throw new ApiError(err?.code || "INTERNAL_ERROR", err?.message, res.status, body);
  }
  return body;
}

// ---------------------------------------------------------------- 실제 API

const realApi = {
  health: () => request("/api/health"),

  scanText: (text) => request("/api/scan", { method: "POST", json: { text } }),

  scanFile(file) {
    const form = new FormData();
    form.append("file", file);
    return request("/api/scan/file", { method: "POST", form });
  },

  // actions: [{ action: "MASK" | "DELETE", targets: ["RRN", ...] }, ...]
  sanitize(file, actions) {
    const form = new FormData();
    form.append("file", file);
    form.append("actions", JSON.stringify(actions));
    return request("/api/sanitize", { method: "POST", form });
  },

  cancelSanitize: (id) =>
    request(`/api/sanitize/${encodeURIComponent(id)}`, { method: "DELETE" }),

  // context: { filename, findings: [{type, count}] } — 원문은 보내지 않는다
  assist: (message, context) =>
    request("/api/assist", { method: "POST", json: { message, context } }),

  // file 또는 sanitizeId 중 하나만.
  // confirmSensitive: 민감정보가 있어도 원본 전송 확인 / confirmUnscanned: 검사 미지원 형식 전송 확인
  // userApproved: 처리본 미리보기 승인 (sanitizeId 전송 시 필수)
  createTransfer({ file, sanitizeId, expiresIn, confirmSensitive = false, confirmUnscanned = false, userApproved = false }) {
    const form = new FormData();
    if (sanitizeId) form.append("sanitize_id", sanitizeId);
    else form.append("file", file);
    form.append("expires_in", String(expiresIn));
    if (confirmSensitive) form.append("confirm_sensitive", "true");
    if (confirmUnscanned) form.append("confirm_unscanned", "true");
    if (userApproved) form.append("user_approved", "true");
    return request("/api/transfers", { method: "POST", form });
  },

  getTransfer: (id) => request(`/api/transfers/${encodeURIComponent(id)}`),

  revokeTransfer: (id) =>
    request(`/api/transfers/${encodeURIComponent(id)}`, { method: "DELETE" }),

  // 웹은 BLOCK만 보낼 수 있다. 정제·전송·폐기 이벤트는 서버가 기록한다.
  getLogs: (limit = 50) => request(`/api/logs?limit=${limit}`),
};

// ---------------------------------------------------------------- mock API
// 화면 개발용. 서버 응답·오류 형식을 흉내 낸다.
// 탐지 정규식은 근사치이고(체크섬 검사 없음) 실제 판정은 서버 detector.py가 한다.

const MOCK_PATTERNS = {
  RRN: /\b\d{6}-[1-4]\d{6}\b/g,
  PHONE: /\b01[016789]-?\d{3,4}-?\d{4}\b/g,
  EMAIL: /\b[\w.+-]+@[\w-]+\.[\w.-]+\b/g,
  AWS_ACCESS_KEY: /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g,
  API_KEY: /\b(?:sk|ghp|xox[bp])[-_][A-Za-z0-9_-]{16,}\b/g,
  CARD: /\b\d{4}-\d{4}-\d{4}-\d{4}\b/g,
};

const delay = (ms) => new Promise((r) => setTimeout(r, ms));
const extOf = (name) => (name.split(".").pop() || "").toLowerCase();
const MB = 1024 * 1024;

function mockDetect(text) {
  const findings = [];
  for (const [type, re] of Object.entries(MOCK_PATTERNS)) {
    const count = (text.match(re) || []).length;
    if (count) findings.push({ type, count });
  }
  return { blocked: findings.length > 0, findings };
}

function mockQr(url) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220" viewBox="0 0 220 220">
    <rect width="220" height="220" fill="#fff"/>
    <rect x="10" y="10" width="200" height="200" fill="none" stroke="#111" stroke-width="4" stroke-dasharray="8 6"/>
    <text x="110" y="105" font-family="sans-serif" font-size="22" text-anchor="middle" fill="#111">MOCK QR</text>
    <text x="110" y="135" font-family="sans-serif" font-size="11" text-anchor="middle" fill="#555">${url.slice(0, 30)}</text>
  </svg>`;
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

const mockDrafts = new Map();   // sanitize_id → { filename, text }
const mockTransfers = new Map();
const mockLogs = [];
const mockLog = (event, types = []) =>
  mockLogs.unshift({ time: new Date().toISOString(), event, types, source: "web" });

const mockApi = {
  async health() {
    await delay(100);
    return { status: "ok", region: "ap-northeast-2", mock: true };
  },

  async scanText(text) {
    await delay(200);
    if (text.length > 50000) throw new ApiError("VALIDATION_ERROR", "텍스트는 50,000자 이하여야 합니다.", 422);
    return mockDetect(text);
  },

  async scanFile(file) {
    await delay(500);
    if (!CONFIG.SCANNABLE_EXTS.includes(extOf(file.name))) throw new ApiError("UNSUPPORTED_TYPE", null, 415);
    if (file.size > CONFIG.SCAN_FILE_MAX_MB * MB) throw new ApiError("FILE_TOO_LARGE", null, 413);
    if (file.size === 0) throw new ApiError("VALIDATION_ERROR", "빈 파일은 처리할 수 없습니다.", 422);
    return { filename: file.name, ...mockDetect(await file.text()) };
  },

  async sanitize(file, actions) {
    await delay(700);
    if (!Array.isArray(actions) || actions.length === 0) {
      throw new ApiError("VALIDATION_ERROR", "정제 조치를 선택하세요.", 422);
    }
    const text = await file.text();
    const before = mockDetect(text).findings;
    let out = text;
    for (const { action, targets } of actions) {
      for (const type of targets) {
        const re = MOCK_PATTERNS[type];
        if (!re) continue;
        out = out.replace(re, (m) => {
          if (action === "DELETE") return "";
          if (type === "RRN") return m.slice(0, 8) + "******";
          if (type === "PHONE") return m.replace(/^(\d{3}-?)\d{3,4}(-?\d{4})$/, "$1****$2");
          return "[REDACTED]";
        });
      }
    }
    const after = mockDetect(out).findings;
    const id = "mock-san-" + Math.random().toString(36).slice(2, 10);
    mockDrafts.set(id, { filename: file.name, text: out });
    mockLog("SANITIZE", before.map((f) => f.type));
    return {
      sanitize_id: id,
      findings_before: before,
      findings_after: after,
      preview: out.split("\n").slice(0, 20).join("\n"),
      clean: after.length === 0,
    };
  },

  async cancelSanitize(id) {
    await delay(100);
    mockDrafts.delete(id);
    return { status: "cancelled" };
  },

  async assist(message, context) {
    await delay(300);
    const options = [
      { label: "마스킹", action: "MASK", targets: (context?.findings || []).map((f) => f.type) },
      { label: "해당 항목 삭제", action: "DELETE", targets: (context?.findings || []).map((f) => f.type) },
      { label: "전송 취소", action: "CANCEL", targets: [] },
    ];
    if (/취소|그만/.test(message)) return { action: "CANCEL", targets: [], reply: "전송을 취소했습니다.", options };
    const targets = Object.entries(TYPE_LABELS)
      .filter(([, label]) => message.includes(label.slice(0, 4)))
      .map(([code]) => code);
    if (/삭제|지워/.test(message)) return { action: "DELETE", targets, reply: "해당 항목을 삭제하겠습니다.", options };
    if (/가려|마스킹|숨겨/.test(message)) return { action: "MASK", targets, reply: "해당 항목을 마스킹하겠습니다.", options };
    return { action: "CLARIFY", targets: [], reply: "마스킹·삭제·취소 중 어떤 조치를 원하시나요?", options };
  },

  async createTransfer({ file, sanitizeId, expiresIn, confirmSensitive, confirmUnscanned, userApproved }) {
    await delay(600);
    if (![300, 600, 3600].includes(expiresIn)) {
      throw new ApiError("VALIDATION_ERROR", "만료 시간은 300, 600, 3600초 중 선택하세요.", 422);
    }
    let filename, text = null;
    if (sanitizeId) {
      if (!userApproved) throw new ApiError("APPROVAL_REQUIRED", "처리본 미리보기 확인 후 승인이 필요합니다.", 403);
      const draft = mockDrafts.get(sanitizeId);
      if (!draft) throw new ApiError("EXPIRED", null, 410);
      ({ filename, text } = draft);
    } else {
      filename = file.name;
      if (!CONFIG.ALLOWED_EXTS.includes(extOf(filename))) throw new ApiError("VALIDATION_ERROR", "허용되지 않는 파일 형식입니다.", 422);
      if (file.size > CONFIG.MAX_FILE_MB * MB) throw new ApiError("FILE_TOO_LARGE", null, 413);
      if (CONFIG.SCANNABLE_EXTS.includes(extOf(filename))) text = await file.text();
    }
    if (text === null && !confirmUnscanned) {
      throw new ApiError("UNSUPPORTED_TYPE", "이 형식은 민감정보 검사를 지원하지 않습니다. 확인 후 전송하세요.", 415,
        { confirmation_required: true, scan_supported: false });
    }
    const types = [];
    if (text !== null) {
      const result = mockDetect(text);
      types.push(...result.findings.map((f) => f.type));
      if (result.blocked && !confirmSensitive) {
        mockLog("BLOCK", types);
        throw new ApiError(sanitizeId ? "SENSITIVE_REMAINS" : "SENSITIVE_DATA",
          "민감정보가 감지되었습니다. 정제하거나 위험을 확인한 후 다시 전송하세요.", 409,
          { ...result, confirmation_required: true, sanitize_available: true });
      }
    }
    if (sanitizeId) mockDrafts.delete(sanitizeId);
    const id = "mock-" + Math.random().toString(36).slice(2, 10);
    const downloadUrl = `https://example.invalid/d/${id}`;
    const expiresAt = new Date(Date.now() + expiresIn * 1000).toISOString();
    mockTransfers.set(id, { id, status: "active", expires_at: expiresAt, downloaded: false });
    mockLog("TRANSFER", types);
    return { id, url: downloadUrl, download_url: downloadUrl, qr: mockQr(downloadUrl), expires_at: expiresAt };
  },

  async getTransfer(id) {
    await delay(150);
    const t = mockTransfers.get(id);
    if (!t || t.status === "revoked") throw new ApiError("NOT_FOUND", null, 404);
    if (Date.parse(t.expires_at) < Date.now()) throw new ApiError("EXPIRED", null, 410);
    return { ...t };
  },

  async revokeTransfer(id) {
    await delay(200);
    const t = mockTransfers.get(id);
    if (!t || t.status === "revoked") throw new ApiError("NOT_FOUND", null, 404);
    t.status = "revoked";
    mockLog("REVOKE");
    return { id, status: "revoked" };
  },

  async getLogs(limit = 50) {
    await delay(200);
    return { items: mockLogs.slice(0, limit) };
  },
};

export const api = CONFIG.USE_MOCK ? mockApi : realApi;
