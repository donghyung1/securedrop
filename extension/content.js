// ChatGPT / Claude 입력창의 전송 버튼 클릭·Enter를 가로채 /api/scan으로 검사한다.
// AI 사이트 UI가 바뀌면 아래 SITES의 선택자만 고치면 된다.

const SITES = {
  "chatgpt.com": {
    input: "#prompt-textarea",
    send: 'button[data-testid="send-button"]',
  },
  "chat.openai.com": {
    input: "#prompt-textarea",
    send: 'button[data-testid="send-button"]',
  },
  "claude.ai": {
    input: 'div[contenteditable="true"].ProseMirror, div[contenteditable="true"][role="textbox"]',
    send: 'button[aria-label="Send message"], button[aria-label="메시지 보내기"]',
  },
};

const TYPE_LABELS = {
  RRN: "주민등록번호",
  PHONE: "휴대전화번호",
  EMAIL: "이메일",
  AWS_ACCESS_KEY: "AWS Access Key",
  API_KEY: "API Key·토큰",
  CARD: "카드번호",
};

const site = SITES[location.hostname];
let busy = false; // 검사 중 중복 처리 방지
let bypass = false; // 검사를 통과한 전송을 다시 흘려보낼 때만 true

function inputOf(target) {
  return site && target instanceof Element ? target.closest(site.input) : null;
}

function readText(input) {
  return input instanceof HTMLTextAreaElement || input instanceof HTMLInputElement
    ? input.value
    : input.innerText;
}

function sendButton() {
  return site ? document.querySelector(site.send) : null;
}

async function serverUrl() {
  try {
    const { serverUrl } = await chrome.storage.sync.get({ serverUrl: "http://localhost:8000" });
    return String(serverUrl).replace(/\/+$/, "");
  } catch {
    return "http://localhost:8000";
  }
}

async function failClosed() {
  try {
    return (await chrome.storage.sync.get({ failClosed: true })).failClosed !== false;
  } catch {
    return true;
  }
}

function send(message) {
  return chrome.runtime.sendMessage(message).catch(() => ({ ok: false }));
}

// 사이트 CSS와 섞이지 않도록 shadow DOM 안에 팝업을 만든다.
async function showPopup({ title, body, types = [], safeLink = true }) {
  document.getElementById("securedrop-guard-root")?.remove();
  const host = document.createElement("div");
  host.id = "securedrop-guard-root";
  const root = host.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = `
    .overlay{position:fixed;inset:0;z-index:2147483647;background:rgba(0,0,0,.45);display:flex;
      align-items:center;justify-content:center;font-family:system-ui,"Malgun Gothic",sans-serif}
    .card{background:#fff;color:#1f2328;width:min(420px,calc(100vw - 32px));border-radius:12px;
      padding:24px;box-shadow:0 12px 40px rgba(0,0,0,.3)}
    h2{margin:0 0 8px;font-size:18px;color:#b42318}
    p{margin:0 0 12px;font-size:14px;line-height:1.5}
    ul{margin:0 0 16px;padding-left:20px;font-size:14px}
    .row{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap}
    a,button{font-size:14px;border-radius:8px;padding:9px 14px;cursor:pointer;text-decoration:none;
      border:1px solid #d0d7de;background:#f6f8fa;color:#1f2328}
    a.primary{background:#1a7f37;border-color:#1a7f37;color:#fff}`;
  const overlay = document.createElement("div");
  overlay.className = "overlay";
  const card = document.createElement("div");
  card.className = "card";
  card.setAttribute("role", "alertdialog");
  const h2 = document.createElement("h2");
  h2.textContent = title;
  const p = document.createElement("p");
  p.textContent = body;
  card.append(h2, p);
  if (types.length) {
    const ul = document.createElement("ul");
    for (const type of types) {
      const li = document.createElement("li");
      li.textContent = TYPE_LABELS[type] ?? type;
      ul.append(li);
    }
    card.append(ul);
  }
  const row = document.createElement("div");
  row.className = "row";
  if (safeLink) {
    const link = document.createElement("a");
    link.className = "primary";
    link.textContent = "안전하게 전송하기";
    link.href = (await serverUrl()) + "/";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    row.append(link);
  }
  const close = document.createElement("button");
  close.textContent = "닫기";
  close.addEventListener("click", () => host.remove());
  row.append(close);
  card.append(row);
  overlay.append(card);
  root.append(style, overlay);
  document.documentElement.append(host);
  close.focus();
}

function release(input) {
  // 검사를 통과한 전송을 한 번만 그대로 흘려보낸다.
  bypass = true;
  const button = sendButton();
  if (button) {
    button.click();
  } else {
    input.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }),
    );
  }
  setTimeout(() => (bypass = false), 0);
}

async function guard(input) {
  const text = readText(input);
  if (!text.trim()) return release(input);
  const result = await send({ kind: "scan", text });
  if (result?.ok && !result.blocked) return release(input);
  if (result?.ok && result.blocked) {
    // 유형·시간만 서버에 보낸다. 원문은 보내지 않는다.
    send({ kind: "log", types: result.types });
    return showPopup({
      title: "전송이 차단되었습니다",
      body: "사내 기밀/개인정보 입력이 감지되어 차단되었습니다",
      types: result.types,
    });
  }
  // 서버 무응답·오류: 기본은 차단(fail-closed), 설정으로 허용 가능
  if (await failClosed()) {
    return showPopup({
      title: "검사 서버에 연결할 수 없습니다",
      body: "안전을 위해 전송을 막았습니다. 서버 상태와 확장 설정을 확인한 뒤 다시 시도하세요.",
      safeLink: false,
    });
  }
  release(input);
}

function intercept(event, input) {
  event.preventDefault();
  event.stopImmediatePropagation();
  if (busy) return;
  busy = true;
  guard(input).finally(() => (busy = false));
}

if (site) {
  document.addEventListener(
    "keydown",
    (event) => {
      if (bypass || event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
      const input = inputOf(event.target);
      if (input) intercept(event, input);
    },
    true,
  );
  document.addEventListener(
    "click",
    (event) => {
      if (bypass || !(event.target instanceof Element)) return;
      if (!event.target.closest(site.send)) return;
      const input = document.querySelector(site.input);
      if (input) intercept(event, input);
    },
    true,
  );
}
