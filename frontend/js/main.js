// 진입점: 탭 전환, API 모드 표시, 화면 모듈 초기화
import { CONFIG } from "./api.js";
import { initUpload } from "./upload.js";
import { initLogs, loadLogs } from "./logs.js";

const badge = document.getElementById("api-mode");
badge.textContent = CONFIG.USE_MOCK ? "MOCK API" : "API " + CONFIG.BASE_URL.replace(/^https?:\/\//, "");
badge.classList.toggle("badge-mock", CONFIG.USE_MOCK);

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const view = tab.dataset.view;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
    document.querySelectorAll(".view").forEach((v) => { v.hidden = v.id !== `view-${view}`; });
    if (view === "logs") loadLogs();
  });
});

initUpload();
initLogs();
