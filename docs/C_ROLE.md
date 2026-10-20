# C 담당 작업 — 프론트엔드 / 연동 (PRD v1.3 기준)

> 근거: PRD v1.3 12장(마일스톤)·6장(F1·F4·F5·F7·F8·F10~F12)·13장(완료 기준), `docs/REMAINING_TASKS.md` C 항목. 기준일 2026-10-20.

## 역할 한 줄

일반 사용자 화면(`frontend/`: 업로드·SecureGuard·정제·QR·휴대폰 다운로드), 서버 연동, AI 플러그인(`plugin/` MCP 서버), 데모 리허설을 맡는다.

## Day별 상태

| Day | PRD상 C 업무 | 상태 | 비고 |
|---|---|---|---|
| 1 | 업로드·결과·QR 화면 골격, mock으로 STEP 1~2 | ✅ 완료 | PR #3 |
| 2 | S3 직접 업로드 연동, `d.html`, CloudFront 배포 | ✅ 완료 | 업로드·`d.html`은 PR #3, CloudFront 배포는 A의 `deploy_serverless.py`가 `frontend/`를 올림 |
| 3 | SecureGuard 패널(버튼), 관리 토큰 보관·폐기, QR 마무리 | ✅ 완료 | 정제 흐름 PR #7 |
| 4 | 플러그인 도구 2개·승인 단계, `/api/assist` 자연어 해석 | ✅ 완료 | 자연어 PR #8, MCP PR #9 |
| 5 | 리허설, 발표 자료, 데모 영상 백업 | ☐ 남음 | 아래 "남은 일" |

## 배포 환경 검증 (2026-10-20, https://securedrop.ldh.mzsec.cloud)

`frontend/tests/verify_deployed.py --url <웹 주소>`로 실제 CloudFront·API Gateway·S3에서 브라우저 검증했다. 결과 JSON은 git 제외 폴더 `backend/deploy/aws/c-verification.json`에 저장된다. 테스트 전송은 시나리오마다 폐기해 S3 객체를 지운다.

| 항목 | 확인 내용 | 결과 |
|---|---|---|
| 정제 → 재검사 → 미리보기 승인 → 전송 (F7·F8) | 민감 CSV → 이메일만 제외하고 마스킹 → 남은 이메일 때문에 전송 버튼 잠김 → "남은 항목도 정제" → 상위 20행 미리보기(`900101-1******`, `010-****-5678`, `[REDACTED]`) → 승인 → QR → 수신 파일 `고객정보_정제본.csv`에 원래 값 없음 | ✅ |
| 삭제 조치 | AWS 키 삭제 → 수신 파일에 키 없음 | ✅ |
| SecureGuard 자연어 (F8) | MASK("주민등록번호만 가려줘" → 주민번호만 마스킹), 이어서 DELETE("이메일도 지워줘" → 기존 선택에 추가), CLARIFY("이메일 말고 다 가려줘" → 버튼으로 되묻기), CANCEL("취소할래" → 확인 버튼만). **확정 전까지 `/api/sanitize` 호출 0건** | ✅ |
| 브라우저 암호화 (F12, B·C 공동) | 링크에 `#k=` 포함, S3 객체가 평문과 다르고 28바이트 큼(IV+태그), 키로 복호화한 파일 = 원본, 틀린 키는 "복호화에 실패" 안내만 하고 저장 안 함 | ✅ |
| 폐기·모바일 | 폐기 후 수신 화면 "폐기된 링크", 390px 폭 가로 스크롤 없음, 스크립트 오류 0건 | ✅ |
| MCP 플러그인 (F4) | Claude Code에 local 범위로 등록 → `scan_text` 실제 호출(휴대전화번호·AWS Key 탐지). SDK 클라이언트로 배포 API에서 `create_transfer` 승인(링크+QR)·거절(미전송)·민감 파일 거부·허용 폴더 밖 거부 확인 | ✅ |

이전 검증: 업로드 화면의 실패·재시도 처리(410 단계 구분, `/complete` 재시도, 해시 불일치 시 토큰 정리)는 moto 기반 로컬 백엔드에서 mock과 실제 서버 결과가 같음을 확인했다.

## 이번 변경 (확장 ZIP, PRD 8.1)

- `backend/infra/extension_package.py`: `extension/`을 ZIP으로 묶고 `config.js`만 배포 주소(`apiBase`, `webBase`)로 바꾼다. 같은 소스면 같은 바이트가 나오도록 타임스탬프를 고정했다. 키는 넣지 않는다.
- `deploy_serverless.py`의 프론트 배포 단계가 `download/securedrop-extension.zip`을 함께 올린다(`--frontend-only` 포함).
- 업로드 화면 하단에 설치 파일 링크와 로드 방법 안내.
- 테스트: `backend/tests/test_extension_package.py` 3개(파일 구성, 배포 주소 반영, 재현성·키 없음).
- **A 재배포 후 확인 필요**: `https://<웹>/download/securedrop-extension.zip` 다운로드 → 압축 해제 → `chrome://extensions`에서 로드 → 팝업의 주소가 배포 주소인지.

## 남은 일

| 구분 | 할 일 | 필요한 것 |
|---|---|---|
| 확인 필요 | 실물 휴대폰으로 QR 스캔·저장·폐기 화면 (F1·F5, 13장). 암호화 전송도 휴대폰에서 복호화 | 휴대폰 |
| 확인 필요 | 확장 ZIP 배포 확인 (위) | A 재배포 |
| 확인 필요 | Claude Code에서 `create_transfer` 승인 창·확인 창 시연 리허설 | — |
| Day 5 | 전체 리허설: 확장 차단 → "안전하게 전송하기" → 정제 → 승인 → QR → 휴대폰 저장 → 관리자 강제 폐기. 시연 5분 전 `/api/health` 예열 | 팀 |
| Day 5 | 데모 영상 백업, 발표 자료 C 파트 | — |
| 선택 | 다중 파일(F10), 내 전송 목록(F11) | PRD 지연 시 제외 순서상 후순위 |

## 발표 시 밝힐 한계

- 정규식으로 찾을 수 있는 유형만 처리한다. 문맥상 개인정보는 미리보기에서 사람이 확인해야 한다.
- 검사 단계에서는 서버가 평문을 본다. 암호화(F12)는 저장·전송 구간 보호다.
- 4MB를 넘는 .txt/.csv는 검사할 수 없어 전송을 막는다(서버 계약상 검사 참조 필수).
- 처리본은 BOM 없는 UTF-8이라 PC 엑셀에서 한글이 깨질 수 있다(휴대폰·메모장은 정상).
- 브라우저 암호화·해시 계산은 HTTPS 또는 localhost에서만 동작한다(Web Crypto 제한). IP 주소 http 접속에서는 안내 문구를 띄운다.
