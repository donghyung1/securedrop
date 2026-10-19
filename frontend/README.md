# SecureDrop Cloud Frontend (C)

PRD v1.2의 웹 UI입니다. 업로드 + SecureGuard 패널 / QR 결과 / 이력 화면. Vanilla JS(ES modules), 빌드 없음.
`backend/frontend/`는 A의 백엔드 테스트용 화면이고, 이 폴더가 데모용 화면입니다.

## 실행

```bash
cd frontend && python3 -m http.server 5500
```

- `http://localhost:5500/?api=mock` — 서버 없이 mock 응답으로 동작 (기본값)
- `http://localhost:5500/?api=real` — `http://localhost:8000` 백엔드에 연결
- 선택한 모드는 브라우저에 기억됩니다. 화면 오른쪽 위 배지로 현재 모드를 확인하세요.
- real 모드는 서버 `ALLOWED_ORIGINS`에 `http://localhost:5500`이 있어야 합니다 (기본값에 포함).

테스트 파일은 저장소 루트 `samples/`에 있습니다. 모두 가상 데이터입니다.

## 구조

| 파일 | 역할 |
|---|---|
| `js/api.js` | API 클라이언트, mock, 오류 코드·탐지 유형 라벨 |
| `js/upload.js` | 파일 선택 → `/api/scan/file` 즉시 검사 → 전송 |
| `js/assist.js` | SecureGuard 패널 (규칙 기반 문구 + 조치 버튼) |
| `js/qr.js` | QR, 만료 카운트다운, 링크 복사·폐기 |
| `js/logs.js` | 이력 조회 |
| `js/main.js`, `js/ui.js` | 탭 전환, 토스트 |

## 진행 상황

- [x] STEP 1~2: 파일 선택 즉시 검사, 민감정보 발견 시 원본 전송 보류
- [x] 검사 미지원 형식 → 확인 후 `confirm_unscanned`, 위험 확인 후 `confirm_sensitive` 전송
- [x] 5MB 초과 .txt/.csv → 전송 시 서버 검사, 409 `SENSITIVE_DATA`면 보류 화면으로 전환
- [x] QR 표시, 링크 복사(`download_url`), 폐기, 이력 조회
- [ ] STEP 3~5: SecureGuard 조치 → `/api/sanitize` → 재검사 → 미리보기 승인 (Day 3)
- [ ] 자연어 요청 `/api/assist` (Day 4)
