# SecureDrop Cloud Backend

PRD v1.1의 백엔드를 구현한 FastAPI 서버입니다. 승인된 전송 파일은 S3, 전송 메타데이터와 이벤트는 SQLite에 저장합니다. 정제 처리본은 TTL 동안 서버 메모리에만 보관합니다. Python 3.10 이상을 지원하고 개발 검증은 Python 3.12 및 uv를 사용하며 `uv.lock`으로 의존성을 고정합니다.

## 실행

```powershell
cd backend
uv sync --locked
Copy-Item .env.example .env
# .env에 S3_BUCKET, PLUGIN_API_KEY, ALLOWED_ORIGINS를 설정
# 로컬에서는 AWS_PROFILE=lab, EC2에서는 AWS_PROFILE을 제거하고 IAM Role 사용
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log
```

`PLUGIN_API_KEY`는 `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`로 생성할 수 있습니다. 비어 있으면 플러그인 인증을 허용하지 않습니다. `ALLOWED_ORIGINS`는 JSON 배열이며 실제 프론트엔드 및 `chrome-extension://확장ID`를 명시하세요. `.env`는 backend 디렉터리 기준으로 읽습니다.

헬스 체크: `http://localhost:8000/api/health`. Swagger: `http://localhost:8000/docs`. Swagger의 Authorize에 API Key를 입력하면 테스트할 수 있습니다. 헬스 응답은 프로세스 상태이며 S3 연결 성공을 의미하지 않습니다.

## 테스트용 프론트엔드

위 서버를 실행한 뒤 **http://localhost:8000/** 에 접속하세요. 별도의 Node.js 설치나 빌드 없이 HTML/CSS/JavaScript 화면을 제공합니다.

- 텍스트 민감정보 검사 및 샘플 입력
- 파일 전송, 5분/10분/1시간 만료 링크와 QR 코드, 링크 복사 및 전송 폐기
- 민감정보 발견 시 전송 중단, TXT/CSV 전체 마스킹·삭제, 처리본 미리보기와 승인
- 별도 위험 확인 후 민감정보를 포함한 파일 전송

`ALLOWED_ORIGINS`에 `http://localhost:8000`을 포함하세요. `127.0.0.1`이나 다른 호스트로 접속하면 해당 Origin도 명시해야 합니다. 필요하면 화면 아래 연결 설정에 API Key를 입력할 수 있으며 브라우저 저장소에는 저장하지 않습니다. 키를 사용하면 출처는 plugin으로 기록되고 분당 30회 제한을 적용합니다.

파일 전송에는 기존 S3 설정과 AWS 자격 증명이 필요하며 **실제 S3 객체가 생성됩니다.** PDF·이미지의 내용 검사는 지원하지 않습니다. 페이지를 닫거나 새로 열면 화면의 전송 결과가 사라지지만 이미 생성한 링크는 만료 또는 폐기 전까지 유효합니다. 남은 처리본은 서버 TTL에 따라 정리됩니다. 링크 복사는 localhost 또는 HTTPS 환경에서 사용하세요. 로그인 없는 테스트 UI입니다.

## API 계약

| 메서드 | 경로 | 내용 |
|---|---|---|
| GET | `/api/health` | 상태, 리전 |
| POST | `/api/scan` | JSON `text`, 최대 50,000자 |
| POST | `/api/sanitize` | multipart `file`(.txt/.csv), `actions`(JSON 배열), 정제·재검사·미리보기 |
| DELETE | `/api/sanitize/{sanitize_id}` | 대기 중인 처리본을 메모리에서 폐기 |
| POST | `/api/assist` | JSON `message`, `context`(파일명, findings), 고정 조치·버튼 반환 |
| POST | `/api/transfers` | multipart `file` 또는 `sanitize_id`, `expires_in`, `confirm_sensitive`, `user_approved` |
| GET | `/api/transfers/{id}` | 활성 상태 및 만료 시간 |
| DELETE | `/api/transfers/{id}` | S3 객체 삭제 후 폐기 기록 |
| POST | `/api/logs` | `event: BLOCK`, `types: [...]`, `source: web 또는 extension` |
| GET | `/api/logs?limit=50` | 최신 이벤트, limit 1~100 |
| POST | `/plugin/scan` | API Key 필수, `scan_text` 도구 |
| POST | `/plugin/transfers` | API Key 및 사용자 승인 필수, `create_transfer` 도구 |
| GET | `/plugin/openapi.json` | 위 두 도구만 포함한 플러그인 명세 |

웹·확장은 허용된 `Origin`으로 호출합니다. 확장은 `X-Source: extension`을 추가합니다. Origin 없는 CLI·Swagger·플러그인은 `X-API-Key`가 필요합니다. Key를 보낸 요청은 출처를 plugin으로 처리하고 공통적으로 키당 분당 30회로 제한합니다. 잘못된 Key는 허용 Origin에서도 거절합니다. 브라우저용 Origin 검사는 로그인이나 비브라우저 클라이언트 인증을 대신하지 않습니다. PRD의 로그인 없는 데모 범위이며 운영용 사용자 권한 관리는 포함하지 않습니다.

```powershell
Invoke-RestMethod http://localhost:8000/api/scan -Method Post `
  -Headers @{ Origin = 'http://localhost:5500' } `
  -ContentType 'application/json' -Body '{"text":"010-1234-5678"}'
```

탐지 유형은 `RRN`, `PHONE`, `EMAIL`, `AWS_ACCESS_KEY`, `API_KEY`, `CARD`입니다. 주민등록번호는 생년월일·기존 체크섬, 카드번호는 Luhn 검사로 검증합니다. 정규식 탐지는 모든 기밀이나 토큰을 식별하지 못합니다. 체크섬 기반 RRN 규칙은 PRD 기준이며 무작위로 부여된 번호는 놓칠 수 있습니다.

파일은 최대 20 MiB입니다. 만료 시간은 300/600/3600초, 기본 600초입니다. `.txt .csv .json .md .log .xml .yaml .yml .pdf .png .jpg .jpeg .gif .webp`만 허용합니다. 텍스트 계열은 UTF-8, BOM이 있는 UTF-16, CP949를 지원하며 파일 전체를 검사합니다. PDF와 이미지는 내용 검사 대상이 아닙니다. MIME 검사나 바이러스 검사는 포함하지 않습니다.

민감정보가 있으면 업로드 없이 HTTP 409 `SENSITIVE_DATA`, `findings`, `confirmation_required: true`, `sanitize_available`을 반환합니다. `.txt`·`.csv`는 다음 정제 흐름으로 안내합니다. 위험을 이해하고 원본 그대로 진행하는 별도 확인에는 `confirm_sensitive=true`를 보냅니다. 플러그인은 모든 업로드에 `user_approved=true`가 필요하며, 민감정보가 있으면 별도로 `confirm_sensitive=true`도 필요합니다. 이 플래그는 클라이언트가 전달하는 승인 표시이므로 실제 사용자 승인 UI는 호출 플랫폼에서 구현해야 합니다. 외부 AI 플랫폼 연결 및 승인 UI는 이번 백엔드 구현에 포함하지 않았으며 `/docs`에서 시연할 수 있습니다.

## 정제 및 SecureGuard 흐름

1. `/api/sanitize`에 원본 파일과 `actions`를 보냅니다. 예: `[{"action":"MASK","targets":["RRN","PHONE"]},{"action":"DELETE","targets":["EMAIL"]}]`. 각 유형에는 한 조치만 허용합니다. `[{"action":"CANCEL","targets":[]}]`는 처리본을 만들지 않고 취소합니다.
2. 응답의 `findings_before`, `findings_after`, `clean`, `preview`, `sanitize_id`를 표시합니다. 미리보기는 상위 20행, 최대 10,000자이며 잔여 민감정보가 포함될 수 있으므로 화면에서만 취급하고 로깅하지 마세요. 원본은 변경하지 않습니다. 처리본은 UTF-8입니다.
3. 사용자가 미리보기를 확인하고 승인하면 `/api/transfers`에 `sanitize_id`, `user_approved=true`를 보냅니다. `file`과 `sanitize_id`는 동시에 보낼 수 없습니다. 서버는 저장된 처리본을 다시 검사합니다. 잔여 민감정보가 있으면 409 `SENSITIVE_REMAINS`로 중단하며, 별도의 위험 확인인 `confirm_sensitive=true`까지 있어야 전송합니다. 추가 정제를 원하면 원본에 필요한 유형을 더 선택해 `/api/sanitize`를 다시 호출하고 이전 처리본은 DELETE로 폐기하세요.
4. 전송 성공 시 처리본은 즉시 메모리에서 삭제됩니다. S3 저장 실패 시 만료 전 재시도가 가능합니다. 처리본은 기본 600초(`SANITIZE_TTL_SEC`) 후 정리되며, 만료·취소·이미 전송·서버 재시작으로 사용할 수 없으면 410 `EXPIRED`를 반환합니다. 취소 버튼은 `/api/sanitize/{sanitize_id}`를 DELETE합니다.

마스킹은 주민등록번호 `900101-1******`, 전화번호 `010-****-5678`, API Key·AWS Key·이메일·카드번호 `[REDACTED]` 형식입니다. DELETE는 탐지된 값만 제거하여 CSV 행·열 구분자를 유지합니다. 정제와 `/api/scan`은 같은 탐지 규칙을 사용하며, 파일 전체를 재검사합니다.

`/api/assist` 요청 예시는 다음과 같습니다. 파일 내용·탐지 원문·위치는 전달하지 않습니다.

```json
{
  "message": "주민등록번호만 가리고 보내줘",
  "context": {
    "filename": "customers.csv",
    "findings": [{"type": "RRN", "count": 2}]
  }
}
```

응답은 `action`(`MASK`, `DELETE`, `CANCEL`, `CLARIFY`), `targets`, `reply`, `options`(선택 버튼)입니다. 빈 message로 호출하면 탐지 유형·개수와 조치 버튼을 안내합니다. 모호하거나 부정·혼합된 요청은 CLARIFY로 되묻습니다. 도우미는 메타정보와 키워드만 사용하고 파일에 접근하지 않습니다. 반환한 조치를 사용자에게 확인한 뒤 `/api/sanitize`에 적용하세요. 버튼만으로도 전체 흐름을 완료할 수 있습니다.

처리본 메모리 한도는 기본 100 MiB(`SANITIZE_MEMORY_MB`)이며 초과 시 503 `CAPACITY_EXCEEDED`를 반환합니다. 데모는 **서버 1개·Uvicorn worker 1개**로 실행하세요. 메모리 처리본은 프로세스 간 공유하지 않으며 서버 종료 시 모두 폐기됩니다.

## 전송 결과 및 오류

응답의 `url`은 S3 다운로드 Presigned URL이며 `qr`은 해당 URL을 인코딩한 PNG data URI입니다. 전송 상태 조회에서는 링크를 다시 발급하지 않습니다. S3 직접 다운로드를 추적하지 않으므로 `downloaded`는 항상 false이며 다운로드 여부 판단에 사용하면 안 됩니다. 만료 시 상태 조회는 410, 폐기·미존재는 404입니다. 만료 후에도 DELETE로 객체를 정리할 수 있습니다.

검증 오류는 422, 인증 실패는 401, 승인 누락은 403, 용량 초과는 413, 정제 미지원 형식은 415 `UNSUPPORTED_TYPE`, 민감정보 확인 필요는 409, 호출 제한은 429, S3 오류는 502입니다. 공통 형식은 `{"error":{"code":"...","message":"..."}}`이며 검증 오류에 원문을 포함하지 않습니다. 외부 차단 로그는 BLOCK만 허용하며 정제(SANITIZE)·전송·폐기 이벤트는 서버에서 생성합니다.

## AWS 및 배포 조건

AWS CLI로 S3 및 EC2용 최소 권한 IAM Role/Instance Profile을 생성하고 로컬 `.env`를 구성할 수 있습니다. 현재 CLI 계정을 확인한 후 실행하세요. API Key는 새 `.env`에 무작위로 생성하며 콘솔에 출력하지 않습니다. 기존 API Key는 유지합니다.

```powershell
# 프로젝트 루트에서 실행 (기본 profile: default, 리전: ap-northeast-2)
./backend/deploy/provision-aws.ps1
# 다른 프로필 또는 새 프로젝트 전용 버킷 지정
./backend/deploy/provision-aws.ps1 -Profile lab -BucketName your-unique-project-bucket

cd backend
uv run python deploy/verify_live.py
```

프로비저닝 스크립트는 지정 버킷의 보안·수명 주기 정책과 `securedrop-cloud-backend` Role의 전용 inline policy를 설정합니다. 이 프로젝트 전용 리소스에만 사용하세요. EC2 인스턴스나 HTTPS 서버는 생성하지 않습니다. 리소스 정보와 적용한 정책은 `deploy/aws/`에 기록합니다. 실제 S3 검증은 작은 파일을 업로드하고 서명 URL 다운로드·익명 접근 차단·암호화·폐기 후 접근 차단을 확인한 뒤 객체를 삭제합니다. 해당 테스트는 실제 AWS API를 호출합니다.

- 서울 리전의 비공개 S3 버킷을 준비하고 Block Public Access 전체 활성화, 기본 SSE-S3 암호화, 생성 1일 뒤 만료하는 수명 주기를 설정하세요. 앱은 업로드마다 `AES256` 암호화를 명시합니다.
- IAM Role은 `arn:aws:s3:::버킷이름/transfers/*`에 대한 `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`만 필요합니다. 앱에 AWS 키를 넣지 않습니다. SDK 기본 자격 증명 체인과 CLI profile/IAM Role을 사용합니다.
- 버킷 버전 관리는 비활성화한 데모 구성을 사용하세요. 버전 관리가 켜져 있으면 삭제 마커 뒤의 이전 버전이 남으므로 별도 비현재 버전 수명 주기가 필요합니다.
- EC2에는 HTTPS reverse proxy를 구성하고 Uvicorn은 내부 주소로 바인딩하세요. 프록시에서도 요청 크기·타임아웃을 제한하고 요청 본문, API Key, 응답의 Presigned URL을 기록하지 마세요. Uvicorn access log는 위 명령으로 비활성화합니다.
- SQLite 파일을 EC2 영구 볼륨에 두세요. 호출 제한은 SQLite 트랜잭션으로 처리합니다. 여러 EC2 인스턴스 사이의 공유 제한은 제공하지 않습니다.
- 파일 내용·스캔 원문·파일명·전체 URL·챗봇 대화는 DB나 로그에 저장하지 않습니다. 전송 메타데이터는 랜덤 ID, S3 객체 키, 만료, 폐기 여부만 저장합니다. 이벤트는 시각·종류·유형·출처만 저장합니다. 요청 전체 크기를 먼저 제한하고 multipart 파싱 단계부터 메모리에서만 처리하며 디스크 임시 파일로 전환하지 않습니다. 정제 처리본은 승인 전 S3에 업로드하지 않습니다.
- Presigned URL은 소지자가 접근하는 링크입니다. 만료는 새 다운로드 요청에 적용되며 이미 내려받은 파일이나 진행 중인 다운로드는 회수할 수 없습니다. 임시 AWS 자격 증명이 먼저 만료되면 링크도 먼저 만료될 수 있습니다.

참조: [FastAPI 파일 및 multipart](https://fastapi.tiangolo.com/tutorial/request-files/), [Boto3 Presigned URL](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/generate_presigned_url.html).

## 검증

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

단위·통합 테스트는 moto S3를 사용하며 AWS 계정이나 실제 파일 전송 비용이 필요하지 않습니다. `deploy/verify_live.py`는 실제 AWS 연결, 서명 링크 다운로드, 단기 링크 만료, 폐기를 검증하고 `deploy/aws/verification.json`에 결과를 저장합니다. 백엔드 HTTPS 배포·휴대폰 다운로드와 20MB 업로드 10초 성능 목표는 별도의 배포 환경에서 확인해야 합니다.
