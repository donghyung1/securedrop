// 배포 환경별 설정 (PRD v1.3 §12: CloudFront 도메인·API 주소는 이 파일 한 곳에서 관리)
// - USE_MOCK 전환: URL에 ?api=real 또는 ?api=mock (localStorage에 기억)

const params = new URLSearchParams(location.search);
const apiParam = params.get("api");
if (apiParam === "real" || apiParam === "mock") {
  try { localStorage.setItem("sd.api", apiParam); } catch { /* 저장 불가 시 무시 */ }
}
let savedMode = null;
try { savedMode = localStorage.getItem("sd.api"); } catch { /* 저장소 차단 */ }

export const CONFIG = {
  // 로컬: http://localhost:8000
  // 배포: https://<api-id>.execute-api.ap-northeast-2.amazonaws.com/prod
  API_BASE: "http://localhost:8000",
  USE_MOCK: (apiParam || savedMode || "mock") === "mock",

  MAX_FILE_MB: 20,        // 전송 상한 (S3 Presigned POST content-length-range)
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
