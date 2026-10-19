// 업로드 화면 (PRD v1.3 §4 여정 1·2·6·7)
// STEP 1 파일 선택 → STEP 2 /api/scan/file 즉시 검사 → 전송: 해시 → /api/transfers → S3 직접 업로드 → /complete → QR
// 민감정보가 있으면 정제(여정 3·4): 조치 선택 → /api/sanitize → 미리보기 승인 → 처리본을 같은 sendBlob()으로 전송
import { api, CONFIG, ApiError, typeLabel, extOf, mimeOf, sha256Hex, base64ToBlob } from "./api.js";
import * as guard from "./assist.js";
import * as sanitize from "./sanitize.js";
import { saveToken, removeToken } from "./manage.js";
import { showTransfer, hideTransfer } from "./qr.js";
import { toast, fmtSize } from "./ui.js";

const $ = (id) => document.getElementById(id);
const MB = 1024 * 1024;

// scan: "idle" | "scanning" | "clean" | "blocked" | "unscannable" | "rejected" | "error"
//   unscannable: .txt/.csv 외 형식 → 사용자 확인 후 acknowledge_unsupported 로 전송
//   rejected:    4MB 초과 .txt/.csv → 검사할 수 없고, 서버는 검사 참조 없는 .txt/.csv를 받지 않으므로 전송 불가
// mode: "original" (원본 전송) | "select" (정제 방법 선택 중) | "preview" (처리본 미리보기, 승인 대기)
let state = { file: null, scan: "idle", scanResult: null, reason: "", mode: "original", sanitized: null };
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
  sanitize.initSanitize({ onResult: handleSanitized, onCancel: backToOriginal });
  reset();
}

function reset() {
  if (sending) return;
  scanToken++;
  state = { file: null, scan: "idle", scanResult: null, reason: "", mode: "original", sanitized: null };
  $("file-input").value = "";
  sanitize.hideSanitize();
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
    return markRejected(file.name);
  }
  await runScan(file);
}

async function runScan(file) {
  const token = ++scanToken;
  state.mode = "original";
  state.sanitized = null;
  sanitize.hideSanitize();
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
    if (err.code === "FILE_TOO_LARGE") {
      markRejected(file.name);
    } else if (err.code === "UNSUPPORTED_TYPE") {
      markUnscannable(err.userMessage);
    } else {
      // 검사 실패 시 전송을 막는다(fail-closed). 다시 검사만 허용.
      state.reason = err.userMessage;
      setScan("error");
    }
  }
}

function markRejected(filename) {
  state.reason = `${CONFIG.SCAN_FILE_MAX_MB}MB를 넘는 .txt·.csv 파일은 민감정보를 검사할 수 없어 보낼 수 없습니다. ${CONFIG.SCAN_FILE_MAX_MB}MB 이하로 나눠서 보내 주세요.`;
  setScan("rejected");
  $("send-card").hidden = true;
  guard.showRejected(filename, state.reason);
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
    case "rejected":
      status.classList.add("is-danger");
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

// 확인이 필요한 경우: 검사 불가(acknowledge_unsupported), 민감정보 원본 그대로 전송(acknowledge_risk),
// 처리본에 민감정보가 남은 채 전송(acknowledge_risk)
function renderConfirm() {
  const row = $("confirm-row");
  $("confirm-check").checked = false;
  $("btn-send").textContent = state.mode === "preview" ? "승인하고 QR 코드 만들기" : "QR 코드 만들기";
  if (state.mode === "select") {
    row.hidden = true;
  } else if (state.mode === "preview") {
    const res = state.sanitized.res;
    if (res.clean) {
      row.hidden = true;
    } else {
      const left = res.findings_after.map((f) => `${typeLabel(f.type)} ${f.count}건`).join(", ");
      $("confirm-text").textContent = `처리본에 남은 민감정보(${left})를 포함해 보내는 위험을 이해했습니다.`;
      row.hidden = false;
    }
  } else if (state.scan === "unscannable") {
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
  let ok;
  if (state.mode === "select") {
    ok = false; // 처리본을 먼저 만들어야 한다
  } else if (state.mode === "preview") {
    ok = state.sanitized.res.clean || $("confirm-check").checked;
  } else {
    const needsConfirm = state.scan === "unscannable" || state.scan === "blocked";
    ok = state.scan === "clean" || (needsConfirm && $("confirm-check").checked);
  }
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
  if (state.mode === "preview") return sendSanitized();
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

// 승인된 처리본 전송: 서버가 준 content_b64 바이트를 그대로 올린다 (해시가 sanitize 세션과 같아야 함)
function sendSanitized() {
  const { res } = state.sanitized;
  const type = mimeOf(state.file);
  return sendBlob({
    blob: base64ToBlob(res.content_b64, type),
    name: sanitizedName(state.file.name),
    type,
    ref: { sanitize_id: res.sanitize_id },
    expectedSha256: res.sha256,
    userApproved: true,
    acknowledgeRisk: !res.clean,
  });
}

// 고객정보.csv → 고객정보_정제본.csv (원본과 구분, 확장자 유지)
function sanitizedName(name) {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? `${name.slice(0, dot)}_정제본${name.slice(dot)}` : `${name}_정제본`;
}

// ---------------------------------------------------------------- 정제 흐름 (여정 3·4)

function enterSelect(preset) {
  if (state.scan !== "blocked" || sending) return;
  state.mode = "select";
  state.sanitized = null;
  setStep(3);
  sanitize.openSelect(state.file, state.scanResult.findings, preset);
  guard.showSelecting(preset);
  renderConfirm();
  updateSendButton();
}

function handleSanitized(res, actions) {
  state.mode = "preview";
  state.sanitized = { res, actions };
  setStep(4);
  guard.showSanitized(actions, res);
  $("send-error").hidden = true;
  renderConfirm();
  updateSendButton();
}

function backToOriginal() {
  state.mode = "original";
  state.sanitized = null;
  sanitize.hideSanitize();
  setStep(3);
  guard.showFindings(state.file.name, state.scanResult.findings);
  renderConfirm();
  updateSendButton();
}

// 원본·처리본 공통 전송. ref: { scan_id } | { sanitize_id } | {}
// userApproved: 처리본(sanitize_id) 미리보기 승인 — 서버가 필수로 요구
export async function sendBlob({ blob, name, type, ref, expectedSha256, acknowledgeRisk = false, acknowledgeUnsupported = false, userApproved = false }) {
  const errBox = $("send-error");
  errBox.hidden = true;
  sending = true;
  updateSendButton();
  $("btn-reset").disabled = true;
  let stage = "hash";   // hash → create → upload → complete (오류 해석에 사용)
  let created = null;
  try {
    setProgress("파일 확인 중…", 0);
    const sha256 = await sha256Hex(blob);
    if (expectedSha256 && sha256 !== expectedSha256) {
      throw new ApiError("VALIDATION_ERROR", "검사 후 파일이 바뀌었습니다. 파일을 다시 선택하세요.");
    }

    stage = "create";
    setProgress("업로드 준비 중…", 0);
    created = await api.createTransfer({
      files: [{ name, size: blob.size, type, sha256 }],
      ...ref,
      expires_in: Number($("expires").value),
      encrypted: false,
      acknowledge_risk: acknowledgeRisk,
      acknowledge_unsupported: acknowledgeUnsupported,
      user_approved: userApproved,
    });
    // 관리 토큰은 이 응답에만 온다 — 바로 보관
    saveToken(created.id, created.manage_token);

    stage = "upload";
    await api.uploadToS3(created.uploads[0], blob, (r) => setProgress(`업로드 중… ${Math.round(r * 100)}%`, r));

    stage = "complete";
    setProgress("업로드 확인 중…", 1);
    let done;
    try {
      done = await api.completeTransfer(created.id, created.manage_token);
    } catch (e) {
      // /complete는 재시도해도 안전하다 (이미 ACTIVE면 같은 링크를 돌려준다)
      if (!(e instanceof ApiError) || e.code !== "NETWORK_ERROR") throw e;
      done = await api.completeTransfer(created.id, created.manage_token);
    }
    saveToken(created.id, created.manage_token, done.expires_at);

    setProgress(null);
    setStep(5);
    $("send-card").hidden = true;
    sanitize.hideSanitize();
    guard.showSent(name);
    showTransfer(done, { onNew: reset });
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError("INTERNAL_ERROR");
    setProgress(null);
    errBox.textContent = err.userMessage;
    errBox.hidden = false;
    // 업로드 단계 이후 실패한 전송은 쓸 수 없다 (서버가 폐기했거나 업로드 권한이 끝남) → 토큰 정리
    if (created && stage !== "create") removeToken(created.id);
    if (err.code === "EXPIRED" && stage === "create" && ref.sanitize_id) {
      // 처리본 세션(10분)이 만료됨 → 같은 선택으로 다시 정제하게 한다
      errBox.textContent = "처리본 유효 시간(10분)이 지났습니다. 처리본을 다시 만들어 주세요.";
      state.mode = "select";
      state.sanitized = null;
      setStep(3);
      sanitize.reopenWithRemaining([]);
      renderConfirm();
    } else if (err.code === "EXPIRED" && stage === "create" && state.file && state.scanResult) {
      // 검사 세션(10분)이 만료됨 → 다시 검사하고 사용자가 다시 누르게 한다
      errBox.textContent = "검사 결과가 만료되어 다시 검사했습니다. 다시 전송하세요.";
      runScan(state.file);
    } else if (err.code === "EXPIRED" && stage === "complete") {
      errBox.textContent = "업로드 유효 시간(5분)이 지났습니다. 다시 전송하세요.";
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
  } else if (action === "MASK" || action === "DELETE") {
    enterSelect(action);
  } else if (action === "MORE" && state.sanitized) {
    // 남은 유형까지 정제: 원본 + 늘어난 조치로 다시 만든다
    state.mode = "select";
    setStep(3);
    sanitize.reopenWithRemaining(state.sanitized.res.findings_after);
    state.sanitized = null;
    renderConfirm();
    updateSendButton();
  }
}
