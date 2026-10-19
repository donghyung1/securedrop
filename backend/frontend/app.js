"use strict";
const $ = (id) => document.getElementById(id);
const labels = { RRN: "주민등록번호", PHONE: "전화번호", EMAIL: "이메일", AWS_ACCESS_KEY: "AWS 키", API_KEY: "API 키", CARD: "카드번호" };
let draftId = null;
let draftFile = null;
let scanResult = null;
let draftClean = true;
let transfer = null;
let findings = [];
let busy = false;
let scanReady = false;
const scanExtensions = new Set(["txt", "csv"]);
const describe = (items) => items.map((item) => `${labels[item.type] || item.type} ${item.count}건`).join(" · ");


function sync() {
  $("upload").disabled = busy || !scanReady || !$("sensitive").hidden || !$("file").files.length || Boolean(draftId) || Boolean(transfer);
  $("original").disabled = busy || !$("risk").checked || (Boolean(draftId) && !$("approve").checked);
  $("send-draft").disabled = busy || !draftId || (!$("approve").checked || (!draftClean && !$("draft-risk").checked));
  $("file").disabled = busy || Boolean(draftId) || Boolean(transfer);
  $("expiry").disabled = busy;
  for (const id of ["risk", "approve", "draft-risk"]) $(id).disabled = busy;
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
  const file = draftFile || $("file").files[0];
  const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer())), (b) => b.toString(16).padStart(2, "0")).join("");
  const extension = file.name.split(".").pop().toLowerCase();
  const payload = {
    files: [{ name: file.name, size: file.size, type: file.type || "application/octet-stream", sha256 }],
    expires_in: Number($("expiry").value),
    user_approved: Boolean(draftId && $("approve").checked),
    acknowledge_risk: confirmSensitive || Boolean(draftId && $("draft-risk").checked),
    acknowledge_unsupported: confirmSensitive && !scanExtensions.has(extension),
    ...(draftId ? { sanitize_id: draftId } : scanResult ? { scan_id: scanResult.scan_id } : {}),
  };
  const pending = await api("/api/transfers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  try {
    const form = new FormData();
    for (const [key, value] of Object.entries(pending.uploads[0].fields)) form.append(key, value);
    form.append("file", file);
    const uploaded = await fetch(pending.uploads[0].url, { method: "POST", body: form, referrerPolicy: "no-referrer" });
    if (!uploaded.ok) throw new Error(`파일 업로드 실패 (${uploaded.status})`);
    const completed = await api(`/api/transfers/${pending.id}/complete`, { method: "POST", headers: { "X-Manage-Token": pending.manage_token } });
    transfer = { ...completed, manage_token: pending.manage_token };
  } catch (error) {
    try { await api(`/api/transfers/${pending.id}`, { method: "DELETE", headers: { "X-Manage-Token": pending.manage_token } }); }
    catch { transfer = pending; $("transfer").hidden = false; $("revoke").hidden = false; $("transfer-status").textContent = "업로드 정리에 실패했습니다. 전송 폐기를 다시 눌러주세요."; }
    throw error;
  }
  discard();
  clearSensitive();
  $("qr").src = transfer.qr;
  $("save-qr").href = transfer.qr;
  $("save-qr").hidden = false;
  $("download").href = transfer.share_url;
  for (const id of ["download", "qr", "copy", "revoke"]) $(id).hidden = false;
  $("expires").textContent = `만료: ${new Date(transfer.expires_at).toLocaleString("ko-KR")}`;
  $("transfer-status").textContent = "링크를 가진 사람은 누구나 다운로드할 수 있습니다. 이 탭에서 전송을 폐기할 수 있습니다.";
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
  const bytes = Uint8Array.from(atob(result.content_b64), (c) => c.charCodeAt(0));
  const original = $("file").files[0];
  draftFile = new File([bytes], original.name, { type: original.type || "text/plain" });
  draftClean = result.clean;
  $("draft-risk").checked = false;
  $("draft-risk-label").hidden = result.clean;
  $("preview").textContent = result.preview;
  $("draft-result").textContent = result.clean ? "재검사 완료: 탐지된 민감정보 없음" : `남은 민감정보: ${describe(result.findings_after)}`;
  $("approve").checked = false;
  $("draft").hidden = false;
  clearSensitive();
  $("status").textContent = "처리본은 아직 업로드되지 않았습니다. 미리보기 확인 후 전송하세요.";
}

function discard() {
  draftId = null;
  draftFile = null;
  draftClean = true;
  $("draft").hidden = true;
  $("preview").textContent = "";
  $("approve").checked = false;
  $("draft-risk").checked = false;
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
  scanResult = null;
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
  if (file.size > 4 * 1024 * 1024) throw new Error("TXT·CSV 검사·전송은 최대 4 MiB입니다. 더 작은 파일을 선택하세요.");
  const form = new FormData();
  form.set("file", file);
  const result = await api("/api/scan/file", { method: "POST", body: form });
  scanResult = result;
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
$("cancel").onclick = () => run(async () => {
  discard();
  clearSensitive();
  if (scanResult?.blocked) {
    findings = scanResult.findings;
    $("findings").textContent = `민감정보 발견: ${describe(findings)}`;
    $("sanitize-actions").hidden = false;
    $("sensitive").hidden = false;
  }
  $("status").textContent = "처리본을 폐기했습니다. 원본을 정제하거나 다른 파일을 선택하세요.";
});
$("risk").onchange = sync;
$("approve").onchange = sync;
$("draft-risk").onchange = sync;
$("api-docs").href = window.SECUREDROP_API_BASE + "/docs";
$("copy").onclick = () => run(async () => { await navigator.clipboard.writeText(transfer.share_url); $("status").textContent = "다운로드 링크를 복사했습니다."; });
$("revoke").onclick = () => run(async () => {
  await api(`/api/transfers/${transfer.id}`, { method: "DELETE", headers: { "X-Manage-Token": transfer.manage_token } });
  transfer = null;
  scanReady = false;
  scanResult = null;
  $("file").value = "";
  $("download").removeAttribute("href");
  $("qr").removeAttribute("src");
  $("save-qr").removeAttribute("href");
  for (const id of ["download", "qr", "save-qr", "copy", "revoke"]) $(id).hidden = true;
  $("transfer-status").textContent = "전송이 폐기되었습니다.";
  $("status").textContent = "파일을 다시 선택하여 새 링크를 만들 수 있습니다.";
});
fetch(window.SECUREDROP_API_BASE + "/api/health", { cache: "no-store" }).then((response) => {
  if (!response.ok) throw new Error();
  $("health").textContent = "● 서버 연결됨";
}).catch(() => { $("health").textContent = "서버 연결 실패"; });
sync();
