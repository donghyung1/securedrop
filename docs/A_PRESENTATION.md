# A 파트 발표 요약 — AWS / 백엔드

## 한 줄 소개

**서버리스 구조**로 전송·검사 백엔드를 AWS에 올리고, 보안 설정·모니터링·비용·정리까지 직접 조회해서 점검했습니다.

## 아키텍처 한눈에

```
브라우저 / 크롬 확장 / Claude 플러그인
        |
        v
CloudFront (HTTPS, 보안 헤더)
   |-- 웹 화면 --------> S3 웹 버킷 (CloudFront OAC로만 읽기)
   |-- /api/admin/* ---> API Gateway /prod  (관리자 API만 프록시)

브라우저 / 확장 / 플러그인 --> API Gateway (REST, 스로틀·API Key)
                                  |
                                  v
                          Lambda (python3.12, FastAPI)
                            |-- DynamoDB 5개 테이블
                            |-- S3 파일 버킷 (transfers/*)
                            |-- CloudWatch 로그·지표

20MiB 파일은 Lambda를 거치지 않고 브라우저가 S3로 직접 업로드(presigned POST)
```

- 파일 검사 입력은 4MiB까지(Lambda 동기 호출 한도 때문). 20MiB 전송은 S3로 직접 보냅니다.
- Lambda에는 AWS 키·플러그인 API Key 같은 비밀값을 넣지 않습니다.

### 왜 이 구조인가 (PRD v1.3 개요·10.10)

PRD는 AWS 핵심 서비스 6개(S3, Lambda, API Gateway, DynamoDB, CloudFront, CloudWatch)만 쓰고 서울 리전에 둡니다. CloudFront만 글로벌입니다.

- **서버리스**: EC2 한 대는 서버 패치·HTTPS 인증서·프로세스 관리가 팀 시간을 잡아먹습니다. Lambda + API Gateway + CloudFront는 HTTPS가 기본이고 서버 관리가 없습니다.
- **20MB는 S3 직접**: Lambda 요청·응답 6MB 한도 때문에 Lambda는 "발급·검증·기록"만 합니다. 검사 입력은 base64로 약 1.33배가 되어 4MB로 제한했습니다.
- **REST API**: HTTP API가 더 싸지만 플러그인용 API Key·사용량 계획을 지원하지 않습니다.
- **Lambda는 상태가 없음**: 검사·정제 세션은 DynamoDB(TTL 10분)에 해시·유형·개수만 둡니다.
- **원칙 4**: 파일 바이트는 S3에만, 판단 기록은 DynamoDB에만, 운영 신호는 CloudWatch에만 둡니다. 어디에도 원문을 남기지 않습니다.

## 시연 순서 (약 3분)

시연 5분 전에 `<apiBase>/api/health`를 한 번 열어 예열합니다(콜드 스타트 방지). 대시보드를 열려면 AWS 콘솔에 먼저 로그인해 둡니다.

| 순서     | 할 일                                                          | 보여줄 것                                                           |
| -------- | -------------------------------------------------------------- | ------------------------------------------------------------------- |
| 1        | B 파트 시연(확장 차단 → 전송)을 한 번 진행                     | 차단·전송이 한 번씩 발생                                            |
| 2        | 관리자 콘솔(`/admin.html`)에서 CloudWatch 링크 클릭            | `config.js`의 `cloudwatchUrl`이 `SecureDrop-Demo` 대시보드로 연결됨 |
| 3        | 대시보드에서 `BlockedCount`(유형·출처별), `TransferCount` 확인 | 방금 한 차단·전송만큼 지표가 올라감                                 |
| 4        | S3 콘솔에서 파일 버킷 > 권한                                   | Block Public Access 4개 모두 켜짐, 퍼블릭 아님                      |
| 5        | 파일 버킷 > 관리 > 수명 주기 규칙                              | `transfers/` 1일 후 만료, 미완료 멀티파트도 1일                     |
| 6 (선택) | 알람 2개(`SecureDrop-LambdaErrors`, `SecureDrop-Api5XX`)       | 상태 OK                                                             |

지표는 수집에 시간이 걸릴 수 있어, 점검 때 이미 쌓인 값(10-19 확장 차단 PHONE·RRN 각 2건)을 같이 보여줄 수 있습니다.

## 설명 멘트 (핵심 5가지)

1. **서버리스**: CloudFront → API Gateway → Lambda → DynamoDB·S3 구조입니다. 켜 둔 서버가 없어서 쓰지 않을 때 비용이 거의 들지 않습니다.
2. **S3는 모두 비공개**: 두 버킷 모두 Block Public Access 4개가 켜져 있고, TLS 강제를 적용했습니다. 파일 버킷은 고객 관리형 KMS 키(SSE-KMS), 웹 버킷은 SSE-S3로 암호화합니다. 파일은 1일 뒤 자동 삭제됩니다.
3. **최소 권한**: Lambda 역할은 S3 `transfers/*`의 Put/Get/Delete, DynamoDB 5개 테이블, 자기 로그 그룹 쓰기만 가집니다. Scan 권한과 관리형 정책은 없습니다.
4. **보이는 운영**: 로그 14일 보존, 대시보드 1개, 알람 2개. 관리자 콘솔에서 대시보드로 바로 이동합니다.
5. **남기지 않음**: 이벤트에는 유형·시간만, 로그에는 원문·서명 URL·토큰이 없습니다. 비밀번호는 scrypt, 관리 토큰은 SHA-256 해시만 저장합니다.

## 검증한 것 (숫자로 말하기)

2026-10-20, 서울 리전 배포 환경을 직접 조회했습니다.

- 태그: `infra/tag_resources.py`로 **19개 리소스**에 `Project=securedrop`, `Owner` 적용 (S3 2, DynamoDB 5, Lambda, API Gateway 4, 로그 그룹 2, IAM 역할 2, 알람 2, CloudFront)
- 저장 데이터: `sd_events` 185건은 날짜·시간·유형 코드 등만 있음. `sd_transfers` 관리 토큰은 64자리 hex 해시(27건), 원문 속성 없음
- 로그 검사: 14일치 Lambda 로그 **2,936건**과 게이트웨이 로그 **637건**을 전화·주민·카드·이메일·서명 URL·토큰·비밀번호 패턴으로 검사. 걸린 것은 타임스탬프·UUID·단계명 오탐뿐
- API Gateway: stage 20/40 rps, 로그인 1/5, 공개 경로 5/10, 플러그인 사용량 계획 0.5/5 rps에 일 2,000회
- Lambda: 1024MB, 25초, 예약 동시성 10, Function URL 없음
- API 정의 내보내기: `backend/infra/apigw-export.json` (계정·API ID 치환, API Key 값 없음)

### PRD 요구 → 구현·검증

| PRD 요구                                          | 구현·검증 결과                                                                |
| ------------------------------------------------- | ----------------------------------------------------------------------------- |
| F13·10.8 로그 14일, 대시보드 1개, 알람 2개        | 로그 그룹 2개 14일, `SecureDrop-Demo`, 알람 2개 OK(알림 action 없음, PRD대로). 임계값은 1분·1건으로 PRD와 다름(한계 표) |
| 13장 대시보드에서 차단·전송 건수 상승             | `BlockedCount` 각 2, `TransferCount` +1 확인                                  |
| 10.2·7장 S3 퍼블릭 차단·SSE-S3·TLS·버전 관리 끔   | 두 버킷 모두 충족(파일 버킷은 SSE-KMS로 강화, 아래 "PRD 비목표였으나 추가 적용"), `transfers/` 1일 만료 |
| (추가) WAF·KMS CMK·CloudTrail                     | 실계정 적용, `verify_security.py`로 종단 점검 통과, 단위 테스트 78개 통과      |
| 10.9 IAM 최소 권한                                | S3 `transfers/*` 3개 동작, 테이블 5개, 로그 쓰기만. Scan·관리형 정책 없음     |
| 10.1·10.9 태그 `Project`, `Owner`                 | 19개 리소스 적용(대시보드는 태그 미지원)                                      |
| 10.3·10.6·원칙 4 해시만 저장, 로그에 원문 없음    | 이벤트 185건·토큰 해시·로그 3,573건 검사 통과(위 숫자)                        |
| 7장·13장 성능, 크기 상한 거부                     | 아래 표. 413·400 거부 확인                                                    |
| 10.5 스로틀, 10.4 예약 동시성 10                  | stage 20/40, 로그인 1/5, 공개 5/10, 플러그인 0.5/5·일 2,000회, 동시성 10      |
| 10.11 API Gateway 내보내기 커밋                   | `apigw-export.json` (API Key 값 없음)                                         |
| 10.13·Day 5 비용 실제 수치, 정리 스크립트         | 약 $0.07, `teardown.py` 계획 실행(19개 대상)                                  |
| 10.13 API Key는 발표 직후 삭제                    | `--api-key-only` 준비                                                         |
| 10.4·만료 Presigned 5분, 공유 링크 최대 60분      | 업로드·다운로드 Presigned 300초, 공유 링크 기본 600초·최대 3600초 ✅          |
| 10.3 DynamoDB TTL·GSI                             | 5개 테이블 온디맨드, TTL `ttl` 4개 활성(sd_admins는 TTL 없음), `day-created-index` GSI 확인 ✅ |
| 20MB 업로드가 Lambda 미경유                       | 로그로 확인: 발급·완료·조회·폐기 요청만 있고 업로드 본문 요청 없음 ✅          |
| 폐기 시 S3 객체 삭제                              | 폐기 후 head-object 404, 다운로드 링크 404 ✅                                 |

### 성능 (목표 vs 측정, 2026-10-20 08:31)

목표는 PRD 7장·13장 값과 같습니다. 시간은 사무실 PC에서 서울 리전까지 재었고 업로드 시간을 포함합니다. 20MiB 항목은 발급 + S3 POST + 완료까지의 시간이며, PRD의 "QR 표시까지"와는 측정 범위가 조금 다릅니다.

| 항목                   | 목표 | 측정                                  | 판정                           |
| ---------------------- | ---- | ------------------------------------- | ------------------------------ |
| 웜 텍스트 검사         | 1초  | 중앙값 0.12초 (10회, 최대 0.125)      | 통과                           |
| 4MiB 파일 검사         | 2초  | 전체 2.48초 / Lambda 1.47초           | 전체 시간 초과, 서버 기준 통과 |
| 1MiB CSV 정제          | 3초  | 2.12초 (Lambda 1.63초)                | 통과                           |
| 20MiB 직접 전송        | 10초 | 2.72초                                | 통과                           |
| 콜드 스타트            | 3초  | 1.98초 (14일 25회 중앙값 Init 1.84초) | 통과                           |
| 4MiB 초과 검사         | 거부 | 413                                   | 통과                           |
| 20MiB 초과 전송 발급   | 거부 | 413                                   | 통과                           |
| S3 POST 선언 크기 초과 | 거부 | 400 EntityTooLarge                    | 통과                           |

## 한계 (질문이 나오기 전에 먼저 말하기)

| 한계 | 설명 | 향후 |
| --- | --- | --- |
| PRD 8.1과 다른 점 | PRD 8.1은 CloudFront를 "API 프록시로 쓰지 않음(API는 API Gateway 주소 직접 호출)", "사용자 지정 도메인은 범위 밖"으로 정했습니다. 구현은 두 가지가 다릅니다. (1) 관리자 API `/api/admin/*`만 CloudFront를 거쳐 같은 도메인으로 `/prod`에 전달합니다(`admin_origin.py`). 배포 환경에서 콘솔의 교차 출처 관리자 호출 인증이 실패해서 바꿨고, 캐시를 끄고 `Authorization`과 쿼리만 전달합니다. (2) 기존 데모 도메인(Route 53·ACM)을 씁니다. 나머지 API는 PRD대로 API Gateway를 직접 부릅니다. | (제안) PRD 8.1 갱신 또는 교차 출처 인증 재검토. 사용자 지정 도메인은 PRD 15장 향후 계획 |
| 이름·구성 차이 | PRD 10장과 이름이 다릅니다. Lambda 역할 `securedrop-lambda-v13`(PRD `securedrop-api-role`), 알람 `SecureDrop-LambdaErrors`·`SecureDrop-Api5XX`(PRD `securedrop-lambda-errors`·`securedrop-api-5xx`), 지표 `BlockedCount`·`TransferCount`(PRD `BlockCount`·`TransferCreated`). 정리는 `99_teardown.sh` 대신 `teardown.py`, 배포는 `deploy_serverless.py`를 씁니다. 동작 요건은 같습니다. | (제안) PRD 이름 갱신 |
| 알람 임계값 | PRD 10.8은 "5분간 Errors·5XXError 5건 이상"인데, 두 알람 모두 **1분간 1건 이상**으로 더 민감하게 두었습니다(`configure_monitoring.py`, 배포값 동일). 데모 트래픽이 적어 5분·5건이면 오류가 나도 알람이 거의 바뀌지 않기 때문에, 시연과 점검 중 오류를 바로 보이게 하려는 것입니다. 알림 action이 없어 알람이 자주 바뀌어도 메일·호출이 나가지 않습니다. | (제안) 실제 운영 시 PRD 값(300초·5건)으로 조정 |
| CORS 로컬 허용 | prod의 `ALLOWED_ORIGINS`에 `localhost`·`127.0.0.1`이 남아 있습니다. 로컬 개발·테스트를 위해 유지했습니다. | (제안) 운영 배포 시 제거 |
| 4MiB 검사 시간 | 전체 2.48초로 PRD 목표 2초 초과. 서버 처리는 1.47초, 업로드 약 1초는 시연 PC 네트워크 시간입니다. | (제안) 다른 네트워크에서 재측정. 4MB 상한은 PRD 10.10 설계 결정이라 유지 |
| 입력 크기 | Lambda 동기 호출 6MB 한도와 base64(약 1.33배) 때문에 검사 입력은 4MiB까지. 큰 파일은 검사할 수 없습니다. | PRD 15장: SQS·Step Functions 대용량 비동기 검사, 브라우저 로컬 검사 |
| 알람 알림 | 알람은 있지만 알림 action이 없어 콘솔 상태로만 확인됩니다. PRD F13이 알림 발송을 범위 밖으로 둔 그대로입니다. | PRD 15장: SES 알림 |
| 대시보드 링크 | 링크를 열려면 AWS 콘솔 로그인이 필요합니다(PRD F13도 동일). 대시보드는 태그를 지원하지 않습니다. | 해당 없음 |
| 게이트웨이 로그 역할 | `securedrop-apigateway-logs`의 로그 권한이 `Resource: *`. 계정 전체 API Gateway 로깅 역할의 일반적인 형태라 수용했습니다. PRD 10.9에는 이 역할 규정이 없습니다. | (제안) 계정 담당자와 협의 |
| 비용 분리 | PRD 10.1은 "비용 확인을 태그로 한다"고 했지만, 공유 계정이고 비용 할당 태그 권한이 없어 프로젝트별 비용을 나눌 수 없습니다. | (제안) 비용 할당 태그 활성화. PRD 15장 Budgets는 비용 알림 용도 |

### PRD 비목표였으나 추가 적용한 것 (WAF · KMS CMK · CloudTrail)

PRD 2장은 WAF·KMS·CloudTrail을 비목표(향후 계획)로 뒀지만, PRD 범위를 넘어 추가로 구현하고 실계정에 적용했습니다. 설정은 `backend/infra/configure_security.py`(멱등, `--only kms waf cloudtrail`), 종단 점검은 `backend/infra/verify_security.py`, 테스트는 `backend/tests/test_security.py`입니다. `teardown.py`·`tag_resources.py`도 이 리소스를 다루도록 갱신했습니다.

| 서비스     | 적용 내용 |
| ---------- | --------- |
| KMS        | 고객 관리형 키 `alias/securedrop-transfers`, 자동 교체 켬. 파일 버킷 기본 암호화는 SSE-KMS + S3 Bucket Key. 키 정책에서 Lambda 역할에는 `kms:GenerateDataKey`·`kms:Decrypt`만, `kms:ViaService=s3.ap-northeast-2.amazonaws.com`과 버킷 암호화 컨텍스트로 제한하며 IAM 쪽 KMS 권한은 없음. 브라우저 presigned POST는 서명 정책에 `x-amz-server-side-encryption=aws:kms`와 키 ID를 고정(`backend/storage.py:40-55`), 플러그인 경로 `put_object`도 KMS 사용(`storage.py:103-117`). 설정 `KMS_KEY_ID`(`backend/config.py:17`)가 비면 로컬 개발용 SSE-S3. 웹 버킷은 SSE-S3 유지 |
| WAF        | WAFv2 REGIONAL 웹 ACL `securedrop-api-acl`을 API Gateway REST API `prod` 스테이지에 연결(CloudFront 아님). 규칙: AWSManagedRulesCommonRuleSet, KnownBadInputsRuleSet, AmazonIpReputationList, IP당 5분 3,000건 속도 제한(차단). 전체 로깅은 끄고 지표·샘플 요청만 사용. 기존 API Gateway 스로틀·사용량 계획도 유지 |
| CloudTrail | 트레일 `securedrop-trail`, 다중 리전, 로그 파일 무결성 검증 켬. 관리 이벤트 + 파일 버킷 `transfers/` 접두사의 S3 데이터 이벤트만 기록. 전용 로그 버킷(퍼블릭 차단, BucketOwnerEnforced, SSE-S3, TLS만 허용, SourceArn 조건 버킷 정책, 90일 만료). 계정에 기존 트레일이 없어 관리 이벤트는 무료 첫 사본 |

- **WAF Count 예외**: 이 서비스는 민감정보처럼 보이는 내용이 든 최대 4MiB 파일을 정상으로 받습니다. 그래서 CommonRuleSet의 `SizeRestrictions_BODY`, `CrossSiteScripting_BODY`, `GenericLFI_BODY`, `GenericRFI_BODY`, `EC2MetaDataSSRF_BODY`는 차단하지 않고 Count로 둡니다. `<script>`, `../`가 든 4MiB 검사 요청이 WAF를 통과함을 실계정에서 확인했습니다.
- **속도 제한 근거**: 한 행사장 IP 뒤 청중 약 100명 × 약 15회 호출 ≈ 1,500건으로 3,000건 아래입니다. 악용 IP 하나는 약 10 rps로 묶이며 스테이지 스로틀 20 rps보다 낮습니다.
- **월 비용 추정**: KMS 약 $1, WAF 약 $9~10, CloudTrail 약 $0(저장 소액 별도) → 월 약 $10~11. 위 "비용"의 $0.07은 이 적용 전 수치입니다.

### 이번에 하지 않은 것 (PRD 2장 비목표 = 향후 계획)

PRD는 아래 AWS 서비스를 일부러 뺐고, 발표에서 "향후 계획"으로 말하라고 합니다. 향후 도입 순서는 PRD 15장입니다. (WAF·KMS·CloudTrail은 위에서 추가 적용했으므로 제외)

| 뺀 서비스       | 대신 한 것                                              |
| --------------- | ------------------------------------------------------- |
| EventBridge     | S3 수명 주기 1일 + 조회 시점 `expires_at` 비교          |
| SQS·Step Functions | 검사 4MB 상한으로 동기 처리, 업로드 2단계 API        |
| SES             | 이메일 발송 없음                                        |
| CDK             | 스크립트 + 런북(`infra/`)                               |
| Budgets         | 예약 동시성·스로틀 상한, 정리 체크리스트                |
| Cognito         | 일반 사용자 로그인 없음, 관리자만 Lambda 자체 인증      |

## 비용

- 10/1~10/20 Cost Explorer 추정치로 **서버리스 관련 서비스 합계는 약 $0.07**입니다. (API Gateway $0.0024, CloudFront $0.0056, S3 $0.0199, CloudWatch $0.0402, DynamoDB $0.0004, Secrets Manager $0.0001, Lambda $0) Route 53 $0.31은 별도입니다. 청구 확정액이 아닙니다.
- 계정 전체는 $186.27(Cost Explorer 추정치, 계정 전체에 세금 $15.85 포함)이고 대부분이 EC2 기타·EC2·ELB·VPC입니다. 공유 계정이라 SecureDrop EC2(10/19 종료)가 차지한 몫은 청구 콘솔 담당자에게 확인해야 합니다.

## 정리 계획

| 시점          | 할 일                                                                                                                                                                |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 발표 종료까지 | 전체 유지                                                                                                                                                            |
| 발표 직후     | `uv run python infra/teardown.py --api-key-only --execute --confirm <acct>` 로 플러그인 API Key만 삭제                                                               |
| 발표 후       | `teardown.py --execute --confirm <acct>` 로 전체 정리 (API Key, 사용량 계획, REST API, Lambda, S3 2개, DynamoDB 5개, 로그 그룹 2개, 알람 2개, 대시보드, Lambda 역할) |
| 팀 결정       | CloudFront·도메인 유지 여부. 삭제하려면 `--include-cloudfront`(약 15분)                                                                                              |

- `teardown.py`는 기본이 계획만 출력하며, `Project=securedrop` 태그나 배포 기록의 ID와 일치하는 것만 지웁니다. 계획 실행에서 **19개 대상, 건너뛰는 항목 1개**를 확인했고 실제 삭제는 하지 않았습니다.
- Route 53 레코드와 ACM 인증서는 수동으로 정리합니다.
- EC2 시절 레거시 버킷과 IAM 역할은 10-20에 이미 삭제했습니다.

## 예상 질문과 답

- **"왜 EC2에서 서버리스로 옮겼나요?"** 켜 둔 서버 없이 쓴 만큼만 비용이 들고, 서버 관리가 필요 없어서입니다. 서버리스 관련 비용은 약 $0.07입니다.
- **"4MiB가 느린 것 아닌가요?"** 전체 2.48초 중 서버 처리는 1.47초이고, 약 1초는 업로드 네트워크 시간입니다. 목표 초과는 인정합니다.
- **"PRD와 뭐가 다른가요?"** PRD 8.1은 CloudFront를 API 프록시로 쓰지 않는다고 했는데, 관리자 API만 CloudFront를 거쳐 같은 도메인으로 호출합니다. 교차 출처 호출의 인증이 배포 환경에서 실패했기 때문입니다. 또 PRD는 사용자 지정 도메인을 범위 밖으로 뒀지만 기존 데모 도메인을 씁니다. 알람은 PRD의 5분·5건보다 민감한 1분·1건으로 두었습니다. 데모 트래픽이 적어 오류를 바로 보이게 하려는 것이고, 알림이 없어 부작용은 없습니다. 나머지 API는 PRD대로입니다.
- **"왜 REST API인가요? HTTP API가 더 싸지 않나요?"** 플러그인에 필요한 API Key·사용량 계획을 HTTP API가 지원하지 않기 때문입니다(PRD 8.2).
- **"20MB를 왜 S3로 바로 올리나요?"** Lambda 요청·응답 한도가 6MB라서, Lambda는 발급·검증·기록만 합니다. 실제 업로드 구간의 로그로 Lambda를 거치지 않음을 확인했습니다. 크기는 presigned POST의 `content-length-range`로 S3가 강제하고, 초과 시 400 EntityTooLarge를 확인했습니다.
- **"WAF·KMS는 왜/어떻게 썼나요?"** PRD에서는 비목표였지만 추가로 적용했습니다. WAF는 API Gateway 앞에 관리형 규칙과 IP당 속도 제한을 두었고, 4MiB 파일에 민감해 보이는 내용이 들어 있어도 정상으로 받아야 해서 본문 검사 규칙 5개는 차단 대신 Count로 두었습니다(실계정에서 `<script>`·`../`가 든 4MiB 검사 통과 확인). 그 대신 속도 제한과 기존 스로틀로 막습니다. KMS는 고객 관리형 키로 파일을 암호화하고 Lambda 역할에는 S3를 통한 `GenerateDataKey`·`Decrypt`만 줍니다. 그래서 S3 읽기 권한만으로는 KMS 권한이 없으면 파일을 복호화할 수 없습니다. 추가 비용은 월 약 $10~11입니다.
- **"만료 이벤트나 알림은요?"** EventBridge·SES는 쓰지 않았습니다. 만료는 조회 때 `expires_at`으로 판단하고, 파일은 S3 수명 주기가 1일 뒤 지웁니다. 알람은 콘솔 상태로만 봅니다.
- **"로그·DB에 민감정보가 남지 않나요?"** 원칙 4에 따라 파일은 S3에만, 판단 기록은 DynamoDB에만, 운영 신호는 CloudWatch에만 둡니다. 14일치 로그 3,573건을 패턴 검사했고 오탐 외에는 없었습니다. `sd_transfers`에는 파일명만 있고(PRD 10.3 허용) 전송과 함께 TTL로 사라집니다.
- **"IaC는 안 썼나요?"** CDK 없이 스크립트와 런북으로 재현합니다(PRD 비목표). API Gateway는 `apigw-export.json`으로 내보내 커밋했습니다.
- **"콜드 스타트는요?"** 14일간 25회 중앙값 Init 1.84초, 최대 1.91초로 목표 3초 안입니다. 시연 전에 `/api/health`로 예열합니다.
- **"파일이 S3에 계속 남나요?"** 아니요. `transfers/`는 1일 뒤 자동 삭제되고, 버킷은 퍼블릭 접근이 모두 차단되어 있습니다.
- **"비용이 얼마나 드나요?"** 서버리스 관련은 약 $0.07입니다(WAF·KMS·CloudTrail 적용 전 수치, 적용 후 추가 월 약 $10~11). 계정 전체 $186.27은 대부분 EC2 계열이고, 공유 계정이라 프로젝트별로는 나눌 수 없습니다.
- **"발표 끝나면 AWS는요?"** 직후 API Key를 삭제하고, 이후 `teardown.py`로 전체를 정리합니다.

## 발표 전 확인할 것

- [ ] 시연 5분 전 `/api/health` 예열
- [ ] AWS 콘솔 로그인, `SecureDrop-Demo` 대시보드 열어 두기
- [ ] 관리자 콘솔의 CloudWatch 링크 동작 확인
- [ ] 발표 직후 `--api-key-only` 명령 준비(계정 ID는 실행 때 직접 입력)
- [ ] PRD 8.1과 다른 점(관리자 API 프록시, 데모 도메인)과 비목표(향후 계획)를 말할 순서 확인
