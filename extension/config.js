// 로컬/배포 전환은 이 한 곳에서 한다. (PRD 10.12)
// apiBase: API Gateway 주소(https://<api-id>.execute-api.ap-northeast-2.amazonaws.com/prod)
// webBase: CloudFront 웹 주소(https://dxxxx.cloudfront.net). "안전하게 전송하기" 링크 대상.
// 팝업에서 저장한 값이 있으면 그 값이 우선한다. AWS 키·API Key는 넣지 않는다.
const SD_CONFIG = {
  apiBase: "http://localhost:8000",
  webBase: "http://localhost:5500",
  failClosed: true,
};
