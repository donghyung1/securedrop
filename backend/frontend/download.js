"use strict";
async function loadDownload() {
  const status = document.getElementById("status");
  const files = document.getElementById("files");
  const refresh = document.getElementById("refresh");
  files.replaceChildren();
  refresh.disabled = true;
  try {
    const id = new URLSearchParams(location.search).get("id");
    if (!id || !/^[a-f0-9]{32}$/.test(id)) throw new Error("올바른 공유 링크가 아닙니다.");
    const result = await api(`/api/d/${id}`);
    if (result.encrypted) throw new Error("암호화된 파일입니다. 암호화를 지원하는 클라이언트에서 열어주세요.");
    status.textContent = `만료: ${new Date(result.expires_at).toLocaleString("ko-KR")} · 다운로드 주소는 최대 5분간 유효합니다.`;
    for (const file of result.files) {
      const line = document.createElement("p");
      const link = document.createElement("a");
      const url = new URL(file.url);
      if (url.protocol !== "https:") throw new Error("다운로드 주소가 올바르지 않습니다.");
      link.href = url.href;
      link.rel = "noopener noreferrer";
      link.textContent = `${file.name} (${(file.size / 1024).toFixed(1)} KiB) 다운로드`;
      line.append(link);
      files.append(line);
    }
  } catch (error) { status.textContent = error.message || "파일을 불러올 수 없습니다."; }
  finally { refresh.disabled = false; }
}
document.getElementById("refresh").onclick = loadDownload;
loadDownload();
