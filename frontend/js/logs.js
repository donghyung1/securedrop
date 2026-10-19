// 이력 화면 (F6): GET /api/logs
import { api, ApiError, typeLabel } from "./api.js";
import { toast } from "./ui.js";

const EVENT_LABELS = { BLOCK: "차단", SANITIZE: "정제", TRANSFER: "전송", REVOKE: "폐기" };
const SOURCE_LABELS = { extension: "확장", web: "웹", plugin: "플러그인" };

export function initLogs() {
  document.getElementById("btn-logs-refresh").addEventListener("click", loadLogs);
}

export async function loadLogs() {
  const body = document.getElementById("logs-body");
  const empty = document.getElementById("logs-empty");
  try {
    const { items } = await api.getLogs(50);
    body.innerHTML = "";
    for (const it of items) {
      const tr = document.createElement("tr");
      const cells = [
        new Date(it.time).toLocaleString("ko-KR"),
        EVENT_LABELS[it.event] || it.event,
        (it.types || []).map(typeLabel).join(", ") || "—",
        SOURCE_LABELS[it.source] || it.source,
      ];
      for (const c of cells) {
        const td = document.createElement("td");
        td.textContent = c;
        tr.appendChild(td);
      }
      tr.children[1].className = `ev ev-${(it.event || "").toLowerCase()}`;
      body.appendChild(tr);
    }
    empty.hidden = items.length > 0;
  } catch (e) {
    toast(e instanceof ApiError ? e.userMessage : "이력을 불러오지 못했습니다.");
  }
}
