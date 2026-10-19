// QR 결과 카드: QR 이미지, 만료 카운트다운, 링크 복사·폐기
import { api, ApiError } from "./api.js";
import { toast } from "./ui.js";

const $ = (id) => document.getElementById(id);
let timer = null;
let current = null;
let onNewHandler = () => {};

// 버튼 리스너는 한 번만 단다 (showTransfer가 여러 번 호출되어도 중복되지 않게)
let bound = false;
function bindOnce() {
  if (bound) return;
  bound = true;
  $("btn-copy").addEventListener("click", copyLink);
  $("btn-revoke").addEventListener("click", revoke);
  $("btn-new").addEventListener("click", () => onNewHandler());
}

export function showTransfer(transfer, { onNew }) {
  bindOnce();
  current = transfer;
  onNewHandler = onNew;
  $("qr-img").src = transfer.qr;
  $("qr-status").textContent = "링크 활성";
  $("btn-revoke").disabled = false;
  $("btn-copy").disabled = false;
  $("qr-card").hidden = false;
  startCountdown(Date.parse(transfer.expires_at));
}

export function hideTransfer() {
  clearInterval(timer);
  current = null;
  $("qr-card").hidden = true;
}

function startCountdown(expiresAt) {
  clearInterval(timer);
  const tick = () => {
    const left = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
    const m = String(Math.floor(left / 60)).padStart(2, "0");
    const s = String(left % 60).padStart(2, "0");
    $("qr-countdown").textContent = `${m}:${s}`;
    if (left === 0) {
      clearInterval(timer);
      markInactive("만료됨 · 더 이상 다운로드할 수 없습니다");
    }
  };
  tick();
  timer = setInterval(tick, 1000);
}

function markInactive(text) {
  $("qr-status").textContent = text;
  $("qr-img").classList.add("is-dead");
  $("btn-revoke").disabled = true;
  $("btn-copy").disabled = true;
}

async function copyLink() {
  if (!current) return;
  try {
    // download_url: 서버의 짧은 공유 주소(/d/{id}). url은 구버전 호환용 S3 Presigned URL
    await navigator.clipboard.writeText(current.download_url || current.url);
    toast("링크를 복사했습니다.");
  } catch {
    toast("복사에 실패했습니다.");
  }
}

async function revoke() {
  if (!current) return;
  $("btn-revoke").disabled = true;
  try {
    await api.revokeTransfer(current.id);
    clearInterval(timer);
    markInactive("폐기됨 · 링크 접근이 차단되었습니다");
    toast("링크를 폐기했습니다.");
  } catch (e) {
    $("btn-revoke").disabled = false;
    toast(e instanceof ApiError ? e.userMessage : "폐기에 실패했습니다.");
  }
}
