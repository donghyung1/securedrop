// 업로드 화면 (PRD v1.3 §4 여정 1·2·6·7)
// STEP 1 파일 선택 → STEP 2 /api/scan/file 즉시 검사 → 전송: 해시 → /api/transfers → S3 직접 업로드 → /complete → QR
// 정제(여정 3·4)는 Day 3에 assist.js + api.sanitize로 연결한다. sendBlob()은 처리본 전송에도 그대로 쓴다.
import { api, CONFIG, ApiError, typeLabel, extOf, mimeOf, sha256Hex } from "./api.js";
import * as guard from "./assist.js";
import { saveToken } from "./manage.js";
import { showTransfer, hideTransfer } from "./qr.js";
import { toast, fmtSize } from "./ui.js";

const $ = (id) => document.getElementById(id);
const MB = 1024 * 1024;

// scan: "idle" | "scanning" | "clean" | "blocked" | "unscannable" | "error"
//   unscannable: .txt/.csv 외 형식이거나 4MB 초과 → acknowledge_unsupported 로 전송
//   (v1.3에서는 파일이 Lambda를 거치지 않고 S3로 가므로 4MB 초과 파일은 서버도 검사할 수 없다)
let state = { file: null, scan: "idle", scanResult: null, reason: "" };
let scanToken = 0; // 검사 중 다른 파일을 고르면 이전 응답은 버린다
let sending = false;

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
  $("btn-send").addEventListener("click", sendSelected);

  const sel = $("expires");
  for (const o of CONFIG.EXPIRES_OPTIONS) {
    sel.add(new Option(o.label, o.sec, false, o.sec === CONFIG.DEFAULT_EXPIRES_SEC));
  }

  guard.onAction(handleGuardAction);
  reset();
}

function reset() {
  if (sending) return;
  scanToken++;
  state = { file: null, scan: "idle", scanResult: null, reason: "" };
  $("file-input").value = "";
  $("dropzone").hidden = false;
  $("file-info").hidden = true;
  $("scan-card").hidden = true;
  $("send-card").hidden = true;
  $("send-error").hidden = true;
  setProgress(null);
  hideTransfer();
  guard.reset();
  setStep(1);
}

// ---------------------------------------------------------------- STEP 1 → 2

async function selectFile(file) {
  if (sending) return;
  reset();
  const ext = extOf(file.name);

  if (!CONFIG.ALLOWED_EXTS.includes(ext)) {
    toast(`.${ext} 형식은 업로드할 수 없습니다.`);
    return;
  }
  if (file.size === 0) {
    toast("빈 파일은 보낼 수 없습니다.");
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
    return markUnscannable(`${CONFIG.SCAN_FILE_MAX_MB}MB를 넘는 파일은 민감정보 검사를 할 수 없습니다.`);
  }
  await runScan(file);
}

async function runScan(file) {
  const token = ++scanToken;
  setScan("scanning");
  try {
    const res = await api.scanFile(file);
    if (token !== scanToken) return;
    state.scanResult = res;
    if (res.blocked) {
      setScan("blocked");
      setStep(3);
      guard.showFindings(file.name, res.findings);
    } else {
      setScan("clean");
      guard.showClean(file.name);
    }
  } catch (e) {
    if (token !== scanToken) return;
    const err = e instanceof ApiError ? e : new ApiError("INTERNAL_ERROR");
    if (err.code === "UNSUPPORTED_TYPE" || err.code === "FILE_TOO_LARGE") {
      markUnscannable(err.userMessage);
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
      const findings = state.scanResult.findings;
      status.classList.add("is-danger");
      const total = findings.reduce((n, f) => n + f.count, 0);
      status.textContent = `${state.file.name}에서 민감정보 ${total}건이 발견되어 원본 전송을 보류했습니다.`;
      for (const f of findings) {
        const li = document.createElement("li");
        li.innerHTML = `<span></span><strong></strong>`;
        li.children[0].textContent = typeLabel(f.type);
        li.children[1].textContent = `${f.count}건`;
        list.appendChild(li);
      }
      break;
    }
    case "unscannable":
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

// 확인이 필요한 경우: 검사 불가(acknowledge_unsupported), 민감정보 원본 그대로 전송(acknowledge_risk)
function renderConfirm() {
  const row = $("confirm-row");
  $("confirm-check").checked = false;
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
  const ok = state.scan === "clean" || (needsConfirm && $("confirm-check").checked);
  $("btn-send").disabled = sending || !ok;
}

function setStep(n) {
  document.querySelectorAll("#steps li").forEach((li) => {
    const s = Number(li.dataset.step);
    li.classList.toggle("is-done", s < n);
    li.classList.toggle("is-current", s === n);
  });
}

// label: 단계 문구, ratio: 0~1 (null이면 숨김)
function setProgress(label, ratio = 0) {
  const box = $("send-progress");
  if (label === null) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  $("progress-label").textContent = label;
  $("progress-bar").style.width = `${Math.round(ratio * 100)}%`;
}

// ---------------------------------------------------------------- 전송

function sendSelected() {
  const ref = state.scanResult ? { scan_id: state.scanResult.scan_id } : {};
  return sendBlob({
    blob: state.file,
    name: state.file.name,
    type: mimeOf(state.file),
    ref,
    expectedSha256: state.scanResult?.sha256,
    acknowledgeRisk: state.scan === "blocked",
    acknowledgeUnsupported: state.scan === "unscannable",
  });
}

// 원본·처리본 공통 전송. ref: { scan_id } | { sanitize_id } | {}
export async function sendBlob({ blob, name, type, ref, expectedSha256, acknowledgeRisk = false, acknowledgeUnsupported = false }) {
  const errBox = $("send-error");
  errBox.hidden = true;
  sending = true;
  updateSendButton();
  $("btn-reset").disabled = true;
  try {
    setProgress("파일 확인 중…", 0);
    const sha256 = await sha256Hex(blob);
    if (expectedSha256 && sha256 !== expectedSha256) {
      throw new ApiError("VALIDATION_ERROR", "검사 후 파일이 바뀌었습니다. 파일을 다시 선택하세요.");
    }

    setProgress("업로드 준비 중…", 0);
    const created = await api.createTransfer({
      files: [{ name, size: blob.size, type, sha256 }],
      ...ref,
      expires_in: Number($("expires").value),
      encrypted: false,
      acknowledge_risk: acknowledgeRisk,
      acknowledge_unsupported: acknowledgeUnsupported,
    });
    // 관리 토큰은 이 응답에만 온다 — 바로 보관
    saveToken(created.id, created.manage_token);

    await api.uploadToS3(created.uploads[0], blob, (r) => setProgress(`업로드 중… ${Math.round(r * 100)}%`, r));

    setProgress("업로드 확인 중…", 1);
    const done = await api.completeTransfer(created.id, created.manage_token);
    saveToken(created.id, created.manage_token, done.expires_at);

    setProgress(null);
    setStep(5);
    $("send-card").hidden = true;
    guard.showSent(name);
    showTransfer(done, { onNew: reset });
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError("INTERNAL_ERROR");
    setProgress(null);
    errBox.textContent = err.userMessage;
    errBox.hidden = false;
    if (err.code === "EXPIRED" && state.file && state.scanResult) {
      // 검사 세션(10분)이 만료됨 → 다시 검사하고 사용자가 다시 누르게 한다
      errBox.textContent = "검사 결과가 만료되어 다시 검사했습니다. 다시 전송하세요.";
      runScan(state.file);
    }
  } finally {
    sending = false;
    $("btn-reset").disabled = false;
    updateSendButton();
  }
}

// ---------------------------------------------------------------- SecureGuard 버튼

function handleGuardAction(action) {
  if (action === "CANCEL") {
    reset();
    toast("전송을 취소했습니다.");
    return;
  }
  // MASK / DELETE → Day 3에 /api/sanitize → 미리보기 승인 → sendBlob({ ref: { sanitize_id } }) 연결
  toast("정제 흐름은 Day 3에 연결됩니다.");
}
