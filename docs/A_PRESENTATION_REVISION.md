# A 파트 발표 자료 수정 문구 초안

대상: `docs/SecureDrop_AWS_Backend_revised.pdf` (10쪽) + 신규 11쪽. 원본 PDF와 `A_PRESENTATION.md`는 수정하지 않는다.
표기: (1) 현재 문구 → (2) 수정 문구(슬라이드용) → (3) 근거 → (4) 발표자 멘트. 근거 없는 수치는 **[확인 필요]**.
경로 약칭: `DS` = `backend/infra/deploy_serverless.py`, `CM` = `backend/infra/configure_monitoring.py`, `MP` = `backend/infra/measure_performance.py`, `PV` = `backend/deploy/aws/performance-verification.json`, `AP` = `docs/A_PRESENTATION.md`, `AR` = `docs/A_ROLE.md`, `PRD` = `docs/SecureDrop_Cloud_PRD_v1.3_3.txt`.

---

## p2 핵심 성과

**(1) 현재**
- "3,573 로그 검사 건수 / Lambda 2,936 + Gateway 637 / 민감정보 패턴 점검"
- "약 $0.07 서버리스 관련 비용 / 10/1–10/20 추정 합계 / 프로젝트 귀속액은 아님"
- "6 핵심 AWS 서비스"

**(2) 수정**
- `3,573건` / `최근 14일 로그 패턴 검사` / `Lambda 2,936 + Gateway 637`
- `약 $0.07` / `서버리스 서비스 추정 합계 (10/1–10/20)` / `전환 직후 단기 수치 · 월 비용 아님`
- `핵심 6개 + 보조(IAM·Route 53·ACM)`

**(3) 근거**
- 14일 검사: `AP:70`, `AR:229`
- 합계 구성·기간: `AP:143`, `AR:276` (서버리스 전환·EC2 종료 10/19: `backend/deploy/aws/serverless.json` 의 `retired_at`)
- 서비스 6개는 PRD 정의: `PRD:13`. Route 53 언급: `AP:143`. IAM 역할 생성: `DS:46-64`
- 서버리스 스택이 실제로 몇 일치 비용인지는 서비스별 시작일 기록이 없다 **[확인 필요]**

**(4) 멘트** "비용은 공유 계정의 Cost Explorer 추정치이고, 서버리스 전환 직후라 월 비용으로 읽으시면 안 됩니다."

---

## p3 아키텍처

**(1) 현재** "API Gateway / REST — 스로틀 · API Key · 사용량 계획", "프런트엔드 / 진입점"

**(2) 수정**
- API Gateway 박스: `REST · 스로틀 (전 경로) · API Key (플러그인 경로만)`
- 제목 줄: `프론트엔드 / 진입점`
- 하단 한 줄 추가: `도메인: Route 53 + ACM (기존 데모 도메인)`
- 하단 CloudFront 줄 유지: `/api/admin/* 만 관리자 API 프록시`

**(3) 근거**
- API Key는 `/api/plugin/{proxy+}` 에만: `DS:338`
- 스로틀(스테이지 20/40, 공개 5/10, 로그인 1/5): `DS:415-416, 430-443`
- 사용량 계획 0.5/5, 일 2,000회: `DS:446-450`
- 도메인: `backend/deploy/aws/deployment.json` 의 `custom_domain`, `AP:114`
- 다이어그램의 실제 화살표 구성은 PDF 렌더링을 확인하지 못했다 **[확인 필요]**

**(4) 멘트** "API Key는 플러그인 경로에만 걸려 있고, 웹·확장은 로그인 없이 스로틀로만 보호됩니다."

---

## p4 설계 결정

**(1) 현재** "데이터 역할 분리 — 파일 바이트는 S3, 판단 기록은 DynamoDB, 운영 신호는 CloudWatch에만 보관."

**(2) 수정**
- 제목: `데이터 역할 분리`
- 본문: `파일은 S3, 판단 기록은 DynamoDB, 운영 신호는 CloudWatch. 원문은 로그·DB에 남기지 않음`
- 보조 줄: `검사 중에는 Lambda 메모리를 일시 경유 (최대 4MiB)`
- "대용량 업로드 분리" 카드에 추가: `검사 입력은 base64로 약 1.33배 → 4MiB 상한`

**(3) 근거**
- 검사 4MiB 상한: `backend/config.py:22`, `backend/main.py:276-282`
- 6MB·1.33배 설명: `AP:30`
- 파일명은 DynamoDB에 저장(허용): `backend/main.py:475-481`, `AR:228`
- 로그는 경로 템플릿·요청 ID 위주: `backend/main.py:225-239`

**(4) 멘트** "원문을 저장하지 않는다는 뜻이고, 4MiB 이하 검사 중에는 Lambda 메모리를 잠깐 거칩니다."

---

## p5 보안 통제

**(1) 현재**
- "최소 권한 IAM — transfers/* Put/Get/Delete · 5개 테이블 · 로그 쓰기"
- "저장 · 전송 암호화 — SSE-S3 기본 암호화, TLS 강제" (※ 현재 배포는 파일 버킷 SSE-KMS로 강화됨, 아래 수정안 참조)
- "보존 기간 — transfers/ 1일 만료 · 로그 14일 보존"
- "원문 최소화 — 비밀번호 scrypt · 관리 토큰 SHA-256 해시"

**(2) 수정 (행 단위 교체 + 2행 추가)**

| 항목 | 슬라이드 문구 |
|---|---|
| S3 비공개 | `버킷 2개 모두 Block Public Access 4개 활성` (유지) |
| 최소 권한 (Lambda 역할) | `Lambda 실행 역할: transfers/* Put/Get/Delete · 테이블 5개 · 자기 로그만` |
| 암호화 | `파일 버킷 SSE-KMS (고객 관리형 키, 자동 교체) · 웹 버킷 SSE-S3 · S3는 TLS 아니면 거부` |
| API 보호 (신규) | `WAF 웹 ACL: 관리형 규칙 3종 + IP당 5분 3,000건 제한 · CloudTrail 감사 기록(관리 이벤트 + 파일 버킷 데이터 이벤트)` |
| 링크·보존 | `공유 링크 최대 60분 · 서명 URL 5분 · 객체는 1일 내 자동 삭제` |
| 무결성 (신규) | `업로드 정책에 크기·SHA-256 고정, 완료 시 재검증` |
| 인증 (신규) | `관리자 scrypt · 5회 실패 시 15분 잠금 · 토큰은 SHA-256 해시만 저장` |
| 로그 | `로그 14일 보존` |
| 원문 최소화 → 항목명 변경 | `민감값 해시 저장` |

**(3) 근거**
- Lambda 역할 정책: `DS:202-227`
- SSE-KMS: 업로드 정책에 `aws:kms`와 키 ID 고정 `backend/storage.py:40-55`, 플러그인 `put_object` `storage.py:103-117`, 설정 `KMS_KEY_ID` `backend/config.py:17`(비면 SSE-S3, 로컬 개발용). 웹 버킷은 SSE-S3 유지(`DS:142-147`). 키 정책: Lambda 역할은 `kms:GenerateDataKey`·`kms:Decrypt`만, `kms:ViaService`·암호화 컨텍스트 제한, IAM 쪽 KMS 권한 없음. 적용: `backend/infra/configure_security.py`, 점검: `backend/infra/verify_security.py`, 테스트: `backend/tests/test_security.py`
- WAF·KMS·CloudTrail은 PRD 비목표(`PRD:80, 89`)였으나 PRD 범위를 넘어 추가 적용함. WAF `securedrop-api-acl`(REGIONAL, API Gateway `prod` 스테이지): CommonRuleSet·KnownBadInputsRuleSet·AmazonIpReputationList·IP 속도 제한 3,000건/5분(차단). 4MiB 민감 내용 파일을 받으려고 CommonRuleSet의 본문 규칙 5개(`SizeRestrictions_BODY`, `CrossSiteScripting_BODY`, `GenericLFI_BODY`, `GenericRFI_BODY`, `EC2MetaDataSSRF_BODY`)는 Count. CloudTrail `securedrop-trail`: 다중 리전, 무결성 검증, 관리 이벤트 + `transfers/` 데이터 이벤트, 전용 로그 버킷 90일 만료
- TLS Deny: 파일 버킷 `DS:176-192`, 웹 버킷 `DS:493-499`
- Presigned 업로드·다운로드 300초: `backend/config.py:27-28`. 링크 만료 300/600/3600초: `backend/main.py:270-274`
- 수명 주기 1일 + 미완료 멀티파트 1일: `DS:148-161`. 만료 후 앱이 410 반환: `backend/main.py:561-566`. 링크 만료와 객체 삭제 시점이 다름(S3에 일정 시간 남음)
- 무결성: `content-length-range`, SHA-256 체크섬 고정 `backend/storage.py:36-58`, 완료 시 재계산 `storage.py:60-86`, 불일치 시 폐기 `backend/main.py:526-532`
- 관리자 인증: scrypt N=2^17 `backend/auth.py:20-36`, 5회·15분 `backend/config.py:30-31`, 로그인 스로틀 1/5 `DS:434-435`
- 토큰 해시 저장: `backend/auth.py:10`, `backend/main.py:492`

**(4) 멘트** "권한은 Lambda 실행 역할 기준이고, 업로드는 크기와 해시를 서명 정책에 고정해 바꿔치기를 막습니다."

---

## p6 실제 배포 환경 검증

**(1) 현재** "19개 리소스 태그 적용(보안 강화 후 23개) / 185건 이벤트 데이터 확인 / 3,573건 로그 패턴 검사 / 27건 관리 토큰 해시 확인", "S3 폐기 후 객체 조회 404, 만료 · 크기 상한 거부 확인"

**(2) 수정**
- `23개` / `리소스에 Project·Owner 태그` (보안 강화 후 19개에서 23개로 증가)
- `185건` / `이벤트에 원문 속성 없음 (10/20 스냅샷)`
- `3,573건` / `최근 14일 로그 패턴 검사`
- `27건` / `관리 토큰 해시만 저장 (10/20 스냅샷)`
- 확인 항목 3번째 줄: `폐기 후 S3 객체 404 · 크기 초과 413/400 거부` (만료 거부는 서버리스 환경 증거를 보강한 뒤에만 추가 **[확인 필요]**)
- 하단 면책(유지): `표본 아님 · 규칙 기반 점검이며 부재를 절대 증명하지 않음`

**(3) 근거**
- 태그 23개 구성(최초 19개 + WAF·KMS·CloudTrail 트레일·CloudTrail 로그 버킷): `backend/infra/tag_resources.py:56-113`, `AR:217`
- 185건 속성(day, event, source, types, request_id, time, ttl): `AR:227`. 27건: `AR:224`. TTL로 시간이 지나면 줄어듦: `backend/infra/create_tables.py:55-61`
- 폐기 후 404: `AR:247`. 413·413·400: `PV`
- 만료 거부는 EC2 시절 `backend/deploy/aws/verification.json` 의 `expired_url_blocked` 만 있고 서버리스 검증 기록(`serverless-verification.json`)에는 없음
- 증거 폴더 `backend/deploy/aws/` 는 `.gitignore:11` 로 제외되어 repo에서 재현 불가

**(4) 멘트** "185건과 27건은 오늘 시점 스냅샷이고, 중요한 건 원문 속성이 없다는 점입니다."

---

## p7 성능

**(1) 현재** 표의 "콜드 스타트 3.00초 / 1.98초 통과", 각주 "4MiB 검사: 전체 2.48초, Lambda 처리 1.47초."

**(2) 수정**
- 표 행: `콜드 스타트 | 3.00초 | 1.98초* | 통과`
- 각주 추가: `*콜드 스타트는 Lambda 로그의 Init 1.87초 + 실행 0.11초 (서버측). 나머지는 클라이언트 측정`
- 측정 조건 한 줄: `사무실 PC → 서울 리전, 업로드 시간 포함, 텍스트 검사만 10회 중앙값`
- 4MiB 각주 교체: `4MiB 검사 2.48초 = 서버 1.47초 + 업로드 네트워크 약 1초 (다른 네트워크 재측정 예정)`
- 속도 문구: `20MiB ÷ 2.717초 ≈ 7.36MiB/s (전체 과정 평균)`

**(3) 근거**
- 1.98초 = Init 1.87 + 실행 0.11, 14일 25회 중앙값 Init 1.84초 / 최대 1.91초: `AR:261`
- 클라이언트 측정 방식과 `/api/health` 콜드 측정: `MP:4-5, 71`. 스크립트 자동 기록값은 0.341초(함수가 이미 warm이었던 것으로 보임): `PV`의 `cold_start` **[확인 필요: 재측정 권장]**
- 4MiB 2.476초, 서버 1.47초, 업로드 약 1초: `PV`, `AR:258, 266`, `AP:118`
- 텍스트 10회: `PV` 의 `text_scan_warm_samples`
- 속도 산식: 20 ÷ 2.717 = 7.36 (슬라이드 기존값 7.35는 2.72초 기준)

**(4) 멘트** "콜드 스타트는 서버 로그 기준이라 나머지 수치와 측정 방법이 다릅니다. 4MiB는 목표를 넘겼고 원인은 업로드 시간입니다."

---

## p8 운영 비용

**(1) 현재** "약 $0.07 서버리스 관련 서비스 비용 추정 합계, 2026.10.01 - 10.20 · AWS Cost Explorer 추정치, Route 53 약 $0.31 별도 | 공유 계정으로 프로젝트별 귀속 비용 분리 불가"

**(2) 수정**
- 제목 보조: `서버리스 서비스 추정 합계 (10/1–10/20)`
- 본문 표 추가:

| 서비스 | 추정 (USD) |
|---|---|
| CloudWatch | 0.0402 |
| S3 | 0.0199 |
| CloudFront | 0.0056 |
| API Gateway | 0.0024 |
| DynamoDB | 0.0004 |
| Secrets Manager* | 0.0001 |
| Lambda | 0 |
| **합계** | **약 0.07** |

- 보안 강화 추가분 표 (위 표 옆 또는 아래, 제목 `보안 강화 적용 후 추가 (월 예상)`):

| 서비스 | 월 예상 (USD) |
|---|---|
| WAF (웹 ACL 1 + 규칙 4 + 요청) | 약 9~10 |
| KMS (고객 관리형 키 1) | 약 1 |
| CloudTrail (첫 관리 이벤트 사본 무료, 데이터 이벤트·로그 저장) | 약 0 |
| **합계** | **약 10~11** |

- 각주 4줄:
  - `*Secrets Manager는 이 프로젝트 코드에서 사용처 없음 → 공유 계정 항목 [확인 필요]`
  - `Route 53 약 $0.31 별도 · 계정 전체 $186.27 (대부분 EC2 계열, 10/19 종료)`
  - `확정 청구액 아님 · 월 비용 환산 불가`
  - `$0.07은 WAF·KMS·CloudTrail 적용 전 실측 · 추가분은 AWS 요금 기준 데모 트래픽 월 예상치`

**(3) 근거**
- 서비스별 수치와 총액: `AP:143-144`, `AR:276`
- Secrets Manager 사용처 없음: `backend` 전체에서 `secretsmanager` 검색 결과 없음
- 서버리스 전환·EC2 종료: `backend/deploy/aws/serverless.json`, `AR:276`
- 월 예상 비용: 위 $0.07은 WAF·KMS·CloudTrail 적용 전 수치. 적용 후 추가 월 약 $10~11(KMS 약 $1, WAF 약 $9~10, CloudTrail 약 $0 + 소액 저장). 슬라이드에 추가분 표로 함께 표시

**(4) 멘트** "공유 계정이라 프로젝트만 따로 뽑을 수 없어서, 서버리스 서비스만 합친 추정치입니다. WAF·KMS·CloudTrail을 추가한 뒤에는 월 10~11달러 정도가 더 드는데, 대부분 WAF 고정비입니다."

---

## p9 한계 (표 보강)

**(1) 현재** 4행: PRD와 차이 / 알람 기준 / 성능 미달 / 검사 용량

**(2) 수정 (기존 4행 유지 + 아래 5행 추가)**

| 구분 | 슬라이드 문구 |
|---|---|
| PRD와 차이 | `관리자 API만 CloudFront 프록시 (교차 출처 인증 실패로 변경) · 기존 데모 도메인 사용` |
| CORS | `운영 허용 origin에 localhost·127.0.0.1 포함 (개발용, 배포 시 제거 예정)` |
| IAM 예외 | `API Gateway 로그 역할은 로그 권한이 Resource * (계정 단위 로깅 역할)` |
| 공개 API 보호 | `웹·확장 API는 API Key 없이 스로틀만 · 스로틀은 전역 한도(IP별 아님)` |
| 관리자 보호 | `관리자 로그인 1/5 전역 스로틀, IP 제한은 선택 항목으로 미적용` |
| PRD 비목표 | `SES/SNS 알림은 범위 밖 (향후 계획). WAF·KMS(CMK)·CloudTrail은 PRD 비목표였으나 추가 적용` |
| WAF 예외 | `WAF 본문 검사 규칙 5개는 Count: 4MiB 민감 내용 파일을 정상 수용하기 위함, 속도 제한(IP당 5분 3,000건)과 스로틀로 보완` |

**(3) 근거**
- 프록시 변경·이유: `AP:114`, `backend/infra/admin_origin.py:6-56`, PRD 8.1: `PRD:304-305`
- localhost: `DS:80-86, 240`, `backend/config.py:32-37`, 한계 인정 `AP:117`, `AR:214`
- 로그 역할 `*`: `DS:374-396`(392행), 인정 `AP:122`, `AR:216`
- API Key는 플러그인 경로만: `DS:338`. 스로틀은 스테이지·메서드 단위: `DS:415-416, 430-443`. API Gateway 스로틀이 클라이언트 IP별이 아니라는 설명은 AWS 일반 동작이며 repo 문서에는 없음 **[확인 필요: 표현 수위]**
- 관리자 IP 제한은 PRD에서 "선택": `PRD:162, 272`. 로그인 잠금 구현: `backend/main.py:640-700`. 로그인 스로틀로 정상 관리자 접근이 막힐 수 있는지는 미검증 **[확인 필요]**
- 비목표: `PRD:80, 87-95`, `AP:129-139`. 이 중 WAF·KMS·CloudTrail은 추가 적용(PRD 범위 밖 구현), SES/SNS·CDK·Budgets는 미구현. WAF Count 예외·속도 제한 근거는 `AP`의 "PRD 비목표였으나 추가 적용한 것" 절
- 알람 1분·1건·알림 없음: `CM:36-41`

**(4) 멘트** "질문이 나오기 전에 먼저 말씀드립니다. 이 표의 항목은 모두 문서에 기록하고 수용 여부를 팀이 결정한 것입니다."

---

## p10 데모

**(1) 현재** 01 확장 차단 → 전송 이벤트 / 02 관리자 콘솔 → BlockedCount·TransferCount / 03 S3 권한·수명 주기 / 하단 "발표 전: /api/health 예열 + AWS 콘솔 로그인 + SecureDrop-Demo 준비"

**(2) 수정**
- 01 `확장 차단 → 파일 전송 (B 파트 시연과 연결)`
- 02 `관리자 콘솔 → CloudWatch 대시보드 (BlockedCount·TransferCount)`
- 03 `S3 권한: 퍼블릭 차단 4개`
- 04 `S3 수명 주기: transfers/ 1일 만료`
- 05 (선택) `알람 2개 상태 OK (알림 없음)`
- 하단 `발표 전 체크: /api/health 예열 · AWS 콘솔 로그인 · SecureDrop-Demo 탭`
- **백업 항목 박스 추가**: `백업: 10/19 확장 차단 PHONE·RRN 각 2건 지표 · 스크린샷 · 영상`

**(3) 근거**
- 시연 순서·알람 선택 항목: `AP:41-52`
- 지표 수집 지연 대비 기존 값: `AP:54`
- 대시보드 링크: `DS:504-507, 524`. 지표 이름: `CM:62-76`
- 10/19 지표 값: `AR:196`
- 스크린샷·영상 백업 유무: `docs/REMAINING_TASKS.md` 에 "리허설·영상 백업" 확인 필요로 기록, 실제 파일은 확인되지 않음 **[확인 필요]**

**(4) 멘트** "지표 반영이 늦으면 어제 쌓인 값으로 대신 보여드리겠습니다."

---

## p11 (신규) 마무리

**(2) 슬라이드 문구**

제목: `정리 · 향후 계획`

요약 3줄
- `서버리스로 서버 관리 없이 구성: Lambda · API Gateway · S3 · DynamoDB · CloudFront · CloudWatch`
- `S3 비공개·최소 권한·해시 저장을 배포 환경에서 직접 조회해 확인`
- `성능 5개 중 4개 통과, 4MiB 검사만 목표 초과`

정리 계획
- `발표 직후: 플러그인 API Key 삭제`
- `발표 후: teardown.py로 전체 정리 (20개 대상, 건너뛰는 항목 1)`
- `CloudFront·도메인 유지 여부는 팀 결정, Route 53·ACM은 수동 정리`

향후 계획
- `SES/SNS 알림 · CDK · Budgets` (WAF·KMS·CloudTrail은 추가 적용 완료)
- `대용량 비동기 검사 (SQS·Step Functions)`
- `알람 임계값을 PRD 값(5분·5건)으로 조정`

예상 Q&A (슬라이드 하단 또는 부록)
1. **왜 EC2에서 서버리스로 옮겼나?** 서버 관리가 없고 쓴 만큼만 비용이 든다. 서버리스 서비스 추정 비용 약 $0.07 (p8 조건 참고).
2. **왜 REST API인가?** 플러그인 API Key·사용량 계획이 HTTP API에는 없다.
3. **20MB를 왜 S3로 바로 올리나?** Lambda 동기 6MB 한도. 크기는 서명 정책의 content-length-range로 S3가 강제, 초과 시 400 EntityTooLarge 확인.
4. **4MiB가 느리지 않나?** 전체 2.48초 중 서버 1.47초, 약 1초는 업로드 네트워크. 목표 초과는 인정.
5. **파일이 S3에 계속 남나?** 링크는 최대 60분, 객체는 수명 주기로 1일 내 자동 삭제, 버킷은 퍼블릭 차단.
6. **WAF·KMS는 왜/어떻게 썼나?** PRD 비목표였지만 추가 적용. WAF는 관리형 규칙 + IP당 속도 제한이고, 4MiB 민감 내용 파일을 받아야 해서 본문 검사 규칙 5개는 Count로 둔 트레이드오프가 있다. KMS는 고객 관리형 키이며 S3 읽기 권한만으로는 KMS 권한 없이 복호화할 수 없다.

**(3) 근거**
- 정리 계획·20개 대상·건너뛰기 1개(보안 강화 후 재실행; 이전 계획은 19개): `AP:146-157`, `AR:271, 279`, `backend/infra/teardown.py`
- 향후 계획·비목표: `AP:127-139`, `PRD:940`(CloudTrail → CDK → Budgets 순서). CloudTrail은 이미 추가 적용했으므로 남은 것은 CDK → Budgets, SES/SNS 알림
- 성능 4/5 통과: `PV`(비교 행 5개 중 4MiB만 `passed: false`). 통과로 센 콜드 스타트의 측정 방식 차이는 p7 각주 참조
- Q&A 원문: `AP:159-173`
- 알람 값 조정: `AP:116`

**(4) 멘트** "한계는 모두 기록되어 있고, 오늘 배포 환경은 발표 후 정리됩니다. 질문 받겠습니다."

---

## 낮음 항목 체크리스트

- [ ] p1 부제 "아키텍처 · 보안 검증 · 성능 · 운영 · 비용" 정리: "운영" 슬라이드가 없으므로 부제를 `아키텍처 · 보안 검증 · 성능 · 비용 · 한계`로 바꾸거나 p9~p11을 "운영"으로 묶는다
- [ ] "프런트엔드" → "프론트엔드"로 통일 (repo 문서 표기: `frontend/README.md`)
- [ ] "MB/MiB" 통일: 슬라이드는 MiB 유지(코드가 1024*1024 기준 `backend/config.py:21-22`). PRD의 "20MB/4MB"는 별도 PRD 정리 과제
- [ ] 슬라이드 제목 톤 통일 (질문형/평서형 혼재): 예 `설계 결정`, `보안 통제`, `배포 환경 검증`, `성능`, `운영 비용`, `한계`, `데모`
- [ ] p5 → p6 연결 문구 추가: `설정(p5) → 실제 배포 조회(p6)`
- [ ] p2·p6의 3,573건 중복 표기 유지 시 같은 문구("최근 14일") 사용
- [ ] PDF 텍스트에서 마침표 앞 공백("사용 .") 확인: 글꼴 문제인지 실제 오탈자인지 렌더링으로 확인 **[확인 필요]**
- [ ] p3 다이어그램 화살표(관리자만 CloudFront 경유) 렌더링 확인 **[확인 필요]**
- [ ] 증거 파일(`backend/deploy/aws/*.json`)이 `.gitignore:11` 로 미추적: 발표용 발췌본을 별도 보관할지 결정
- [ ] 진단 코드(`backend/handler.py:15-42`, `backend/main.py:586-638`)를 prod에 남길지 결정 (민감값은 기록하지 않음)
- [ ] 콜드 스타트 재측정: 함수 15분 유휴 후 `MP` 실행, `PV` 의 `cold_start` 0.341초와 문서 1.98초 불일치 해소
