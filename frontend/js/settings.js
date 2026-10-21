// 화면 고정값 (상한·확장자·만료 선택지)과 mock 전환
// - 배포 주소(apiBase)는 웹 루트의 config.js(window.SD_CONFIG)에서 읽는다. 관리자 콘솔과 같은 파일이다.
// - USE_MOCK 전환: URL에 ?api=real 또는 ?api=mock (localStorage에 기억)

const params = new URLSearchParams(location.search);
const apiParam = params.get("api");
if (apiParam === "real" || apiParam === "mock") {
  try { localStorage.setItem("sd.api", apiParam); } catch { /* 저장 불가 시 무시 */ }
}
let savedMode = null;
try { savedMode = localStorage.getItem("sd.api"); } catch { /* 저장소 차단 */ }

export const CONFIG = {
  API_BASE: String(window.SD_CONFIG?.apiBase || "http://localhost:8000").replace(/\/+$/, ""),
  USE_MOCK: (apiParam || savedMode || window.SD_CONFIG?.defaultApiMode || "mock") === "mock",

  MAX_FILES: 5,           // 한 번에 보낼 수 있는 파일 수
  MAX_FILE_MB: 20,        // 전송 합계 상한 (파일 합계, 서버 max_file_mb)
  SCAN_FILE_MAX_MB: 4,    // 검사·정제 상한 (Lambda 페이로드 한도)
  SCANNABLE_EXTS: ["txt", "csv"],
  // 업로드 허용 확장자 — v1.3에 목록이 없어 A의 v1.2 백엔드 목록을 유지. A와 확정 필요.
  ALLOWED_EXTS: ["txt", "csv", "json", "md", "log", "xml", "yaml", "yml", "pdf", "png", "jpg", "jpeg", "gif", "webp"],
  EXPIRES_OPTIONS: [
    { sec: 300, label: "5분" },
    { sec: 600, label: "10분" },
    { sec: 3600, label: "60분" },
  ],
  DEFAULT_EXPIRES_SEC: 600,
};
