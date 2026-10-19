# B 담당 작업 — 보안 / 확장프로그램 (PRD v1.3 기준)

> 근거: `SecureDrop_Cloud_PRD_v1.3` 12장(마일스톤)·6장(F2·F3·F6·F7·F13)·10.6(인증 테스트). 현재 `upstream/master`는 A의 v1.3 백엔드(8d4d1b3)까지 반영되어 있다.

## 역할 한 줄

탐지 규칙(`detector.py`)과 정제(`sanitizer.py`)의 정확도, 크롬 확장, 관리자 콘솔 화면, 보안·인증 테스트를 맡는다. A는 AWS·API 계약, C는 일반 사용자 화면·연동을 맡는다.

## Day별 상태

| Day | PRD상 B 업무 | 상태 | 비고 |
|---|---|---|---|
| 1 | `detector.py` 규칙·체크섬, `/api/scan`·`/api/scan/file` 응답 형식 | ✅ 완료 | 6종 탐지, RRN 체크섬, 카드 Luhn. 테스트 케이스 `tests/test_cases.md` 1~3장 |
| 2 | 확장 뼈대 + `/api/scan` 연동, `sanitizer.py` | ✅ 완료 | v1.2 시점 구현(PR #1 병합) |
| 3 | 정제 재검사, 확장 차단 팝업 + `/api/ext/logs` | 🔶 이번 브랜치에서 v1.3로 이전 | 아래 "변경점" |
| 4 | 오탐·미탐 테스트, `admin.html`, 인증 테스트(10.6), 확장 안정화, (여유 시) `crypto.js` | 🔶 진행 | `admin.html` 작성, 인증 테스트는 A가 대부분 작성 + B 보강 |
| 5 | 보안 점검(퍼블릭 차단·로그 원문 없음·키 미포함), 데모 테스트 케이스 | ☐ 남음 | 아래 체크리스트 |

## 이번 브랜치(`feat/b-v13`) 변경점

1. **확장 → v1.3 계약**: `/api/logs` → `/api/ext/logs`. 기존 확장은 본문에 `source`를 같이 보냈는데 v1.3 서버는 알 수 없는 필드를 422로 거부하므로 **차단 로그가 전부 실패했을 것**이다. `source`와 `X-Source` 헤더를 제거했다.
2. **주소 분리**: API Gateway 주소(`apiBase`, `/prod` 포함)와 CloudFront 주소(`webBase`, "안전하게 전송하기" 링크)를 `extension/config.js` 한 곳에서 바꾸고, 팝업에서도 덮어쓸 수 있다. `manifest.json`에 `https://*.execute-api.ap-northeast-2.amazonaws.com/*` 권한 추가, 버전 1.3.0.
3. **관리자 콘솔**: `backend/frontend/admin.html·js·css`. 로그인(세션은 `sessionStorage`, 쿠키 없음) → 현황 집계 / 최근 이벤트 / 활성 전송 / 강제 폐기. 모든 값을 `textContent`로만 그리고 인라인 스크립트가 없어 CSP(`script-src 'self'`)와 호환된다. 파일명·링크는 표시하지 않는다.
4. **테스트 보강**: 확장 로그 계약(엄격 스키마, 유형만 저장), 모든 `/api/admin/*`가 세션 없이 401인지 확인. `tests/test_cases.md`에 인증(4장)·확장 시연(5장) 표 추가.

## 확인 못 한 것 (정직하게)

- 백엔드 테스트는 WSL(Ubuntu)의 uv로 실행해 56개 통과, ruff 통과. 실행: `wsl` → `cd backend && SCAN_FILE_MAX_MB=4 uv run pytest -q` (로컬 `.env`에 v1.2 값 `SCAN_FILE_MAX_MB=5`가 있으면 4로 고칠 것).
- 확장·`admin.html`은 실제 브라우저에서 동작을 확인하지 못했다. 배포 주소가 나오면 `tests/test_cases.md` 5장 순서로 점검한다.

## A·C와 맞춰야 할 것

- **A**: Lambda `ALLOWED_ORIGINS`에 `chrome-extension://<확장 ID>`와 관리자 콘솔이 올라가는 CloudFront 도메인. 게이트웨이 오류 응답(401/403/429)의 CORS 헤더(확장·콘솔이 오류 문구를 읽으려면 필요). 관리자 로그인 API Gateway 스로틀 1 rps.
- **C**: 배포 시 `admin.html·js·css`와 `config.js`(`apiBase`, 선택 `cloudwatchUrl`)를 같은 웹 버킷에 포함. 확장 zip(`/download/securedrop-extension.zip`) 업로드. 현재 `admin.js`는 `window.SD_CONFIG.apiBase`를 읽는다.
- **디렉터리**: PRD는 `frontend/`이지만 A가 `backend/frontend/`에 두었다. 위치가 정해지면 `admin.*`도 같이 옮긴다.

## Day 5 보안 점검 결과 (2026-10-19, 배포 환경 외부 관점)

AWS 자격 증명 없이 공개 주소로 확인할 수 있는 항목만 점검했다. CloudWatch·DynamoDB 내부 확인은 AWS 권한이 있는 사람이 해야 한다.

| 항목 | 결과 | 근거 |
|---|---|---|
| 파일 버킷 익명 접근 | ✅ 차단 | 목록·객체 GET·HTTP(비TLS) 모두 403 |
| 웹 버킷 S3 주소 직접 접근 | ✅ 차단 | `securedrop-web-<acct>-ap-northeast-2` 직접 GET 403 (CloudFront로만 제공) |
| CloudFront 보안 헤더 | ✅ | HSTS, nosniff, X-Frame-Options DENY, Referrer-Policy no-referrer, CSP(`script-src 'self'`, `connect-src`는 API·파일 버킷만), HTTP→HTTPS 301 |
| 관리자 API 인증 | ✅ | 토큰 없이/가짜 토큰으로 `/api/admin/*` 401 |
| 플러그인 API Key | ✅ | 키 없이 `/api/plugin/scan` 403 |
| 관리 토큰 | ✅ | 임의 전송 ID 조회 404(존재 여부 외 정보 없음) |
| CORS | ✅ | 허용되지 않은 Origin에는 `Access-Control-Allow-Origin`을 붙이지 않음 |
| 오류 응답 | ✅ | 입력 오류가 값 원문을 되돌려주지 않음, 로그인 실패 문구 동일 |
| 저장소 비밀값 | ✅ | 추적 파일·git 이력에 실제 키·토큰 없음. `AKIAIOSFODNN7EXAMPLE`은 AWS 공식 예시 값(샘플 파일). `.env`·`aws/` 산출물 미추적 |
| 로그·DynamoDB에 원문 없음 | ✅ (A 확인 2026-10-20) | 로그 3,573건과 DynamoDB 전 항목 패턴 검사 결과 오탐뿐. [A_ROLE.md](A_ROLE.md) 참고 |
| `sd_admins`는 scrypt 해시, 토큰은 SHA-256만 | ✅ (A 확인 2026-10-20) | `scrypt$131072$8$1$…`, `manage_hash`는 hex64. 관리자 세션은 점검 시 0건이라 코드·테스트로 확인 |
| 두 버킷 Block Public Access 4개 설정 | ✅ (A 확인 2026-10-20) | 두 버킷 모두 4개 true, `IsPublic=false` |

### 남은 점검
- [ ] 위 "미확인" 3건 (AWS 콘솔 또는 개발자 프로필로)
- [ ] 시연 전 예열(`/api/health`)
- [ ] 오탐·미탐 표(`tests/test_cases.md` 1~3장)의 "실제 결과" 열을 배포 환경에서 다시 채움

### 관리자 콘솔 시연 점검 (배포 환경, 2026-10-19, 본인 확인)
- [x] 로그인 → 현황 집계·최근 이벤트 표시, 파일명·링크 미표시
- [x] 확장 차단 이벤트(`BLOCK / RRN / extension`) 콘솔에 반영
- [x] 활성 전송 표에 ACTIVE 전송 표시(ID 앞 8자, 크기, 생성·만료, 다운로드 수) → "강제 폐기" 동작
- [x] 로그아웃 후 새로고침 시 로그인 화면으로 복귀
