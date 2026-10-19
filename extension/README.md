# SecureDrop Guard (크롬 확장)

ChatGPT(`chatgpt.com`) 또는 Claude(`claude.ai`) 입력창에서 전송 버튼 클릭·Enter를 가로채 `/api/scan`으로 검사합니다. 감지되면 전송을 막고 "안전하게 전송하기" 링크가 있는 팝업을 띄우며, 유형과 시간만 `/api/logs`로 보냅니다.

## 설치

1. 서버를 실행합니다. (`backend/README.md` 참고)
2. `chrome://extensions` → 개발자 모드 켜기 → **압축해제된 확장 프로그램을 로드합니다** → 이 `extension/` 폴더 선택
3. 로드된 확장의 **ID**를 복사합니다.
4. `backend/.env`의 `ALLOWED_ORIGINS`에 추가하고 서버를 재시작합니다. (확장의 요청 Origin은 `chrome-extension://<ID>`입니다.)
   ```
   ALLOWED_ORIGINS=["http://localhost:5500","http://localhost:8000","http://127.0.0.1:5500","http://127.0.0.1:8000","chrome-extension://<ID>"]
   ```
5. 툴바의 확장 아이콘을 눌러 서버 주소(기본 `http://localhost:8000`)를 확인하고 **저장 후 연결 확인**을 누릅니다.

## 동작 요약

- 검사는 `background.js`(service worker)가 `X-Source: extension` 헤더로 호출합니다. AI 사이트 Origin으로는 CORS에 막히기 때문입니다.
- 서버 무응답·오류 시 기본은 **전송 차단(fail-closed)** 입니다. 팝업 설정에서 해제할 수 있습니다.
- 50,000자를 넘는 입력은 겹치는 조각으로 나눠 검사합니다.
- 원문은 저장하지 않으며 서버에도 로그로 보내지 않습니다(검사 요청 본문 제외).

## 사이트 UI가 바뀌었을 때

`content.js` 맨 위 `SITES`의 선택자(`input`, `send`)만 수정하면 됩니다.

## 알려진 한계

- AI 사이트 DOM 변경 시 가로채기가 실패할 수 있습니다.
- HTTPS 서버를 쓰려면 `manifest.json`의 `host_permissions`에 해당 주소를 추가해야 합니다.
- 파일 첨부 검사는 이 확장에 포함하지 않았습니다. 파일은 웹 화면(`/`)에서 검사합니다.
