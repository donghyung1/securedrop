# SecureDrop Cloud Frontend (C)

PRD v1.3 웹 UI입니다. Vanilla JS(ES modules), 빌드 없음, 외부 스크립트 없음(CloudFront CSP `script-src 'self'` 대응).
`backend/frontend/`는 A의 v1.2 백엔드 테스트 화면이고, 이 폴더가 데모용 화면입니다.

## 실행

```bash
cd frontend && python3 -m http.server 5500
```

- `http://localhost:5500/?api=mock` — 서버·S3 없이 mock으로 동작 (기본값)
- `http://localhost:5500/?api=real` — `js/config.js`의 `API_BASE`에 연결
- 선택한 모드는 브라우저에 기억됩니다. 화면 오른쪽 위 배지로 현재 모드를 확인하세요.
- mock 모드에서도 QR 링크(`d.html?id=…`)를 같은 브라우저 새 탭에서 열면 다운로드 화면을 확인할 수 있습니다. 1MB 이하 파일만 실제로 저장됩니다.

배포 주소(API Gateway `/prod`, CloudFront 도메인)는 `js/config.js` 한 곳만 바꾸면 됩니다.
테스트 파일은 저장소 루트 `samples/`에 있습니다. 모두 가상 데이터입니다.

## 페이지와 파일

| 파일 | 역할 |
|---|---|
| `index.html` | 업로드 + SecureGuard 패널 + QR 결과 |
| `d.html` | 휴대폰 다운로드 페이지 (`GET /api/d/{id}`) |
| `js/config.js` | API 주소, 상한(전송 20MB·검사 4MB), 확장자, mock 전환 |
| `js/api.js` | API 클라이언트 + v1.3 계약을 흉내 내는 mock, SHA-256 |
| `js/upload.js` | 선택 → `/api/scan/file` → 해시 → `/api/transfers` → S3 직접 업로드 → `/complete` |
| `js/manage.js` | 관리 토큰 보관 (sessionStorage), `X-Manage-Token` |
| `js/qr.js` | QR, 만료 카운트다운, 다운로드 수, 링크 복사·폐기 |
| `js/download.js` | 다운로드 페이지 로직 |
| `js/assist.js` | SecureGuard 패널 (규칙 기반 문구 + 조치 버튼) |
| `js/main.js`, `js/ui.js` | 진입점, 토스트·공통 도우미 |

## 백엔드와 맞춰야 할 것

- `/api/sanitize`의 `actions` 형식: `[{"action":"MASK","targets":["RRN"]}]` (A의 v1.2 형식 유지 가정)
- 확인 플래그 누락 시 응답: 422 `VALIDATION_ERROR` 가정
- 4MB 초과 .txt/.csv: 서버도 검사할 수 없으므로 `acknowledge_unsupported`로 전송
- 업로드 허용 확장자: A의 v1.2 목록 유지 가정
- `GET /api/d/{id}` 응답에 `expires_at` 포함 가정 (없으면 카운트다운만 숨김)
- S3 버킷 CORS에 `http://localhost:5500` 포함 (PRD §10.2)

## 진행 상황

- [x] STEP 1~2: 선택 즉시 검사, 민감정보 원본 보류, 검사 불가 형식·4MB 초과 확인
- [x] 3단계 업로드(SHA-256, Presigned POST, 진행률), 관리 토큰, QR, 상태 조회, 폐기
- [x] `d.html` 휴대폰 다운로드 (만료 410·폐기 404 화면)
- [ ] 여정 3~4: SecureGuard 조치 → `/api/sanitize` → 미리보기 승인 → 처리본 전송 (Day 3)
- [ ] 자연어 요청 `/api/assist`, 플러그인 도구 (Day 4)
- [ ] 선택: 내 전송 목록(F11), 다중 파일(F10), 브라우저 E2EE(F12)
