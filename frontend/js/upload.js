// 업로드 화면 (PRD v1.3 §4 여정 1·2·6·7, F10 다중 파일)
// STEP 1 파일 선택(최대 5개·합계 20MB) → STEP 2 파일마다 /api/scan/file 즉시 검사
// 전송: 해시 → /api/transfers(파일별 scan_id/sanitize_id) → 파일마다 S3 직접 업로드 → /complete 한 번 → QR
// 민감정보가 있으면 정제(여정 3·4): 목록에서 파일 선택 → 조치 선택 → /api/sanitize → 미리보기 승인 → 그 파일이 처리본으로 바뀜
import { api, CONFIG, ApiError, typeLabel, sha256Hex, base64ToBlob } from "./api.js";
import * as guard from "./assist.js";
import * as sanitize from "./sanitize.js";
import { saveToken, removeToken } from "./manage.js";
import { showTransfer, hideTransfer } from "./qr.js";
import { generateKey, encryptBlob, withKey, OVERHEAD } from "./crypto.js";
import { qrDataUrl } from "./qrcode.js";
import { toast, fmtSize } from "./ui.js";
import { extOf, mimeOf, checkAddition, blocksE2ee, planTransfer, buildCreateBody, uploadAll } from "./transfer.js";

const $ = (id) => document.getElementById(id);
const MB = 1024 * 1024;
const DROP_TITLE = "휴대폰으로 보낼 파일을 선택하세요";
const DROP_HINT = `끌어다 놓거나 클릭 · 최대 ${CONFIG.MAX_FILES}개, 합계 ${CONFIG.MAX_FILE_MB}MB · ${CONFIG.SCAN_FILE_MAX_MB}MB 이하 .txt / .csv 는 민감정보를 자동 검사합니다`;

// 파일 하나(entry): { id, file, scan, scanResult, reason, processed, scanToken }
//   scan: "scanning" | "clean" | "blocked" | "unscannable" | "rejected" | "error"
//     unscannable: .txt/.csv 외 형식 → 사용자 확인 후 acknowledge_unsupported 로 전송
//     rejected:    4MB 초과 .txt/.csv → 검사할 수 없고, 서버는 검사 참조 없는 .txt/.csv를 받지 않으므로 전송 불가 (목록에서 빼야 함)
//   processed: 승인된 처리본 { res(/api/sanitize 응답), blob, name, type } — 있으면 원본 대신 이것을 보낸다
// mode: "original" (목록 편집·전송) | "select" (focus 파일의 정제 방법 선택 중) | "preview" (처리본 미리보기, 승인 대기)
// focus: SecureGuard·검사 카드가 설명하는 파일 (정제 대상도 이 파일)
const newState = () => ({ files: [], focusId: null, mode: "original", sanitized: null, confirmText: null, sent: false });
let state = newState();
let nextId = 0;
let sending = false;

export function initUpload() {
  const input = $("file-input");
  const drop = $("dropzone");

  input.addEventListener("change", () => {
    addFiles([...input.files]);
    input.value = ""; // 같은 파일을 지웠다가 다시 고를 수 있게
  });
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("is-over"); }));
  drop.addEventListener("drop", (e) => addFiles([...e.dataTransfer.files]));
  drop.addEventListener("click", (e) => {
    const why = editBlock();
    if (why) { e.preventDefault(); toast(why); }
  });

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
  state = newState();
  $("file-input").value = "";
  sanitize.hideSanitize();
  $("send-error").hidden = true;
  $("confirm-check").checked = false;
  setProgress(null);
  hideTransfer();
  guard.reset();
  render();
}

const focused = () => state.files.find((e) => e.id === state.focusId) || null;
const sizeOf = (e) => (e.processed ? e.processed.blob.size : e.file.size);
const nameOf = (e) => (e.processed ? e.processed.name : e.file.name);
const unresolved = (e) => e.scan === "blocked" && !e.processed; // 아직 정제하지 않은 민감정보 파일

// 파일 목록을 바꿀 수 없는 이유 (바꿀 수 있으면 null)
function editBlock() {
  if (sending) return "전송 중에는 파일을 바꿀 수 없습니다.";
  if (state.sent) return "전송을 마쳤습니다. '새 파일 보내기'로 다시 시작하세요.";
  if (state.mode !== "original") return "정제 중에는 파일을 바꿀 수 없습니다. 먼저 정제를 마치거나 원본으로 돌아가세요.";
  return null;
}

// ---------------------------------------------------------------- STEP 1 → 2

function addFiles(list) {
  if (!list.length) return;
  const why = editBlock();
  if (why) return toast(why);

  const accepted = [];
  const errors = [];
  for (const file of list) {
    const err = checkAddition([...state.files.map((e) => e.file), ...accepted], file, CONFIG);
    if (err) errors.push(err);
    else accepted.push(file);
  }
  if (errors.length) toast(errors.length === 1 ? errors[0] : `${errors[0]} 외 ${errors.length - 1}개 파일을 제외했습니다.`);
  if (!accepted.length) return;

  $("send-error").hidden = true;
  const entries = accepted.map((file) => ({
    id: ++nextId, file, scan: "scanning", scanResult: null, reason: "", processed: null, scanToken: 0,
  }));
  state.files.push(...entries);
  render();
  entries.forEach(startScan);
}

function startScan(entry) {
  if (!CONFIG.SCANNABLE_EXTS.includes(extOf(entry.file.name))) {
    return markUnscannable(entry, "이 형식은 민감정보 검사를 지원하지 않습니다.");
  }
  if (entry.file.size > CONFIG.SCAN_FILE_MAX_MB * MB) return markRejected(entry);
  return runScan(entry);
}

async function runScan(entry) {
  const token = ++entry.scanToken;
  entry.scan = "scanning";
  entry.scanResult = null;
  entry.processed = null;
  render();
  try {
    const res = await api.scanFile(entry.file);
    if (stale(entry, token)) return;
    entry.scanResult = res;
    entry.scan = res.blocked ? "blocked" : "clean";
    afterScan(entry);
  } catch (e) {
    if (stale(entry, token)) return;
    const err = e instanceof ApiError ? e : new ApiError("INTERNAL_ERROR");
    if (err.code === "FILE_TOO_LARGE") {
      markRejected(entry);
    } else if (err.code === "UNSUPPORTED_TYPE") {
      markUnscannable(entry, err.userMessage);
    } else {
      // 검사 실패 시 전송을 막는다(fail-closed). 다시 검사하거나 목록에서 빼야 한다.
      entry.reason = err.userMessage;
      entry.scan = "error";
      afterScan(entry);
    }
  }
}

// 검사 중 파일이 목록에서 빠졌거나 다시 검사가 시작됐으면 이전 응답은 버린다
const stale = (entry, token) => !state.files.includes(entry) || entry.scanToken !== token;

function markRejected(entry) {
  entry.reason = `${CONFIG.SCAN_FILE_MAX_MB}MB를 넘는 .txt·.csv 파일은 민감정보를 검사할 수 없어 보낼 수 없습니다. ${CONFIG.SCAN_FILE_MAX_MB}MB 이하로 나눠서 보내 주세요.`;
  entry.scan = "rejected";
  afterScan(entry);
}

function markUnscannable(entry, reason) {
  entry.reason = reason;
  entry.scan = "unscannable";
  afterScan(entry);
}

// 주의가 더 필요한 파일(민감정보 > 거부·실패 > 검사 미지원)을 SecureGuard가 설명하게 한다
const attention = (e) => (unresolved(e) ? 3 : e.scan === "rejected" || e.scan === "error" ? 2 : e.scan === "unscannable" ? 1 : 0);

function afterScan(entry) {
  const cur = focused();
  if (state.mode === "original" && (!cur || attention(entry) > attention(cur))) setFocus(entry);
  else render();
}

function setFocus(entry) {
  state.focusId = entry ? entry.id : null;
  if (entry) {
    if (entry.scan === "blocked") guard.showFindings(entry.file.name, entry.scanResult.findings);
    else if (entry.scan === "rejected") guard.showRejected(entry.file.name, entry.reason);
    else if (entry.scan === "unscannable") guard.showUnscannable(entry.file.name, entry.reason);
    else if (entry.scan === "clean") guard.showClean(entry.file.name);
  } else {
    guard.reset();
  }
  render();
}

// 목록에서 뺀다. 정제 중이던 파일이면 정제도 함께 끝낸다.
function removeEntry(entry) {
  entry.scanToken++;
  state.files = state.files.filter((e) => e !== entry);
  if (state.mode !== "original" && state.focusId === entry.id) {
    state.mode = "original";
    state.sanitized = null;
    sanitize.hideSanitize();
  }
  $("send-error").hidden = true;
  if (!state.files.length) return reset();
  if (state.focusId === entry.id) setFocus(state.files.find((e) => attention(e) > 0) || null);
  else render();
}

// ---------------------------------------------------------------- 렌더링

function render() {
  const files = state.files;
  const has = files.length > 0;
  $("file-info").hidden = !has;
  $("scan-card").hidden = !has;
  $("send-card").hidden = !has || state.sent;
  $("dropzone").hidden = state.sent;
  $("dropzone").classList.toggle("is-compact", has);
  $("dropzone-title").textContent = has ? `파일 추가 (${files.length}/${CONFIG.MAX_FILES})` : DROP_TITLE;
  $("dropzone-hint").textContent = has ? `끌어다 놓거나 클릭 · 합계 ${CONFIG.MAX_FILE_MB}MB까지` : DROP_HINT;
  $("file-summary").textContent = `${files.length}개 · ${fmtSize(files.reduce((n, e) => n + sizeOf(e), 0))} / ${CONFIG.MAX_FILE_MB}MB`;
  $("btn-reset").hidden = state.sent;

  // 검사 대상 형식(.txt/.csv)은 서버가 평문 해시로 검사 결과를 검증하므로, 하나라도 있으면 전송 전체에서 종단간 암호화를 쓸 수 없다.
  const noE2ee = blocksE2ee(files.map((e) => e.file), CONFIG);
  if (noE2ee) $("encrypt-check").checked = false;
  $("encrypt-check").disabled = noE2ee;
  $("encrypt-note").hidden = !noE2ee;

  renderList();
  renderScan();
  renderConfirm();
  updateSendButton();
  setStep(currentStep());
}

function currentStep() {
  if (state.sent) return 5;
  if (!state.files.length) return 1;
  if (state.mode === "preview") return 4;
  if (state.mode === "select" || state.files.some(unresolved)) return 3;
  return 2;
}

function setStep(n) {
  document.querySelectorAll("#steps li").forEach((li) => {
    const s = Number(li.dataset.step);
    li.classList.toggle("is-done", s < n);
    li.classList.toggle("is-current", s === n);
  });
}

const leftover = (res) => res.findings_after.reduce((n, f) => n + f.count, 0);

function badgeOf(e) {
  if (e.scan === "scanning") return ["검사 중", ""];
  if (e.processed) {
    return e.processed.res.clean ? ["정제 완료", "is-ok"] : [`정제본 · 민감정보 ${leftover(e.processed.res)}건 남음`, "is-warn"];
  }
  return {
    clean: ["안전", "is-ok"],
    blocked: ["민감정보 발견", "is-danger"],
    unscannable: ["검사 미지원", "is-warn"],
    rejected: ["거부", "is-danger"],
    error: ["검사 실패", "is-danger"],
  }[e.scan];
}

function renderList() {
  const list = $("file-list");
  list.innerHTML = "";
  const editable = !editBlock();
  for (const e of state.files) {
    const li = document.createElement("li");
    li.className = "file-item" + (e.id === state.focusId ? " is-focus" : "");

    const main = document.createElement("div");
    main.className = "file-item-main";
    const name = document.createElement("div");
    name.className = "file-name";
    name.textContent = nameOf(e);
    const meta = document.createElement("div");
    meta.className = "muted";
    meta.textContent = fmtSize(sizeOf(e));
    main.append(name, meta);

    const [text, tone] = badgeOf(e);
    const badge = document.createElement("span");
    badge.className = "file-badge" + (tone ? ` ${tone}` : "");
    badge.textContent = text;
    li.append(main, badge);

    if (!state.sent) {
      const actions = document.createElement("div");
      actions.className = "file-item-actions";
      const add = (label, onClick) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn btn-ghost btn-sm";
        b.textContent = label;
        b.disabled = !editable;
        b.setAttribute("aria-label", `${e.file.name} ${label}`);
        b.addEventListener("click", onClick);
        actions.appendChild(b);
      };
      if (e.scan === "blocked") add(e.processed ? "다시 정제" : "정제", () => startSanitize(e));
      if (e.processed) add("원본 사용", () => useOriginal(e));
      if (e.scan === "error") add("다시 검사", () => runScan(e));
      add("빼기", () => removeEntry(e));
      li.appendChild(actions);
    }
    list.appendChild(li);
  }
}

const sensitiveTotal = (findings) => findings.reduce((n, f) => n + f.count, 0);

// 검사 카드: 주의가 필요한 focus 파일이 있으면 그 상세, 없으면 전체 요약
function renderScan() {
  const status = $("scan-status");
  const list = $("scan-findings");
  list.innerHTML = "";
  status.className = "status";
  const files = state.files;
  if (!files.length) return;
  const f = focused();

  if (f && f.scan === "blocked") {
    status.classList.add("is-danger");
    status.textContent = `${f.file.name}에서 민감정보 ${sensitiveTotal(f.scanResult.findings)}건이 발견되어 원본 전송을 보류했습니다.`;
    for (const item of f.scanResult.findings) {
      const li = document.createElement("li");
      li.innerHTML = `<span></span><strong></strong>`;
      li.children[0].textContent = typeLabel(item.type);
      li.children[1].textContent = `${item.count}건`;
      list.appendChild(li);
    }
  } else if (f && (f.scan === "unscannable" || f.scan === "rejected")) {
    status.classList.add(f.scan === "rejected" ? "is-danger" : "is-warn");
    status.textContent = `${f.file.name}: ${f.reason}`;
  } else if (f && f.scan === "error") {
    status.classList.add("is-danger");
    status.textContent = `${f.file.name} 검사에 실패했습니다: ${f.reason} 검사 없이는 전송할 수 없습니다.`;
    const retry = document.createElement("button");
    retry.className = "btn btn-ghost btn-sm";
    retry.textContent = "다시 검사";
    retry.addEventListener("click", () => runScan(f));
    status.append(" ", retry);
  } else if (files.some((e) => e.scan === "scanning")) {
    status.classList.add("is-loading");
    status.textContent = "민감정보를 검사하는 중…";
  } else if (files.some(unresolved)) {
    status.classList.add("is-danger");
    status.textContent = `민감정보가 발견된 파일 ${files.filter(unresolved).length}개가 있습니다. 정제하거나 위험을 확인해야 전송할 수 있습니다.`;
  } else if (files.some((e) => e.scan === "unscannable")) {
    status.classList.add("is-warn");
    status.textContent = "검사를 지원하지 않는 파일이 포함되어 있습니다.";
  } else if (files.some((e) => e.processed)) {
    status.classList.add("is-ok");
    status.textContent = "정제를 마쳐 모든 파일이 전송 준비되었습니다.";
  } else {
    status.classList.add("is-ok");
    status.textContent = files.length === 1 ? "민감정보가 발견되지 않았습니다." : "모든 파일에서 민감정보가 발견되지 않았습니다.";
  }
}

// 확인이 필요한 경우 (전송 전체에 하나의 체크박스): 검사 불가 파일(acknowledge_unsupported),
// 민감정보 원본을 그대로 전송(acknowledge_risk), 처리본에 민감정보가 남은 채 승인(acknowledge_risk, 승인 단계)
function confirmText() {
  if (state.mode === "select") return null;
  if (state.mode === "preview") {
    const res = state.sanitized.res;
    if (res.clean) return null;
    const left = res.findings_after.map((f) => `${typeLabel(f.type)} ${f.count}건`).join(", ");
    return `처리본에 남은 민감정보(${left})를 포함해 보내는 위험을 이해했습니다.`;
  }
  const single = state.files.length === 1;
  const unsupported = state.files.filter((e) => e.scan === "unscannable").length;
  const risky = state.files.filter(unresolved).length;
  const parts = [];
  if (unsupported) {
    parts.push(single ? "민감정보 검사 없이 전송된다는 것을 확인했습니다."
      : `검사를 지원하지 않는 파일 ${unsupported}개가 민감정보 검사 없이 전송된다는 것을 확인했습니다.`);
  }
  if (risky) {
    parts.push(single ? "민감정보가 포함된 원본을 그대로 보내는 위험을 이해했습니다. (권장: SecureGuard에서 정제 후 전송)"
      : `민감정보가 포함된 원본 ${risky}개를 그대로 보내는 위험을 이해했습니다. (권장: 정제 후 전송)`);
  }
  return parts.length ? parts.join(" ") : null;
}

function renderConfirm() {
  const text = confirmText();
  if (text !== state.confirmText) { // 확인할 내용이 바뀌면 이전 체크는 무효
    state.confirmText = text;
    $("confirm-check").checked = false;
  }
  $("confirm-row").hidden = text === null;
  if (text !== null) $("confirm-text").textContent = text;
  $("btn-send").textContent = state.mode === "preview" ? "처리본 승인" : "QR 코드 만들기";
}

// 전송(또는 처리본 승인)을 막는 이유. 막을 이유가 없으면 null
function blockReason() {
  const files = state.files;
  if (!files.length) return "파일을 선택하세요.";
  if (state.mode === "select") return "처리본을 먼저 만들어 주세요.";
  const confirmed = state.confirmText === null || $("confirm-check").checked;
  if (state.mode === "preview") return confirmed ? null : "남은 민감정보에 대한 확인 항목에 체크하세요.";
  if (files.some((e) => e.scan === "scanning")) return "검사가 끝날 때까지 기다려 주세요.";
  if (files.some((e) => e.scan === "error")) return "검사에 실패한 파일이 있습니다. 다시 검사하거나 목록에서 빼 주세요.";
  if (files.some((e) => e.scan === "rejected")) return `${CONFIG.SCAN_FILE_MAX_MB}MB를 넘는 .txt·.csv 파일이 있습니다. 목록에서 빼 주세요.`;
  if (files.reduce((n, e) => n + sizeOf(e), 0) > CONFIG.MAX_FILE_MB * MB) return `파일 합계가 ${CONFIG.MAX_FILE_MB}MB를 초과합니다. 파일을 빼 주세요.`;
  return confirmed ? null : "확인 항목에 체크하세요.";
}

function updateSendButton() {
  const reason = blockReason();
  $("btn-send").disabled = sending || reason !== null;
  $("send-hint").textContent = reason || "";
  $("send-hint").hidden = sending || !reason;
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
  if (state.mode === "preview") return approveSanitized();
  return sendTransfer();
}

// 미리보기 승인: 서버가 준 content_b64 바이트를 그대로 처리본으로 삼는다 (해시가 sanitize 세션과 같아야 함).
// 이 파일은 전송 목록에서 처리본으로 바뀌고, 실제 전송은 모든 파일이 준비된 뒤 "QR 코드 만들기"로 한다.
function approveSanitized() {
  const entry = focused();
  const { res } = state.sanitized;
  const type = mimeOf(entry.file);
  entry.processed = { res, blob: base64ToBlob(res.content_b64, type), name: sanitizedName(entry.file.name), type };
  state.mode = "original";
  state.sanitized = null;
  sanitize.hideSanitize();
  $("send-error").hidden = true;
  toast(`${entry.file.name} 처리본을 반영했습니다.`);
  const next = state.files.find(unresolved);
  if (next) {
    setFocus(next);
  } else {
    state.focusId = null;
    guard.showApproved(entry.file.name, res);
    render();
  }
}

// 고객정보.csv → 고객정보_정제본.csv (원본과 구분, 확장자 유지)
function sanitizedName(name) {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? `${name.slice(0, dot)}_정제본${name.slice(dot)}` : `${name}_정제본`;
}

// ---------------------------------------------------------------- 정제 흐름 (여정 3·4)

// 목록의 "정제" 버튼: 이 파일을 focus로 삼고 정제 방법 선택을 연다
function startSanitize(entry) {
  if (editBlock()) return;
  state.focusId = entry.id;
  guard.showFindings(entry.file.name, entry.scanResult.findings);
  enterSelect("MASK");
}

// 처리본을 버리고 원본을 쓴다 (민감정보 위험 확인이 다시 필요해진다)
function useOriginal(entry) {
  if (editBlock()) return;
  entry.processed = null;
  setFocus(entry);
}

// preset: "MASK" | "DELETE" (전체) 또는 { TYPE: "MASK" | "DELETE" | "SKIP" }
function enterSelect(preset, { quiet = false } = {}) {
  const entry = focused();
  if (!entry || entry.scan !== "blocked" || sending) return;
  state.mode = "select";
  state.sanitized = null;
  sanitize.openSelect(entry.file, entry.scanResult.findings, preset);
  if (!quiet) guard.showSelecting(preset);
  render();
}

// 자연어 해석 결과 { action, targets } → 선택 화면에 채운다 (처리는 사용자가 "처리본 만들기"로 확정)
// 처음이면 언급한 유형만 적용하고 나머지는 그대로 두기, 이미 고르던 중이면 기존 선택에 더한다.
function applyAssist({ action, targets }) {
  const entry = focused();
  if (!entry || entry.scan !== "blocked") return;
  const base = state.mode === "original"
    ? Object.fromEntries(entry.scanResult.findings.map((f) => [f.type, "SKIP"]))
    : sanitize.getChoices();
  for (const t of targets) base[t] = action;
  enterSelect(base, { quiet: true });
}

function handleSanitized(res, actions) {
  state.mode = "preview";
  state.sanitized = { res, actions };
  guard.showSanitized(actions, res);
  $("send-error").hidden = true;
  render();
}

function backToOriginal() {
  const entry = focused();
  state.mode = "original";
  state.sanitized = null;
  sanitize.hideSanitize();
  guard.showFindings(entry.file.name, entry.scanResult.findings);
  render();
}

// 검사·정제 결과(서버 세션 10분)가 만료됨 → 모두 다시 검사한다. 정제한 파일은 다시 정제해야 한다.
function rescanAll() {
  state.focusId = null;
  guard.reset();
  state.files.forEach(startScan);
}

// 선택한 모든 파일을 한 번에 전송. 파일마다 자기 scan_id/sanitize_id를 보내고, 확인 플래그는 전송 전체에 건다.
async function sendTransfer() {
  const errBox = $("send-error");
  errBox.hidden = true;
  sending = true;
  render();
  $("btn-reset").disabled = true;
  let stage = "hash";   // hash → create → upload → complete (오류 해석에 사용)
  let created = null;
  try {
    const entries = state.files.slice();
    const { items, flags } = planTransfer(entries);

    setProgress("파일 확인 중…", 0);
    const hashes = [];
    for (const it of items) {
      const sha = await sha256Hex(it.blob);
      if (it.expectedSha256 && sha !== it.expectedSha256) {
        throw new ApiError("VALIDATION_ERROR", `검사 후 ${it.name}이(가) 바뀌었습니다. 파일을 다시 선택하세요.`);
      }
      hashes.push(sha);
    }

    // 암호화: 검사·정제·승인은 평문으로 끝났다. 업로드할 바이트만 암호문으로 바꾼다. 키는 전송 하나에 하나.
    // 서버에는 파일마다 암호문의 크기·해시를 알리고(encrypted:true), 키는 어디에도 보내지 않는다.
    const encrypt = $("encrypt-check").checked;
    const limit = CONFIG.MAX_FILE_MB * MB;
    const plainTotal = items.reduce((n, it) => n + it.blob.size, 0);
    if (plainTotal > limit) {
      throw new ApiError("FILE_TOO_LARGE", `파일 합계가 ${CONFIG.MAX_FILE_MB}MB를 초과합니다. 파일을 빼 주세요.`, 413);
    }
    let payloads = items.map((it, i) => ({ blob: it.blob, size: it.blob.size, sha256: hashes[i] }));
    let keyText = null;
    if (encrypt) {
      if (plainTotal + OVERHEAD * items.length > limit) {
        throw new ApiError("FILE_TOO_LARGE", `암호화하면 파일마다 ${OVERHEAD}바이트가 늘어나 합계가 ${CONFIG.MAX_FILE_MB}MB를 넘습니다. 파일을 조금 줄여 주세요.`, 413);
      }
      setProgress("브라우저에서 암호화하는 중…", 0);
      const { key, encoded } = await generateKey();
      keyText = encoded;
      payloads = [];
      for (const it of items) {
        const blob = await encryptBlob(it.blob, key);
        payloads.push({ blob, size: blob.size, sha256: await sha256Hex(blob) });
      }
    }

    stage = "create";
    setProgress("업로드 준비 중…", 0);
    created = await api.createTransfer(buildCreateBody({
      items, payloads, expiresIn: Number($("expires").value), encrypted: encrypt, flags,
    }));
    // 관리 토큰은 이 응답에만 온다 — 바로 보관
    saveToken(created.id, created.manage_token);

    stage = "upload";
    await uploadAll(
      created.uploads, payloads,
      (upload, p, onProgress) => api.uploadToS3(upload, p.blob, onProgress),
      (r) => setProgress(`업로드 중… ${Math.round(r * 100)}%`, r),
    );

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
    state.sent = true;
    sanitize.hideSanitize();
    guard.showSent(entries.length === 1 ? nameOf(entries[0]) : `파일 ${entries.length}개`);
    if (keyText) {
      // 서버가 만든 QR에는 키가 없으므로, 키를 붙인 링크로 브라우저에서 QR을 다시 그린다.
      const shareUrl = withKey(done.share_url, keyText);
      showTransfer({ ...done, share_url: shareUrl, qr: qrDataUrl(shareUrl), encrypted: true }, { onNew: reset });
    } else {
      showTransfer(done, { onNew: reset });
    }
  } catch (e) {
    // 서버 응답이 아닌 브라우저 내부 오류를 "서버 오류"로 보이지 않게 구분
    const err = e instanceof ApiError ? e : new ApiError("CLIENT_ERROR");
    setProgress(null);
    errBox.textContent = err.userMessage;
    errBox.hidden = false;
    // 업로드 단계 이후 실패한 전송은 쓸 수 없다 (서버가 폐기했거나 업로드 권한이 끝남) → 토큰 정리
    if (created && stage !== "create") removeToken(created.id);
    if (err.code === "EXPIRED" && stage === "create") {
      errBox.textContent = "검사·정제 결과 유효 시간(10분)이 지나 파일을 다시 검사합니다. 정제한 파일은 다시 정제한 뒤 전송하세요.";
      rescanAll();
    } else if (err.code === "EXPIRED" && stage === "complete") {
      errBox.textContent = "업로드 유효 시간(2분)이 지났습니다. 다시 전송하세요.";
    }
  } finally {
    sending = false;
    $("btn-reset").disabled = false;
    render();
  }
}

// ---------------------------------------------------------------- SecureGuard 버튼

function handleGuardAction(action) {
  if (typeof action === "object") {
    applyAssist(action);
  } else if (action === "CANCEL") {
    cancelFocused();
  } else if (action === "MASK" || action === "DELETE") {
    enterSelect(action);
  } else if (action === "MORE" && state.sanitized) {
    // 남은 유형까지 정제: 원본 + 늘어난 조치로 다시 만든다
    state.mode = "select";
    sanitize.reopenWithRemaining(state.sanitized.res.findings_after);
    state.sanitized = null;
    render();
  }
}

// SecureGuard의 "전송 취소": 지금 설명 중인 파일을 이번 전송에서 뺀다 (하나뿐이면 전송 전체 취소)
function cancelFocused() {
  const entry = focused();
  if (!entry || sending) return;
  const last = state.files.length === 1;
  removeEntry(entry);
  toast(last ? "전송을 취소했습니다." : `${entry.file.name}을(를) 전송 목록에서 뺐습니다.`);
}
