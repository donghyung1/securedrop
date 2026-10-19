# SecureDrop Cloud Frontend (C)

PRD v1.3 웹 UI입니다. Vanilla JS(ES modules), 빌드 없음, 외부 스크립트 없음(CloudFront CSP `script-src 'self'` 대응).
`backend/frontend/`는 A의 v1.2 백엔드 테스트 화면이며 v1.3 API와 맞지 않습니다. 데모 화면은 이 폴더입니다.


## 실행

```bash
cd frontend && python3 -m http.server 5500
```

- `http://localhost:5500/?api=mock` — 서버·S3 없이 mock으로 동작 (기본값)
- `http://localhost:5500/?api=real` — `config.js`의 `apiBase`에 연결
- 선택한 모드는 브라우저에 기억됩니다. 화면 오른쪽 위 배지로 현재 모드를 확인하세요.
- mock 모드에서도 QR 링크(`d.html?id=…`)를 같은 브라우저 새 탭에서 열면 다운로드 화면을 확인할 수 있습니다. 1MB 이하 파일만 실제로 저장됩니다.

배포 주소(API Gateway `/prod`)는 웹 루트의 `config.js`(`window.SD_CONFIG.apiBase`) 한 곳만 바꾸면 됩니다. B의 관리자 콘솔(`admin.html`)도 같은 파일을 읽으므로, 배포 시 `admin.*`를 이 폴더에 함께 두면 됩니다.
테스트 파일은 저장소 루트 `samples/`에 있습니다. 모두 가상 데이터입니다.

## 페이지와 파일

| 파일 | 역할 |
|---|---|
| `index.html` | 업로드 + SecureGuard 패널 + QR 결과 |
| `d.html` | 휴대폰 다운로드 페이지 (`GET /api/d/{id}`) |
| `config.js` | 배포 주소 `window.SD_CONFIG` (`apiBase`, `cloudwatchUrl`) — 관리자 콘솔과 공유 |
| `js/settings.js` | 상한(전송 20MB·검사 4MB), 확장자, 만료 선택지, mock 전환 |
| `js/api.js` | API 클라이언트 + v1.3 계약을 흉내 내는 mock, SHA-256 |
| `js/upload.js` | 선택 → `/api/scan/file` → 해시 → `/api/transfers` → S3 직접 업로드 → `/complete` |
| `js/manage.js` | 관리 토큰 보관 (sessionStorage), `X-Manage-Token` |
| `js/qr.js` | QR, 만료 카운트다운, 다운로드 수, 링크 복사·폐기 |
| `js/download.js` | 다운로드 페이지 로직 |
| `js/assist.js` | SecureGuard 패널 (규칙 기반 문구 + 조치 버튼) |
| `js/main.js`, `js/ui.js` | 진입점, 토스트·공통 도우미 |

## 백엔드 계약 메모

`backend/README.md`(A, v1.3)의 "웹 파일 전송 계약"을 따릅니다. 화면 동작에 영향이 큰 규칙:

- .txt/.csv는 검사(`scan_id`) 또는 정제(`sanitize_id`) 참조가 있어야만 전송됩니다. 그래서 4MB를 넘는 .txt/.csv는 보낼 수 없다고 안내합니다.
- 그 외 형식은 `acknowledge_unsupported`, 민감정보가 남은 파일은 `acknowledge_risk`(없으면 409 `SENSITIVE_REMAINS`), 처리본은 `user_approved`가 필요합니다.
- 로컬 실행 시 백엔드 `.env`의 `ALLOWED_ORIGINS`에 `http://localhost:5500`, `PUBLIC_BASE_URL`은 `http://localhost:5500`으로 두면 QR이 이 폴더의 `d.html`을 가리킵니다. S3 버킷 CORS에도 `http://localhost:5500`이 있어야 합니다.

## 진행 상황

- [x] STEP 1~2: 선택 즉시 검사, 민감정보 원본 보류, 검사 미지원 형식 확인, 4MB 초과 .txt/.csv 차단
- [x] 3단계 업로드(SHA-256, Presigned POST, 진행률), 관리 토큰, QR, 상태 조회, 폐기
- [x] `d.html` 휴대폰 다운로드 (만료 410·폐기 404 화면)
- [ ] 여정 3~4: SecureGuard 조치 → `/api/sanitize` → 미리보기 승인 → 처리본 전송 (Day 3)
- [ ] 자연어 요청 `/api/assist`, 플러그인 도구 (Day 4)
- [ ] 선택: 내 전송 목록(F11), 다중 파일(F10), 브라우저 E2EE(F12)
