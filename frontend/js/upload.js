// 업로드 화면: STEP 1(파일 선택) → STEP 2(/api/scan/file 즉시 검사) → 전송 → QR
// 정제(STEP 3~4)는 Day 3에 assist.js / sanitize 흐름으로 연결한다.
import { api, CONFIG, ApiError, typeLabel } from "./api.js";
import * as guard from "./assist.js";
import { showTransfer, hideTransfer } from "./qr.js";
import { toast } from "./ui.js";

const $ = (id) => document.getElementById(id);
const MB = 1024 * 1024;

// 현재 선택된 파일의 상태
// scan: "idle" | "scanning" | "clean" | "blocked" | "unscannable" | "deferred" | "error"
//   unscannable: 검사 미지원 형식 → 사용자 확인 후 confirm_unscanned=true 로 전송
//   deferred:    5MB 초과 .txt/.csv → 미리 검사는 못 하지만 서버가 전송 시점에 검사한다
let state = { file: null, scan: "idle", findings: [], reason: "" };
let scanToken = 0; // 검사 중 다른 파일을 고르면 이전 응답은 버린다

const extOf = (name) => (name.split(".").pop() || "").toLowerCase();
const fmtSize = (b) => (b < MB ? `${(b / 1024).toFixed(1)} KB` : `${(b / MB).toFixed(1)} MB`);

export function initUpload() {
  const input = $("file-input");
  const drop = $("dropzone");

  input.addEventListener("change", () => input.files[0] && selectFile(input.files[0]));
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("is-over"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && selectFile(e.dataTransfer.files[0]));

  $("btn-reset").addEventListener("click", reset);
  $("confirm-check").addEventListener("change", updateSendButton);
  $("btn-send").addEventListener("click", send);

  const sel = $("expires");
  for (const o of CONFIG.EXPIRES_OPTIONS) {
    sel.add(new Option(o.label, o.sec, false, o.sec === CONFIG.DEFAULT_EXPIRES_SEC));
  }

  guard.onAction(handleGuardAction);
  reset();
}

function reset() {
  scanToken++;
  state = { file: null, scan: "idle", findings: [], reason: "" };
  $("file-input").value = "";
  $("dropzone").hidden = false;
  $("file-info").hidden = true;
  $("scan-card").hidden = true;
  $("send-card").hidden = true;
  $("send-error").hidden = true;
  hideTransfer();
  guard.reset();
  setStep(1);
}

// ---------------------------------------------------------------- STEP 1 → 2

async function selectFile(file) {
  reset();
  const ext = extOf(file.name);

  if (!CONFIG.ALLOWED_EXTS.includes(ext)) {
    toast(`.${ext} 형식은 업로드할 수 없습니다.`);
    return;
  }
  if (file.size > CONFIG.MAX_FILE_MB * MB) {
    toast(`파일이 ${CONFIG.MAX_FILE_MB}MB를 초과합니다.`);
    return;
  }

  state.file = file;
  $("dropzone").hidden = true;
  $("file-info").hidden = false;
  $("file-name").textContent = file.name;
  $("file-meta").textContent = fmtSize(file.size);
  $("scan-card").hidden = false;
  $("send-card").hidden = false;
  setStep(2);

  if (!CONFIG.SCANNABLE_EXTS.includes(ext)) {
    return markUnscannable("이 형식은 민감정보 검사를 지원하지 않습니다.");
  }
  if (file.size > CONFIG.SCAN_FILE_MAX_MB * MB) {
    // 업로드(20MB)는 되지만 미리 검사(5MB)는 안 되는 구간
    state.reason = `${CONFIG.SCAN_FILE_MAX_MB}MB를 넘는 파일은 미리 검사할 수 없어 전송할 때 검사합니다.`;
    setScan("deferred");
    guard.showDeferred(file.name, state.reason);
    return;
  }
  await runScan(file);
}

async function runScan(file) {
  const token = ++scanToken;
  setScan("scanning");
  try {
    const res = await api.scanFile(file);
    if (token !== scanToken) return;
    state.findings = res.findings || [];
    if (res.blocked) {
      setScan("blocked");
      setStep(3);
      guard.showFindings(file.name, state.findings);
    } else {
      setScan("clean");
      guard.showClean(file.name);
    }
  } catch (e) {
    if (token !== scanToken) return;
    const err = e instanceof ApiError ? e : new ApiError("INTERNAL_ERROR");
    if (err.code === "UNSUPPORTED_TYPE") {
      markUnscannable(err.userMessage);
    } else if (err.code === "FILE_TOO_LARGE") {
      state.reason = err.userMessage + " 전송할 때 검사합니다.";
      setScan("deferred");
      guard.showDeferred(file.name, state.reason);
    } else {
      // 검사 실패 시 전송을 막는다(fail-closed). 다시 검사만 허용.
      state.reason = err.userMessage;
      setScan("error");
    }
  }
}

function markUnscannable(reason) {
  state.reason = reason;
  setScan("unscannable");
  guard.showUnscannable(state.file.name, reason);
}

// ---------------------------------------------------------------- 렌더링

function setScan(scan) {
  state.scan = scan;
  const status = $("scan-status");
  const list = $("scan-findings");
  list.innerHTML = "";
  status.className = "status";

  switch (scan) {
    case "scanning":
      status.classList.add("is-loading");
      status.textContent = "민감정보를 검사하는 중…";
      break;
    case "clean":
      status.classList.add("is-ok");
      status.textContent = "민감정보가 발견되지 않았습니다.";
      break;
    case "blocked": {
      status.classList.add("is-danger");
      const total = state.findings.reduce((n, f) => n + f.count, 0);
      status.textContent = `${state.file.name}에서 민감정보 ${total}건이 발견되어 원본 전송을 보류했습니다.`;
      for (const f of state.findings) {
        const li = document.createElement("li");
        li.innerHTML = `<span></span><strong></strong>`;
        li.children[0].textContent = typeLabel(f.type);
        li.children[1].textContent = `${f.count}건`;
        list.appendChild(li);
      }
      break;
    }
    case "unscannable":
    case "deferred":
      status.classList.add("is-warn");
      status.textContent = state.reason;
      break;
    case "error": {
      status.classList.add("is-danger");
      status.textContent = `검사에 실패했습니다: ${state.reason} 검사 없이는 전송할 수 없습니다.`;
      const retry = document.createElement("button");
      retry.className = "btn btn-ghost btn-sm";
      retry.textContent = "다시 검사";
      retry.addEventListener("click", () => runScan(state.file));
      status.append(" ", retry);
      break;
    }
  }
  renderConfirm();
  updateSendButton();
}

// 확인이 필요한 경우: 검사 불가 형식, 또는 민감정보가 있는 원본을 그대로 보낼 때
function renderConfirm() {
  const row = $("confirm-row");
  const check = $("confirm-check");
  check.checked = false;
  if (state.scan === "unscannable") {
    $("confirm-text").textContent = "민감정보 검사 없이 전송된다는 것을 확인했습니다.";
    row.hidden = false;
  } else if (state.scan === "blocked") {
    $("confirm-text").textContent = "민감정보가 포함된 원본을 그대로 보내는 위험을 이해했습니다. (권장: SecureGuard에서 정제 후 전송)";
    row.hidden = false;
  } else {
    row.hidden = true;
  }
}

function updateSendButton() {
  const needsConfirm = state.scan === "unscannable" || state.scan === "blocked";
  const ok = state.scan === "clean" || state.scan === "deferred" || (needsConfirm && $("confirm-check").checked);
  $("btn-send").disabled = !ok;
}

function setStep(n) {
  document.querySelectorAll("#steps li").forEach((li) => {
    const s = Number(li.dataset.step);
    li.classList.toggle("is-done", s < n);
    li.classList.toggle("is-current", s === n);
  });
}

// ---------------------------------------------------------------- 전송

async function send() {
  const btn = $("btn-send");
  const errBox = $("send-error");
  errBox.hidden = true;
  btn.disabled = true;
  btn.textContent = "업로드 중…";
  try {
    const transfer = await api.createTransfer({
      file: state.file,
      expiresIn: Number($("expires").value),
      confirmSensitive: state.scan === "blocked",
      confirmUnscanned: state.scan === "unscannable",
    });
    setStep(5);
    $("send-card").hidden = true;
    showTransfer(transfer, { onNew: reset });
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError("INTERNAL_ERROR");
    btn.textContent = "QR 코드 만들기";
    // 서버가 전송 시점 검사에서 막은 경우 → 화면 상태를 서버 판정에 맞춘다
    if (err.code === "SENSITIVE_DATA" && err.body?.findings) {
      state.findings = err.body.findings;
      setScan("blocked");
      setStep(3);
      guard.showFindings(state.file.name, state.findings);
      return;
    }
    if (err.code === "UNSUPPORTED_TYPE" && err.body?.confirmation_required) {
      markUnscannable(err.userMessage);
      return;
    }
    errBox.textContent = err.userMessage;
    errBox.hidden = false;
    updateSendButton();
  } finally {
    btn.textContent = "QR 코드 만들기";
  }
}

// ---------------------------------------------------------------- SecureGuard 버튼

function handleGuardAction(action) {
  if (action === "CANCEL") {
    reset();
    toast("전송을 취소했습니다.");
    return;
  }
  // MASK / DELETE → Day 3에 /api/sanitize 흐름(STEP 4) 연결
  toast("정제 흐름은 Day 3에 연결됩니다.");
}
