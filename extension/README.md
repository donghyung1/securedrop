# SecureDrop Guard (크롬 확장, PRD v1.3)

ChatGPT(`chatgpt.com`) 또는 Claude(`claude.ai`) 입력창에서 전송 버튼 클릭·Enter를 가로채 `/api/scan`으로 검사합니다. 감지되면 전송을 막고 "안전하게 전송하기" 링크(CloudFront 웹)가 있는 팝업을 띄우며, 유형과 시간만 `POST /api/ext/logs`로 보냅니다. 출처(`extension`)는 서버가 고정하므로 요청 본문에 `source`를 넣지 않습니다(알 수 없는 필드는 422).

## 설치

1. API를 실행하거나 배포 주소를 확인합니다. (`backend/README.md` 참고)
2. `chrome://extensions` → 개발자 모드 켜기 → **압축해제된 확장 프로그램을 로드합니다** → 이 `extension/` 폴더 선택
3. 이 확장은 `manifest.json`의 공개키로 ID를 `cbamkobbnpomdhcfndaenamfpoldkcno`로 고정합니다. 자동 배포는 이 키에서 origin을 계산하여 Lambda `ALLOWED_ORIGINS`에 추가합니다. 로컬은 `backend/.env`에 `chrome-extension://cbamkobbnpomdhcfndaenamfpoldkcno`를 추가하고 서버를 재시작합니다. 기존에 키 없이 설치했다면 확장을 제거한 뒤 다시 로드하고 ID를 확인하세요. 기존 확장 설정은 다시 입력해야 할 수 있습니다.

공개키는 압축해제 설치의 ID 유지용이며 비밀값이 아닙니다. Chrome 웹 스토어 배포 시에는 스토어에서 부여한 공개키로 교체하고 origin을 다시 배포해야 합니다. [Chrome 문서](https://developer.chrome.com/docs/extensions/reference/manifest/key)를 참고하세요.
4. 툴바 아이콘 → **API 주소**(`https://<api-id>.execute-api.ap-northeast-2.amazonaws.com/prod`)와 **웹 주소**(CloudFront)를 입력하고 **저장 후 연결 확인**을 누릅니다.
   기본값은 `config.js` 한 곳에서 바꿉니다(로컬 `http://localhost:8000` / `http://localhost:5500`).

## 동작 요약

- 검사는 `background.js`(service worker)가 호출합니다. AI 사이트 Origin으로는 CORS에 막히기 때문입니다.
- 서버 무응답·오류 시 기본은 **전송 차단(fail-closed)** 입니다. 팝업 설정에서 해제할 수 있습니다. 콜드 스타트 대비 타임아웃은 5초입니다.
- 50,000자를 넘는 입력은 겹치는 조각으로 나눠 검사합니다.
- 원문은 저장하지 않으며 서버에도 로그로 보내지 않습니다(검사 요청 본문 제외). 차단 로그에는 유형만 담깁니다.

## 사이트 UI가 바뀌었을 때

`content.js` 맨 위 `SITES`의 선택자(`input`, `send`)만 수정하면 됩니다.

## 알려진 한계

- AI 사이트 DOM 변경 시 가로채기가 실패할 수 있습니다.
- 다른 API 도메인을 쓰려면 `manifest.json`의 `host_permissions`에 해당 주소를 추가해야 합니다.
- 파일 첨부 검사는 이 확장에 포함하지 않았습니다. 파일은 웹 화면에서 검사합니다.
- 배포용 zip(`/download/securedrop-extension.zip`)은 `extension/` 폴더를 압축해 웹 버킷에 올립니다(C와 배포 시점 조율).
