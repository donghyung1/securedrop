# PRD v1.3 서버리스 배포 런북

이 디렉터리는 v1.3용입니다. `../deploy/`는 이전 EC2 배포 이력이므로 새 배포에 사용하지 않습니다. 스크립트는 직접 실행해야 AWS 리소스를 변경합니다. 기존 데모 도메인을 유지하는 자동 배포는 `deploy_serverless.py`를 사용합니다. 실행 명령과 검증 절차는 [백엔드 README](../README.md#데모-서버리스-배포)를 참고하세요.

## 1. 환경과 DynamoDB

서울 리전의 개발자 AWS CLI profile을 사용하세요. 명령 예시는 `--profile lab` 기준이며, 로컬에 `lab` 프로필이 없으면 `default`로 바꿔 실행합니다(`aws configure list-profiles`로 확인). `.env.example`을 복사하고 전송 버킷·CloudFront 주소·허용 origin을 지정합니다. Lambda에는 AWS 키·개발자 profile·플러그인 API Key를 넣지 않습니다.

```powershell
cd backend
uv sync --frozen
uv run python infra/create_tables.py --profile lab
uv run python infra/create_admin.py operator --profile lab
```

테이블은 온디맨드이며 다음 키를 사용합니다. 생성 스크립트는 기존 테이블을 재사용하고 새 테이블에 `Project=securedrop`을 붙입니다. 팀의 Owner 태그는 콘솔에서 추가하세요. 기존 테이블 스키마가 다르면 새 이름을 환경 변수로 지정해야 합니다.

| 테이블 | 키 | TTL |
|---|---|---|
| sd_admins | username | 없음 |
| sd_admin_sessions | token_hash | ttl: 8시간 |
| sd_sessions | session_id | ttl: 10분 |
| sd_transfers | transfer_id | 미완료 1시간, 활성 전송 만료+1일 |
| sd_events | day + ts#uuid | ttl: 30일 |

sd_transfers에는 `day-created-index` GSI(created_day HASH, created_at RANGE, ALL projection)가 필요합니다. TTL은 즉시 삭제되지 않으므로 앱은 조회 시 만료를 별도로 확인합니다. DynamoDB 권한은 5개 테이블의 GetItem/PutItem/UpdateItem/DeleteItem/Query 및 전송 GSI Query로 제한합니다. 앱은 Scan이나 테이블 생성 권한이 필요하지 않습니다. 관리자 생성·비밀번호 재설정 스크립트는 개발자 프로필 권한으로만 실행합니다.

## 2. S3와 CloudFront

파일·웹 버킷 2개를 서울 리전에 생성하고 `Project=securedrop`, `Owner=<팀 담당자>` 태그를 붙입니다. 파일 버킷은 다음과 같이 구성합니다.

- Block Public Access 4개 모두 활성화, BucketOwnerEnforced, 버전 관리 비활성화. 기본 암호화는 `configure_security.py`가 만든 고객 관리형 KMS 키(`alias/securedrop-transfers`)의 SSE-KMS + Bucket Key입니다(키 생성 전·로컬은 SSE-S3). 웹 버킷은 SSE-S3입니다.
- `aws:SecureTransport=false` 요청을 버킷 정책으로 거부합니다.
- `transfers/` 객체는 생성 1일 후 삭제하고 미완료 멀티파트 업로드도 1일 후 정리합니다.
- CORS origin은 CloudFront와 개발용 localhost만 허용합니다. Methods GET/POST, AllowedHeaders `*`, ExposeHeaders ETag, MaxAgeSeconds 3000.
- Lambda 실행 역할에 이 버킷의 `transfers/*`에 대한 s3:PutObject/GetObject/DeleteObject만 부여합니다. HeadObject는 GetObject 권한을 사용합니다.

웹 버킷은 CloudFront OAC를 통해서만 읽도록 제한합니다. HTTPS redirect, HSTS, nosniff, DENY frame 정책, CSP를 적용하고 connect-src에는 API Gateway와 전송 S3 주소를 허용합니다. API는 CloudFront로 프록시하지 않지만 관리자 API(`/api/admin/*`)는 예외입니다. 배포 환경에서 콘솔의 교차 출처 관리자 호출 인증이 실패해 같은 도메인으로 전달하도록 바꿨습니다(`admin_origin.py`, 커밋 b692ff8). 이 경로는 캐시를 끄고 `Authorization`과 쿼리만 API Gateway `/prod`로 전달합니다. PRD 8.1과 다른 점이므로 발표 자료에 명시합니다. `PUBLIC_BASE_URL`은 이 CloudFront origin입니다. `../frontend/`의 `d.html` 수신 페이지와 데모 화면은 새 API 계약을 사용하며 자동 배포 시 이 웹 버킷에 업로드합니다.

## 3. Lambda 패키지

```powershell
uv run python infra/package_lambda.py
```

`dist/securedrop-api.zip`을 생성합니다. Python 3.12용 manylinux2014 x86_64 바이너리 휠을 받아 pydantic-core를 포함하고, 프론트·테스트·`.env`·로컬 AWS 자료는 포함하지 않습니다. segno는 Pillow 없이 PNG QR을 생성합니다.

Lambda 함수 `securedrop-api`를 Python 3.12 / x86_64 / 1024MB / 25초 / 예약 동시성 10으로 생성합니다. 핸들러는 **handler.handler**입니다. 실행 역할의 신뢰 주체는 lambda.amazonaws.com이며, 위 S3 권한·DynamoDB 권한과 해당 Lambda 로그 그룹에 대한 로그 쓰기 권한을 추가합니다. VPC 연결은 필요하지 않습니다.

환경 변수는 `.env.example`의 설정을 사용하되 `APP_ENV=prod`로 변경하고 `AWS_PROFILE`은 제외합니다. `AWS_REGION`은 Lambda가 제공합니다. `PUBLIC_BASE_URL`과 명시적 `ALLOWED_ORIGINS`를 실제 CloudFront·확장 origin으로 설정합니다. Mangum은 `/prod` 기본 경로를 처리하고 Swagger 서버도 그 경로를 사용합니다. 함수에 공개 Function URL은 만들지 않습니다.

## 4. API Gateway REST API

REST API `securedrop-api`를 만들고 `prod` stage를 사용합니다. 아래 리소스의 메서드는 모두 같은 Lambda의 AWS_PROXY 통합으로 구성합니다. Lambda 실행 권한의 SourceArn은 이 REST API의 execute-api ARN으로 제한합니다.

| 리소스 | API Key | 스로틀 |
|---|---|---|
| `/api/health`, `/api/scan`, `/api/d/{id}`, `/api/ext/logs` | 불필요 | 5 rps, burst 10 |
| `/api/admin/login` | 불필요 | 1 rps, burst 5 |
| `/api/plugin/scan`, `/api/plugin/transfers` 또는 `/api/plugin/{proxy+}` | 필요 | 사용량 계획 |
| `/api/plugin/openapi.json` | 불필요, 구체 경로로 명시 | stage 기본 |
| `/api/{proxy+}`, `/docs`, `/openapi.json` | 불필요 | stage 기본 |

stage 기본은 20 rps / burst 40입니다. 플러그인 사용량 계획 `plugin-plan`은 0.5 rps / burst 5 / 일 2,000회로 설정하고 데모 API Key 1개를 연결합니다. API Key는 애플리케이션 환경 변수에 넣지 않습니다. Lambda의 플러그인 경로는 게이트웨이가 검증한 `requestContext.identity.apiKeyId`가 없으면 거절합니다. 임의 헤더만으로 로컬 API 또는 직접 Lambda 호출을 우회할 수 없습니다.

OPTIONS는 모든 관련 리소스에서 API Key 없이 Lambda로 프록시해야 FastAPI CORS가 동작합니다. binary media types에 `multipart/form-data`를 추가하고 배포합니다. Lambda 동기 호출 한도 때문에 파일 입력은 4MiB이며 20MiB 전송은 S3로 직접 보냅니다.

DEFAULT_4XX/DEFAULT_5XX 및 UNAUTHORIZED(401), ACCESS_DENIED/INVALID_API_KEY(403), THROTTLED/QUOTA_EXCEEDED(429) Gateway Response는 앱과 같은 `{"error":{"code":"...","message":"..."}}` JSON 형식을 사용합니다. 오류의 Access-Control-Allow-Origin은 `method.request.header.Origin`으로 매핑하여 콘솔·확장 각각의 origin을 반환하고, Vary: Origin과 Cache-Control: no-store를 설정합니다. 이 매핑은 일반적인 게이트웨이 오류 문구를 모든 origin에서 읽도록 허용하며 credentials는 허용하지 않습니다. 애플리케이션 데이터와 OPTIONS는 Lambda의 명시적 ALLOWED_ORIGINS로 계속 제한합니다. [AWS 헤더 매핑 문서](https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-swagger-extensions-gateway-responses.responseParameters.html)를 참고하세요. 요청·응답 본문 data tracing은 비활성화합니다.

배포 스크립트는 `extension/manifest.json`의 고정 공개키에서 확장 origin을 계산하고 `.env`의 ALLOWED_ORIGINS와 함께 반영합니다. CORS 설정만 기존 배포에 적용하려면 백엔드 폴더에서 `uv run python infra/deploy_serverless.py --cors-only`를 실행합니다. 이 모드는 기존 Lambda 환경 변수를 보존하고 허용 origin을 추가한 뒤 Gateway Response를 설정하여 prod stage를 재배포합니다.

접근 로그 그룹은 `/apigw/securedrop-api`입니다. 로그 필드는 requestId, ip, httpMethod, resourcePath, status, responseLatency만 포함하고 토큰·헤더·쿼리·본문은 기록하지 않습니다. resourcePath 템플릿을 사용하며 실제 다운로드 URL을 남기지 않습니다. REST API에서 CloudWatch Logs 역할은 계정별 API Gateway 설정이 필요합니다.

## 5. CloudWatch

```powershell
uv run python infra/configure_monitoring.py --profile lab --api-id YOUR_REST_API_ID
```

Lambda·게이트웨이 로그 그룹을 14일 보존하고 `SecureDrop-Demo` 대시보드와 Lambda Errors/API 5XX 알람 2개를 설정합니다. 알림 action은 없으며 SNS·SES를 만들지 않습니다. 앱은 request_id·경로 템플릿·상태·지연을 JSON 로그로, 차단 건수(유형/출처별)와 전송 수를 `SecureDrop` namespace의 EMF 지표로 기록합니다. 배포 스크립트는 `config.js`의 `cloudwatchUrl`에 이 대시보드 주소를 넣으므로 관리자 콘솔에 링크가 표시됩니다. 링크를 열려면 AWS 콘솔에 로그인해야 합니다.

배포된 REST API 정의는 `apigw-export.json`(OAS 3.0, API Gateway 확장 포함)에 보관합니다. 계정 ID와 REST API ID는 `<ACCOUNT_ID>`, `<REST_API_ID>`로 바꿨고 API Key 값은 내보내기에 포함되지 않습니다. 재생성은 `aws apigateway get-export --parameters extensions=apigateway --export-type oas30`으로 하며 같은 치환을 적용합니다.

## 5-1. 보안 설정(KMS, WAF, CloudTrail)

```powershell
uv run python infra/configure_security.py --profile lab --deploy-code   # 멱등. --only kms waf cloudtrail 로 일부만
uv run python infra/verify_security.py --profile lab
```

- **KMS**: 대칭 CMK, 자동 교체, alias `alias/securedrop-transfers`, `Project=securedrop`. 키 정책은 계정 루트 관리자와 Lambda 역할(`kms:GenerateDataKey`, `kms:Decrypt`만)을 허용하며 `kms:ViaService=s3.ap-northeast-2.amazonaws.com`과 `kms:EncryptionContext:aws:s3:arn`(전송 버킷)으로 제한합니다. Lambda IAM 정책에는 KMS 문장이 없습니다. 브라우저 S3 POST는 Lambda 역할이 서명하므로 S3가 이 역할의 권한으로 KMS를 호출합니다. Lambda 환경 변수 `KMS_KEY_ID`(키 ARN)가 `storage.py`의 POST 필드·조건(`x-amz-server-side-encryption=aws:kms`, `...-aws-kms-key-id`)과 `put_object`에 쓰입니다. 파일 버킷 정책은 `aws:SecureTransport` 거부만 있으며 암호화 방식 거부 규칙은 없습니다(기본 암호화로 충분). 키·ARN은 `serverless.json`(`kms_key_id`, `kms_key_arn`)에 기록됩니다.
- **WAF**: REGIONAL Web ACL `securedrop-api-acl`(기본 Allow, 지표·샘플 요청 활성화)을 `prod` stage에 연결합니다. 규칙은 IP당 5분 3,000건 Block(행사장 한 IP의 청중 수백 명 사용과 stage 20 rps 스로틀을 고려한 값), AmazonIpReputationList, KnownBadInputsRuleSet, CommonRuleSet입니다. CommonRuleSet의 `SizeRestrictions_BODY`(8KB 초과), `CrossSiteScripting_BODY`, `GenericLFI_BODY`, `GenericRFI_BODY`, `EC2MetaDataSSRF_BODY`는 Count입니다(파일 본문 검사 오탐 방지). CloudFront용(us-east-1) ACL은 만들지 않았습니다. 요청 전체 로깅은 비용 때문에 켜지 않았습니다.
- **CloudTrail**: `securedrop-trail`은 다중 리전, 로그 파일 검증, 관리 이벤트와 전송 버킷 `transfers/` 접두사의 S3 데이터 이벤트(고급 이벤트 선택기)를 기록합니다. 로그 버킷 `securedrop-cloudtrail-<계정>-ap-northeast-2`는 퍼블릭 차단, BucketOwnerEnforced, SSE-S3, 비 TLS 거부, `aws:SourceArn` 제한 정책, 90일 만료입니다. 기존 트레일이 있으면 관리 이벤트 두 번째 사본은 과금되므로 먼저 `aws cloudtrail describe-trails --include-shadow-trails`로 확인하세요.

`deploy_serverless.py`를 다시 실행해도 `serverless.json`에 키가 있으면 SSE-KMS와 `KMS_KEY_ID`를 유지합니다. 순서는 배포 → `configure_security.py`입니다.

## 6. 배포 검증

우선 `/prod/api/health`로 예열하고 `/prod/docs`를 엽니다. TXT 검사 → 마스킹 → content_b64 브라우저 보관 → 승인 → 메타데이터 발급 → 브라우저 S3 POST → 완료 → 공유 페이지 → 모바일 S3 다운로드 순서로 확인합니다. S3 POST는 응답 fields를 변경 없이 사용하고 file을 마지막 필드로 넣습니다.

관리 토큰 없이/다른 토큰으로 조회·완료·폐기는 403이어야 합니다. 객체가 없거나 크기가 다르면 완료 409, 해시 불일치는 삭제 후 409, 폐기·만료 링크는 각각 404/410이어야 합니다. 검사 세션 만료, 정제 잔여 민감정보 확인, 미지원 형식 확인도 검증합니다. API Key 없는 플러그인 호출 거절과 사용량 계획, 관리자 5회 실패 잠금·로그아웃·파일명 비노출을 확인합니다. 파일 원문·토큰이 로그에 없는지도 확인합니다.

4MiB 검사 4초, 1MiB CSV 정제 3초, 20MiB 직접 전송 10초, 콜드 스타트 3초 목표는 실제 AWS에서 측정해야 합니다. 로컬 모의 테스트로 이 성능이나 브라우저의 실제 S3 서명 정책 적용을 보증하지 않습니다. `uv run python infra/measure_performance.py`는 이 목표와 4MiB·20MiB 초과 거부, S3 POST 선언 크기 초과 거부를 측정합니다. 함수를 15분 이상 쉬게 한 뒤 실행해야 첫 호출이 콜드 스타트가 됩니다. 시간은 호출자 네트워크를 포함하므로 Lambda 로그의 `latency_ms`·REPORT와 함께 판단합니다. 결과는 `../deploy/aws/performance-verification.json`에 저장되며 측정에 쓴 전송은 폐기합니다.

## 7. 태그와 정리

`uv run python infra/tag_resources.py --owner <담당자> --execute`는 S3·DynamoDB·Lambda·API Gateway(REST API·stage·사용량 계획·키)·로그 그룹·IAM 역할·알람·CloudFront와 보안 설정 리소스(KMS 키, WAF ACL, CloudTrail 트레일·로그 버킷)에 `Project=securedrop`, `Owner`를 붙입니다. 기존 태그는 유지되며 대시보드는 태그를 지원하지 않습니다. `--execute`를 빼면 계획만 출력합니다.

`infra/teardown.py`는 기본적으로 계획만 출력합니다. 삭제 전에 `Project=securedrop` 태그 또는 `../deploy/aws/serverless.json`의 ID와 일치하는지 확인하고, 맞지 않는 리소스는 건너뜁니다.

```powershell
uv run python infra/teardown.py                                                  # 계획 확인
uv run python infra/teardown.py --api-key-only --execute --confirm <계정 ID>     # 발표 직후: 플러그인 API Key 삭제
uv run python infra/teardown.py --execute --confirm <계정 ID>                    # 전체 정리(CloudFront 유지)
uv run python infra/teardown.py --execute --confirm <계정 ID> --include-cloudfront
```

전체 정리 대상은 API Key, 사용량 계획, REST API, Lambda, S3 버킷 2개(객체 포함), WAF Web ACL(먼저 연결 해제), CloudTrail 트레일과 로그 버킷(객체 포함), KMS 키(alias 삭제 후 최소 대기 7일로 삭제 예약), DynamoDB 테이블 5개, 로그 그룹 2개, 알람 2개, 대시보드, Lambda 역할입니다. CloudFront 배포는 기존 도메인을 쓰므로 `--include-cloudfront`를 줄 때만 비활성화 후 삭제합니다(약 15분 소요). OAC와 응답 헤더 정책도 이때 함께 삭제합니다. `securedrop-apigateway-logs` 역할은 계정 전체의 API Gateway 로그 역할로 등록되어 있으면 건너뜁니다. Route 53 레코드, ACM 인증서, EC2 시절의 `securedrop-cloud-backend` 역할과 `securedrop-cloud-<계정>-<리전>` 버킷은 수동으로 정리합니다.
