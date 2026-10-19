# PRD v1.3 서버리스 배포 런북

이 디렉터리는 v1.3용입니다. `../deploy/`는 이전 EC2 배포 이력이므로 새 배포에 사용하지 않습니다. 스크립트는 직접 실행해야 AWS 리소스를 변경합니다. 기존 데모 도메인을 유지하는 자동 배포는 `deploy_serverless.py`를 사용합니다. 실행 명령과 검증 절차는 [백엔드 README](../README.md#데모-서버리스-배포)를 참고하세요.

## 1. 환경과 DynamoDB

서울 리전의 개발자 AWS CLI profile을 사용하세요. `.env.example`을 복사하고 전송 버킷·CloudFront 주소·허용 origin을 지정합니다. Lambda에는 AWS 키·개발자 profile·플러그인 API Key를 넣지 않습니다.

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

- Block Public Access 4개 모두 활성화, BucketOwnerEnforced, SSE-S3 기본 암호화, 버전 관리 비활성화.
- `aws:SecureTransport=false` 요청을 버킷 정책으로 거부합니다.
- `transfers/` 객체는 생성 1일 후 삭제하고 미완료 멀티파트 업로드도 1일 후 정리합니다.
- CORS origin은 CloudFront와 개발용 localhost만 허용합니다. Methods GET/POST, AllowedHeaders `*`, ExposeHeaders ETag, MaxAgeSeconds 3000.
- Lambda 실행 역할에 이 버킷의 `transfers/*`에 대한 s3:PutObject/GetObject/DeleteObject만 부여합니다. HeadObject는 GetObject 권한을 사용합니다.

웹 버킷은 CloudFront OAC를 통해서만 읽도록 제한합니다. HTTPS redirect, HSTS, nosniff, DENY frame 정책, CSP를 적용하고 connect-src에는 API Gateway와 전송 S3 주소를 허용합니다. API를 CloudFront로 프록시하지 않습니다. `PUBLIC_BASE_URL`은 이 CloudFront origin입니다. `../frontend/`의 `d.html` 수신 페이지와 데모 화면은 새 API 계약을 사용하며 자동 배포 시 이 웹 버킷에 업로드합니다.

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

DEFAULT_4XX/DEFAULT_5XX Gateway Response는 앱과 같은 `{"error":{"code":"...","message":"..."}}` JSON 형식과 실제 CloudFront의 Access-Control-Allow-Origin을 설정합니다. INVALID_API_KEY/THROTTLED/QUOTA_EXCEEDED도 각각 UNAUTHORIZED/RATE_LIMITED로 매핑하세요. API Key 거절은 API Gateway 단계에서 403일 수 있습니다. 요청·응답 본문 data tracing은 비활성화합니다.

접근 로그 그룹은 `/apigw/securedrop-api`입니다. 로그 필드는 requestId, ip, httpMethod, resourcePath, status, responseLatency만 포함하고 토큰·헤더·쿼리·본문은 기록하지 않습니다. resourcePath 템플릿을 사용하며 실제 다운로드 URL을 남기지 않습니다. REST API에서 CloudWatch Logs 역할은 계정별 API Gateway 설정이 필요합니다.

## 5. CloudWatch

```powershell
uv run python infra/configure_monitoring.py --profile lab --api-id YOUR_REST_API_ID
```

Lambda·게이트웨이 로그 그룹을 14일 보존하고 `SecureDrop-Demo` 대시보드와 Lambda Errors/API 5XX 알람 2개를 설정합니다. 알림 action은 없으며 SNS·SES를 만들지 않습니다. 앱은 request_id·경로 템플릿·상태·지연을 JSON 로그로, 차단 건수(유형/출처별)와 전송 수를 `SecureDrop` namespace의 EMF 지표로 기록합니다. 관리자 화면에서는 AWS 콘솔 로그인 후 이 대시보드로 연결하면 됩니다.

## 6. 배포 검증

우선 `/prod/api/health`로 예열하고 `/prod/docs`를 엽니다. TXT 검사 → 마스킹 → content_b64 브라우저 보관 → 승인 → 메타데이터 발급 → 브라우저 S3 POST → 완료 → 공유 페이지 → 모바일 S3 다운로드 순서로 확인합니다. S3 POST는 응답 fields를 변경 없이 사용하고 file을 마지막 필드로 넣습니다.

관리 토큰 없이/다른 토큰으로 조회·완료·폐기는 403이어야 합니다. 객체가 없거나 크기가 다르면 완료 409, 해시 불일치는 삭제 후 409, 폐기·만료 링크는 각각 404/410이어야 합니다. 검사 세션 만료, 정제 잔여 민감정보 확인, 미지원 형식 확인도 검증합니다. API Key 없는 플러그인 호출 거절과 사용량 계획, 관리자 5회 실패 잠금·로그아웃·파일명 비노출을 확인합니다. 파일 원문·토큰이 로그에 없는지도 확인합니다.

4MiB 검사 2초, 1MiB CSV 정제 3초, 20MiB 직접 전송 10초, 콜드 스타트 3초 목표는 실제 AWS에서 측정해야 합니다. 로컬 모의 테스트로 이 성능이나 브라우저의 실제 S3 서명 정책 적용을 보증하지 않습니다.

데모 종료 후 Project 태그로 신규 리소스를 확인해 API 사용량 계획/키, 게이트웨이, Lambda, CloudFront/OAC, S3 버킷 2개, DynamoDB 테이블 5개, 로그·대시보드·알람, IAM 역할을 정리합니다. 기존 EC2 리소스는 이 런북에서 변경하지 않습니다.
