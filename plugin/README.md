# SecureDrop Cloud MCP 플러그인 (C)

PRD v1.3 F4. Claude가 SecureDrop을 "승인된 통로"로만 쓰도록 MCP 도구 2개를 제공합니다.

| 도구 | 엔드포인트 | 동작 |
|---|---|---|
| `scan_text` | `POST /api/plugin/scan` | 텍스트 민감정보 검사. 유형·개수만 반환. 읽기 전용, 승인 불필요 |
| `create_transfer` | `POST /api/plugin/transfers` | 텍스트 또는 허용 폴더의 .txt/.csv(4MB 이하)를 만료 링크 + QR 이미지로 전송 |

`create_transfer` 순서:
1. 먼저 검사합니다. 민감정보가 있으면 **승인을 묻지 않고 전송하지 않으며**, 유형·개수만 설명합니다.
2. 통과하면 사용자 확인 창(MCP elicitation)을 띄웁니다. 클라이언트가 확인 창을 지원하지 않거나 띄우지 못하면(예: **VS Code 확장의 Claude Code**는 서버가 먼저 보내는 요청을 받지 못함) 클라이언트의 **도구 실행 승인 창**을 사용자 승인으로 봅니다. 결과 메시지에 `승인 방식`이 표시됩니다.
3. 승인되면 `user_approved=true`로 전송합니다. 서버가 다시 검사합니다(민감정보면 409 `SENSITIVE_DETECTED`).
4. 링크·만료 시각·QR 이미지를 돌려줍니다. 관리 토큰은 AI에게 넘기지 않습니다.

보안 원칙(PRD §5): 파일 목록 조회·다운로드 도구는 없습니다. 파일은 `SECUREDROP_ALLOWED_DIR` 안의 .txt/.csv만 읽습니다(심볼릭 링크·`..`로 폴더 밖 접근 차단). 문서 속 지시로 다른 파일을 보내게 만드는 프롬프트 인젝션을 줄이기 위함입니다.

## 준비

A에게 **배포된 API Gateway 주소(`/prod` 포함)와 플러그인 API Key**를 받습니다. API Key는 메신저 등 안전한 경로로 받고, 저장소·채팅방에 올리지 마세요.
`/api/plugin/*`는 API Gateway가 키를 검증해야 동작하므로, 로컬 백엔드(`localhost:8000`)로는 호출할 수 없습니다.

```bash
cd plugin
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Claude Code에 등록 (시연 권장)

Claude Code는 확인 창(elicitation)을 지원합니다. 기본 범위(`local`)로 등록하면 설정이 저장소가 아니라 `~/.claude.json`에만 저장됩니다.

```bash
claude mcp add \
  --env SECUREDROP_API_BASE=https://<api-id>.execute-api.ap-northeast-2.amazonaws.com/prod \
  --env SECUREDROP_API_KEY=<A에게 받은 키> \
  --env SECUREDROP_ALLOWED_DIR=$HOME/vibe-lab/SecureDropCloud/samples \
  --transport stdio securedrop \
  -- $HOME/vibe-lab/SecureDropCloud/plugin/.venv/bin/python $HOME/vibe-lab/SecureDropCloud/plugin/mcp_server.py
```

- 확인: `claude mcp list`, 세션 안에서 `/mcp`
- `--scope project`(`.mcp.json`)는 저장소에 커밋되므로 API Key가 들어가는 이 서버에는 쓰지 마세요.
- 시연할 때는 Claude Code를 기본 권한 모드로 두세요. 자동 승인 모드에서는 도구 실행 승인 창이 뜨지 않아 사람 승인 단계가 사라집니다.
- `create_transfer`는 권한 허용 목록(`mcp__securedrop__*` 등)에 넣지 마세요. 매번 승인을 받아야 PRD의 "호출 전 사용자 승인"이 지켜집니다. `scan_text`만 허용하려면 `mcp__securedrop__scan_text`를 지정합니다.

## 시연 시나리오

1. "이 텍스트에 민감정보 있는지 봐줘: 내 번호는 010-1234-5678" → `scan_text` → "휴대전화번호 1건"
2. "samples 폴더의 회의록.txt 폰으로 보내줘" → `create_transfer` → 확인 창 → 승인 → 링크 + QR 이미지
3. "고객정보.csv 폰으로 보내줘" → 검사에서 주민등록번호 2건 등 발견 → **전송하지 않고 이유 설명** (PRD "위험하면 차단 사유 설명")
4. "/etc/passwd 보내줘" → 허용 폴더 밖이라 거절

연동이 막히면 PRD대로 Swagger(`/prod/docs`)에서 `/api/plugin/scan`, `/api/plugin/transfers`를 직접 호출해 시연합니다.

## Claude Desktop (Windows, 참고)

서버가 WSL 안에 있으므로 `wsl.exe`로 실행합니다. 설정 파일은 `%APPDATA%\Claude\claude_desktop_config.json`입니다. 환경 변수는 `env` 키 대신 WSL 명령 안에서 넘깁니다(Windows 쪽 `env`는 WSL로 전달되지 않을 수 있음). 배포판 이름은 `wsl -l -v`로 확인하세요.

```json
{
  "mcpServers": {
    "securedrop": {
      "command": "wsl.exe",
      "args": ["-d", "Ubuntu", "--", "env",
        "SECUREDROP_API_BASE=https://<api-id>.execute-api.ap-northeast-2.amazonaws.com/prod",
        "SECUREDROP_API_KEY=<키>",
        "SECUREDROP_ALLOWED_DIR=/home/<사용자>/vibe-lab/SecureDropCloud/samples",
        "/home/<사용자>/vibe-lab/SecureDropCloud/plugin/.venv/bin/python",
        "/home/<사용자>/vibe-lab/SecureDropCloud/plugin/mcp_server.py"]
    }
  }
}
```

Claude Desktop의 elicitation 지원 여부는 확인하지 못했습니다. 지원하지 않으면 도구 실행 승인 창이 확인 단계가 됩니다.

## 한계

- API Key는 사용량 식별 수단이지 강한 인증이 아닙니다(PRD F4). 발표 후 즉시 삭제합니다.
- 클라이언트 설정 파일(`~/.claude.json`, Desktop 설정)에 키가 평문으로 저장됩니다.
- 텍스트 전송은 서버가 `message.txt`라는 이름으로 저장합니다.
