const DEFAULTS = { serverUrl: "http://localhost:8000", failClosed: true };
const urlInput = document.getElementById("serverUrl");
const failClosed = document.getElementById("failClosed");
const status = document.getElementById("status");

function show(text, ok) {
  status.textContent = text;
  status.className = ok ? "ok" : "bad";
}

chrome.storage.sync.get(DEFAULTS).then((saved) => {
  urlInput.value = saved.serverUrl;
  failClosed.checked = saved.failClosed !== false;
});

document.getElementById("save").addEventListener("click", async () => {
  let url;
  try {
    url = new URL(urlInput.value.trim());
    if (!["http:", "https:"].includes(url.protocol)) throw new Error();
  } catch {
    return show("올바른 서버 주소를 입력하세요.", false);
  }
  const serverUrl = url.origin;
  await chrome.storage.sync.set({ serverUrl, failClosed: failClosed.checked });
  urlInput.value = serverUrl;
  show("확인 중...", true);
  const result = await chrome.runtime.sendMessage({ kind: "health" }).catch(() => ({ ok: false }));
  show(
    result?.ok ? "서버에 연결되었습니다." : "서버에 연결할 수 없습니다. 주소·실행 상태·ALLOWED_ORIGINS를 확인하세요.",
    !!result?.ok,
  );
});
