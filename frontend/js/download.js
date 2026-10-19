// 휴대폰 다운로드 페이지 (여정 8): d.html?id=<전송 ID>[#k=<복호화 키>]
// GET /api/d/{id} 가 상태·만료를 확인하고 5분짜리 Presigned GET을 준다. 파일은 S3에서 직접 받는다.
// 주의: 서버는 /api/d/{id} 호출마다 다운로드 수를 올리므로, URL이 만료될 때만 다시 받는다.
import { api, CONFIG, ApiError } from "./api.js";
import { toast, fmtSize, showModeBadge } from "./ui.js";

const $ = (id) => document.getElementById(id);
const URL_TTL_MS = 4.5 * 60 * 1000; // Presigned GET 5분보다 약간 짧게

const id = new URLSearchParams(location.search).get("id");
// #k= 키는 HTTP 요청에 실리지 않는다 (F12 브라우저 복호화용)
const key = new URLSearchParams(location.hash.slice(1)).get("k");

let fetchedAt = 0;
let files = [];
let timer = null;

showModeBadge(CONFIG);
load();

async function load() {
  if (!id) return showError("잘못된 링크입니다", "QR 코드를 다시 스캔하거나 보낸 사람에게 링크를 다시 요청하세요.");
  try {
    const res = await api.getDownload(id);
    fetchedAt = Date.now();
    files = res.files;
    if (res.encrypted) {
      // F12(선택) 미구현: 복호화 화면은 crypto.js가 들어오면 연결한다
      return key
        ? showError("암호화된 파일입니다", "이 버전에서는 아직 브라우저 복호화를 지원하지 않습니다.")
        : showError("복호화 키가 없는 링크입니다", "QR 코드를 다시 스캔하세요. 링크 끝의 #k=… 부분이 필요합니다.");
    }
    render(res);
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError("INTERNAL_ERROR");
    if (err.code === "EXPIRED") showError("만료된 링크입니다", "보낸 사람에게 새 링크를 요청하세요.");
    else if (err.code === "NOT_FOUND") showError("폐기되었거나 없는 링크입니다", "보낸 사람에게 새 링크를 요청하세요.");
    else showError("파일 정보를 불러오지 못했습니다", err.userMessage);
  }
}

function render(res) {
  $("dl-loading").hidden = true;
  $("dl-ready").hidden = false;
  const list = $("dl-files");
  list.innerHTML = "";
  files.forEach((f, i) => {
    const li = document.createElement("li");
    const info = document.createElement("div");
    const name = document.createElement("div");
    name.className = "file-name";
    name.textContent = f.name;
    const size = document.createElement("div");
    size.className = "muted";
    size.textContent = fmtSize(f.size);
    info.append(name, size);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-primary";
    btn.textContent = "저장";
    btn.addEventListener("click", () => download(i, btn));
    li.append(info, btn);
    list.appendChild(li);
  });
  if (res.expires_at) startCountdown(Date.parse(res.expires_at));
  else $("dl-countdown").parentElement.hidden = true;
}

async function download(index, btn) {
  btn.disabled = true;
  try {
    if (Date.now() - fetchedAt > URL_TTL_MS) {
      const res = await api.getDownload(id);
      fetchedAt = Date.now();
      files = res.files;
    }
    const url = files[index]?.url;
    if (!url) {
      toast("mock 모드에서는 1MB 이하 파일만 실제로 받을 수 있습니다.");
      return;
    }
    // 원래 파일명은 S3 응답의 Content-Disposition이 붙인다
    const a = document.createElement("a");
    a.href = url;
    a.download = files[index].name; // 같은 출처(data:)일 때만 적용, S3는 Content-Disposition 사용
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError("INTERNAL_ERROR");
    if (err.code === "EXPIRED" || err.code === "NOT_FOUND") load();
    else toast(err.userMessage);
  } finally {
    btn.disabled = false;
  }
}

function startCountdown(expiresAt) {
  clearInterval(timer);
  const tick = () => {
    const left = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
    $("dl-countdown").textContent =
      `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(left % 60).padStart(2, "0")}`;
    if (left === 0) {
      clearInterval(timer);
      showError("만료된 링크입니다", "보낸 사람에게 새 링크를 요청하세요.");
    }
  };
  tick();
  timer = setInterval(tick, 1000);
}

function showError(title, text) {
  clearInterval(timer);
  $("dl-loading").hidden = true;
  $("dl-ready").hidden = true;
  $("dl-error").hidden = false;
  $("dl-error-title").textContent = title;
  $("dl-error-text").textContent = text;
}
