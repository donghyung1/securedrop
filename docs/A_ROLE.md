# A — AWS / 백엔드 점검 결과

기준일: 2026-10-20. 개발자 프로필로 서울 리전 배포 환경을 직접 조회했다. 계정·리소스 ID는 `<acct>` 등으로 표기한다. 측정 원자료는 `backend/deploy/aws/performance-verification.json`(미추적)에 있다.

## 모니터링 (F13, 10.8)

| 항목 | 결과 | 근거 |
|---|---|---|
| 로그 보존 | ✅ | `/aws/lambda/securedrop-api`, `/apigw/securedrop-api` 모두 14일 |
| 대시보드 | ✅ | `SecureDrop-Demo` 1개 |
| 알람 | ✅ | `SecureDrop-LambdaErrors`, `SecureDrop-Api5XX` 2개, 상태 OK, action 없음 |
| 지표 상승 | ✅ | `BlockedCount`(PHONE·RRN/extension)가 10-19 15:35 확장 차단 시 각 2. 10-20 측정 전송 완료 후 `TransferCount`(web) +1 |
| 관리자 콘솔 링크 | ✅ | 배포 `config.js`의 `cloudwatchUrl`이 대시보드 주소로 설정됨(`--frontend-only` 재배포, 무효화 완료) |

## S3·IAM·태그 (10.1~10.2, 10.9)

| 항목 | 파일 버킷 | 웹 버킷 |
|---|---|---|
| Block Public Access 4개 | ✅ 모두 true | ✅ 모두 true |
| 기본 암호화 | ✅ SSE-S3(AES256), SSE-C 차단 | ✅ 동일 |
| TLS 강제 | ✅ `aws:SecureTransport=false` Deny | ✅ 동일 + CloudFront OAC만 GetObject |
| 버전 관리 | ✅ 사용 안 함 | ✅ 사용 안 함 |
| 수명 주기 | ✅ `transfers/` 1일 만료, 미완료 멀티파트 1일 | 해당 없음 |
| 소유권 | ✅ BucketOwnerEnforced | ✅ 동일 |
| CORS | ✅ 웹 도메인·CloudFront·localhost만, GET/POST | 없음 |
| 공개 여부 | ✅ `IsPublic=false` | ✅ `IsPublic=false` |

- **Lambda 역할** `securedrop-lambda-v13`: S3 `transfers/*`의 Put/Get/DeleteObject, DynamoDB 5개 테이블과 GSI의 Get/Put/Update/Delete/Query, 자기 로그 그룹 쓰기만 허용. Scan·관리형 정책 없음. ✅
- **Lambda**: python3.12, 1024MB, 25초, 예약 동시성 10, Function URL 없음, 호출 권한은 해당 REST API ARN으로 제한. 환경 변수에 비밀값 없음(`ALLOWED_ORIGINS`, `APP_ENV`, `FILES_BUCKET`, `PUBLIC_BASE_URL`). ✅
- **API Gateway**: stage 20/40, 로그인 1/5, 공개 경로 5/10 rps, data trace 끔, 사용량 계획 0.5/5·일 2,000회. ✅
- **API Gateway 로그 역할** `securedrop-apigateway-logs`: 로그 권한이 `Resource: *`. 계정 전체 API Gateway 로깅 역할의 일반적인 형태이므로 수용한다.
- **태그** ✅: 2026-10-20 `infra/tag_resources.py --owner dh1 --execute`로 19개 리소스(S3 2, DynamoDB 5, Lambda, API Gateway 4, 로그 그룹 2, IAM 역할 2, 알람 2, CloudFront)에 `Project=securedrop`, `Owner=dh1`을 적용했다. 이전에 태그가 없던 CloudFront 배포와 API stage 포함, 기존 태그는 유지. 대시보드는 태그 미지원.

## 저장 데이터·로그 (10.3, 10.6, 원칙 4)

| 항목 | 결과 | 근거 |
|---|---|---|
| 관리자 비밀번호 | ✅ | `sd_admins.password_hash`가 `scrypt$131072$8$1$...` 형식 |
| 관리 토큰 | ✅ | `sd_transfers.manage_hash`가 64자리 hex(SHA-256)이고 원문 속성 없음(27건) |
| 관리자 세션 | ✅ (코드·테스트) | 점검 시 `sd_admin_sessions` 0건(TTL 삭제). 키가 `token_hash`이고 자동 테스트가 SHA-256 저장을 확인 |
| 검사 세션 | ✅ | `findings`는 유형·건수만 저장(`detector.py`), 원문·content 없음 |
| `sd_events` | ✅ | 185건 속성은 day, ts#uuid, event, source, types, request_id, time, ttl뿐. types는 유형 코드만 |
| `sd_transfers` 파일명 | 허용 | `files[].name`만 존재(PRD 10.3 허용 범위). 서명 URL 없음 |
| CloudWatch 로그 | ✅ | 14일치 Lambda 2,936건과 게이트웨이 637건을 전화·주민·카드·이메일·서명 URL·관리 토큰·Bearer·비밀번호·base64 패턴으로 검사. 걸린 것은 EMF 타임스탬프, UUID, 진단 단계명뿐인 오탐 |

## 성능·크기 경계 (7장, 13장) — 2026-10-20 08:31 측정

시간은 클라이언트(사무실 PC → 서울 리전) 기준이고 업로드 네트워크 시간을 포함한다.

| 항목 | 목표 | 측정 | 판정 |
|---|---|---|---|
| 웜 텍스트 검사 | 1초 | 중앙값 0.12초(10회, 최대 0.125) | ✅ |
| 4MiB 파일 검사 | 2초 | 전체 2.48초 / Lambda 1.47초(앱 1.39초) | ⚠️ 서버 기준 통과, 전체 시간은 업로드 약 1초 포함으로 초과 |
| 1MiB CSV 정제 | 3초 | 2.12초(Lambda 1.63초) | ✅ |
| 20MiB 직접 전송 | 10초 | 2.72초(발급+S3 POST+완료) | ✅ |
| 콜드 스타트 | 3초 | Init 1.87초 + 실행 0.11초 = 1.98초. 14일 Init 25회 중앙값 1.84초, 최대 1.91초 | ✅ |
| 4MiB 초과 검사 | 거부 | 413 | ✅ |
| 20MiB 초과 전송 발급 | 거부 | 413 | ✅ |
| S3 POST 선언 크기 초과 | 거부 | 400 EntityTooLarge | ✅ |

4MiB 검사는 시연 PC의 업로드 대역폭에 좌우된다. 시연은 작은 파일로 진행하고 4MiB 결과는 서버 처리 시간과 함께 설명한다.

## API 내보내기·정리 (10.11~10.13, Day 2·5)

- `backend/infra/apigw-export.json`: prod stage OAS 3.0 내보내기. 계정·API ID를 치환했고 API Key 값은 들어 있지 않다. ✅
- `backend/infra/teardown.py`: 기본은 계획만 출력하고, 태그·ID가 확인된 리소스만 삭제한다. 발표 직후 `--api-key-only`로 API Key만 삭제할 수 있다. 계획 실행 결과 19개 대상과 건너뛰는 항목 1개를 확인했다. 실제 삭제는 하지 않았다. ✅
- 관리자 API 경로: CloudFront `/api/admin/*` 프록시를 유지하기로 하고 이유를 런북 2절에 기록했다. 발표 자료에 PRD 8.1과 다른 점으로 명시해야 한다.

## 비용·정리 대상 (Day 5)

- 10/1~10/20 Cost Explorer 추정치에서 서버리스 관련 서비스 합계는 약 **$0.07**(API Gateway $0.002, CloudFront $0.006, S3 $0.020, CloudWatch $0.040, Route 53 $0.31 별도)이다. 계정 전체는 $186.27로 EC2 기타 $100.77, EC2 $44.15, ELB $13.05, VPC $11.92가 대부분이다. 공유 계정이고 비용 할당 태그 권한이 없어서 프로젝트별로 나눌 수 없다. 이 중 SecureDrop EC2(10/19 종료)가 차지하는 몫은 청구 콘솔 담당자에게 확인해야 한다. Lambda·DynamoDB는 $0이다.
- 남은 SecureDrop EC2 자원: 인스턴스·EIP·볼륨·NAT·VPC 없음(태그 기준). 레거시 버킷 `securedrop-cloud-<acct>-ap-northeast-2`(Project=SecureDropCloud, 객체 2개, 2.4MB)와 IAM 역할·인스턴스 프로파일 `securedrop-cloud-backend`(SSM 관리형 정책 + 해당 버킷 전용 인라인 정책)는 2026-10-20 aws cli로 삭제했다(Lambda 환경변수 참조 없음, 프로파일 사용 인스턴스 없음 확인 후). EC2 시절 수동 정리 대상은 더 이상 없다.
- 배포 버전: Lambda 최종 수정 2026-10-19 07:26 UTC, API 배포 `x122qq`, 웹 자산 2026-10-20 master 기준.
- **유지**: 발표 종료까지 전체 유지. **발표 직후**: API Key 삭제. **발표 후**: `teardown.py` 전체 정리. CloudFront·도메인 유지 여부는 팀이 결정한다.
