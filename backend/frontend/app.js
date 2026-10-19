"use strict";
const $ = (id) => document.getElementById(id);
const labels = { RRN: "주민등록번호", PHONE: "전화번호", EMAIL: "이메일", AWS_ACCESS_KEY: "AWS 키", API_KEY: "API 키", CARD: "카드번호" };
let draftId = null;
let transfer = null;
let findings = [];
let busy = false;
let scanReady = false;
const scanExtensions = new Set(["txt", "csv"]);
const describe = (items) => items.map((item) => `${labels[item.type] || item.type} ${item.count}건`).join(" · ");

async function api(path, options = {}) {
  const headers = new Headers(options.headers);
  if ($("api-key").value.trim()) headers.set("X-API-Key", $("api-key").value.trim());
  const response = await fetch(path, { ...options, headers, cache: "no-store", referrerPolicy: "no-referrer" });
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.error?.message || `요청 실패 (${response.status})`), { data, status: response.status });
  return data;
}

function sync() {
  $("upload").disabled = busy || !scanReady || !$("sensitive").hidden || !$("file").files.length || Boolean(draftId) || Boolean(transfer);
  $("original").disabled = busy || !$("risk").checked || (Boolean(draftId) && !$("approve").checked);
  $("send-draft").disabled = busy || !draftId || !$("approve").checked;
  $("file").disabled = busy || Boolean(draftId) || Boolean(transfer);
  $("api-key").disabled = busy;
  for (const id of ["scan", "sample", "mask", "remove", "cancel", "revoke", "copy"]) $(id).disabled = busy;
}

async function run(task) {
  if (busy) return;
  busy = true;
  $("status").className = "";
  $("status").textContent = "처리 중…";
  sync();
  try { await task(); }
  catch (error) {
    $("status").className = "error";
    $("status").textContent = error.message || "서버에 연결할 수 없습니다.";
  } finally { busy = false; sync(); }
}

function clearSensitive() {
  $("sensitive").hidden = true;
  $("risk").checked = false;
  findings = [];
}

async function send(confirmSensitive = false) {
  const form = new FormData();
  if (draftId) form.set("sanitize_id", draftId);
  else form.set("file", $("file").files[0]);
  form.set("expires_in", $("expiry").value);
  form.set("user_approved", "true");
  form.set("confirm_sensitive", String(confirmSensitive));
  const extension = $("file").files[0]?.name.split(".").pop().toLowerCase();
  form.set("confirm_unscanned", String(confirmSensitive && !scanExtensions.has(extension)));
  try {
    transfer = await api("/api/transfers", { method: "POST", body: form });
  } catch (error) {
    if (error.status !== 409 && !(error.status === 415 && error.data.confirmation_required)) throw error;
    findings = error.data.findings || [];
    $("findings").textContent = error.status === 415 ? "이 형식은 민감정보 검사를 지원하지 않습니다. 내용을 직접 확인한 후 전송하세요." : `민감정보 발견: ${describe(findings)}`;
    $("sensitive").hidden = false;
    $("sanitize-actions").hidden = !error.data.sanitize_available;
    $("risk").checked = false;
    $("status").textContent = "전송이 중단되었습니다. 정제하거나 위험을 확인하세요.";
    return;
  }
  draftId = null;
  $("draft").hidden = true;
  $("preview").textContent = "";
  clearSensitive();
  $("qr").src = transfer.qr;
  $("save-qr").href = transfer.qr;
  $("save-qr").hidden = false;
  $("download").href = transfer.download_url;
  $("download").hidden = false;
  $("qr").hidden = false;
  $("copy").hidden = false;
  $("revoke").hidden = false;
  $("expires").textContent = `만료: ${new Date(transfer.expires_at).toLocaleString("ko-KR")}`;
  $("transfer-status").textContent = "링크를 가진 사람은 누구나 다운로드할 수 있습니다.";
  $("transfer").hidden = false;
  $("status").textContent = "전송 완료. 새 파일을 전송하려면 현재 전송을 폐기하거나 페이지를 새로 여세요.";
}

async function sanitize(action) {
  if (draftId) await discard();
  const form = new FormData();
  form.set("file", $("file").files[0]);
  // Reapply to the original using every supported type, including previously masked types.
  form.set("actions", JSON.stringify([{ action, targets: Object.keys(labels) }]));
  const result = await api("/api/sanitize", { method: "POST", body: form });
  draftId = result.sanitize_id;
  $("preview").textContent = result.preview;
  $("draft-result").textContent = result.clean ? "재검사 완료: 탐지된 민감정보 없음" : `남은 민감정보: ${describe(result.findings_after)}`;
  $("approve").checked = false;
  $("draft").hidden = false;
  clearSensitive();
  $("status").textContent = "처리본은 아직 업로드되지 않았습니다. 미리보기 확인 후 전송하세요.";
}

async function discard() {
  if (draftId) {
    try { await api(`/api/sanitize/${draftId}`, { method: "DELETE" }); }
    catch (error) { if (error.status !== 410) throw error; }
  }
  draftId = null;
  $("draft").hidden = true;
  $("preview").textContent = "";
  $("approve").checked = false;
}

$("sample").onclick = () => { $("text").value = "테스트 연락처: 010-1234-5678\n이메일: demo@example.com"; };
$("scan").onclick = () => run(async () => {
  const result = await api("/api/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: $("text").value }) });
  $("scan-result").textContent = result.blocked ? `민감정보 발견: ${describe(result.findings)}` : "검사 완료: 탐지된 민감정보 없음";
  $("status").textContent = "텍스트 검사를 완료했습니다.";
});
$("file").onchange = () => run(async () => {
  clearSensitive();
  scanReady = false;
  const file = $("file").files[0];
  if (file && (!file.size || file.size > 20 * 1024 * 1024)) {
    $("file").value = "";
    $("status").textContent = "비어 있지 않은 20 MiB 이하 파일을 선택하세요.";
    return;
  }
  if (!file) { $("status").textContent = ""; return; }
  if (!scanExtensions.has(file.name.split(".").pop().toLowerCase())) {
    $("findings").textContent = "이 형식은 민감정보 검사를 지원하지 않습니다. 내용을 직접 확인한 후 전송하세요.";
    $("sanitize-actions").hidden = true;
    $("sensitive").hidden = false;
    $("status").textContent = "검사 미지원 파일입니다. 전송하려면 별도로 확인하세요.";
    return;
  }
  const form = new FormData();
  form.set("file", file);
  const result = await api("/api/scan/file", { method: "POST", body: form });
  scanReady = true;
  findings = result.findings;
  if (result.blocked) {
    $("findings").textContent = `민감정보 발견: ${describe(findings)}`;
    $("sanitize-actions").hidden = false;
    $("sensitive").hidden = false;
    $("status").textContent = "원본 전송이 보류되었습니다. 정제하거나 위험을 확인하세요.";
  } else $("status").textContent = "파일 검사 완료: 탐지된 민감정보 없음";
});
$("upload").onclick = () => run(() => send());
$("original").onclick = () => run(() => send(true));
$("mask").onclick = () => run(() => sanitize("MASK"));
$("remove").onclick = () => run(() => sanitize("DELETE"));
$("send-draft").onclick = () => run(() => send());
$("cancel").onclick = () => run(async () => { await discard(); clearSensitive(); $("status").textContent = "처리본을 폐기했습니다."; });
$("risk").onchange = sync;
$("approve").onchange = sync;
$("copy").onclick = () => run(async () => { await navigator.clipboard.writeText(transfer.download_url); $("status").textContent = "다운로드 링크를 복사했습니다."; });
$("revoke").onclick = () => run(async () => {
  await api(`/api/transfers/${transfer.id}`, { method: "DELETE" });
  transfer = null;
  $("download").removeAttribute("href");
  $("qr").removeAttribute("src");
  $("save-qr").removeAttribute("href");
  for (const id of ["download", "qr", "save-qr", "copy", "revoke"]) $(id).hidden = true;
  $("transfer-status").textContent = "전송이 폐기되었습니다.";
  $("status").textContent = "파일을 다시 선택하여 새 링크를 만들 수 있습니다.";
});
fetch("/api/health", { cache: "no-store" }).then((response) => {
  if (!response.ok) throw new Error();
  $("health").textContent = "● 서버 연결됨";
}).catch(() => { $("health").textContent = "서버 연결 실패"; });
sync();
