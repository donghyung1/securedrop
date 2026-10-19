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

## Day 5 보안 점검 체크리스트

- [ ] 두 S3 버킷 퍼블릭 액세스 차단 4개 항목 켬, 웹 버킷 S3 주소 직접 접근 403
- [ ] CloudWatch Logs·DynamoDB 검색: 주민번호·전화번호 등 원문, 파일명, Presigned URL, 토큰 없음
- [ ] 저장소 전체 검색: AWS 키(`AKIA`), API Key, 확장 ID 외 비밀값 없음 (`git grep -nE "AKIA|sk-|api[_-]?key"`)
- [ ] 관리자 콘솔 화면·응답에 파일명·내용·다운로드 링크 없음
- [ ] `sd_admins`에 scrypt 해시만, 토큰 컬럼은 SHA-256만
- [ ] 확장 fail-closed 기본값 유지, 시연 전 예열(`/api/health`)
- [ ] 오탐·미탐 표(`tests/test_cases.md`)의 "실제 결과" 열을 배포 환경에서 다시 채움
