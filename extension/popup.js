const DEFAULTS = { apiBase: SD_CONFIG.apiBase, webBase: SD_CONFIG.webBase, failClosed: SD_CONFIG.failClosed };
const apiInput = document.getElementById("apiBase");
const webInput = document.getElementById("webBase");
const failClosed = document.getElementById("failClosed");
const status = document.getElementById("status");

function show(text, ok) {
  status.textContent = text;
  status.className = ok ? "ok" : "bad";
}

function originOf(value) {
  const url = new URL(value.trim());
  if (!["http:", "https:"].includes(url.protocol)) throw new Error();
  // API Gateway 주소는 /prod 같은 스테이지 경로를 유지해야 한다.
  return (url.origin + url.pathname).replace(/\/+$/, "");
}

chrome.storage.sync.get(DEFAULTS).then((saved) => {
  apiInput.value = saved.apiBase;
  webInput.value = saved.webBase;
  failClosed.checked = saved.failClosed !== false;
});

document.getElementById("save").addEventListener("click", async () => {
  let apiBase;
  let webBase;
  try {
    apiBase = originOf(apiInput.value);
    webBase = originOf(webInput.value);
  } catch {
    return show("올바른 주소를 입력하세요.", false);
  }
  await chrome.storage.sync.set({ apiBase, webBase, failClosed: failClosed.checked });
  apiInput.value = apiBase;
  webInput.value = webBase;
  show("확인 중...", true);
  const result = await chrome.runtime.sendMessage({ kind: "health" }).catch(() => ({ ok: false }));
  show(
    result?.ok ? "서버에 연결되었습니다." : "서버에 연결할 수 없습니다. 주소·host_permissions·ALLOWED_ORIGINS를 확인하세요.",
    !!result?.ok,
  );
});
