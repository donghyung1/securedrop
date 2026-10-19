// SecureGuard 패널 (F8)
// - 규칙 기반 문구 + 선택 버튼(필수), 자연어 요청 입력(/api/assist).
// - 서버에는 파일명과 탐지 유형·개수만 보낸다. 파일 내용·탐지 원문은 보내지 않는다.
// - 해석 결과는 MASK/DELETE/CANCEL/CLARIFY + 대상 유형뿐이며, 바로 처리하지 않고
//   정제 선택 화면에 채워 사용자가 확인하게 한다 (PRD 14장 "처리 전 선택 내용 확인").
import { api, ApiError, typeLabel } from "./api.js";

const $ = (id) => document.getElementById(id);
const box = () => $("guard-messages");
const btns = () => $("guard-buttons");
let actionHandler = () => {};
let context = null; // { filename, findings } — 민감정보가 있을 때만 자연어 입력을 연다
let asking = false;

// 버튼 클릭은 "MASK" 같은 문자열, 자연어 해석은 { action, targets } 객체로 전달된다
export function onAction(fn) {
  actionHandler = fn;
  $("guard-form").addEventListener("submit", (e) => {
    e.preventDefault();
    ask($("guard-input").value.trim());
  });
}

function setContext(ctx) {
  context = ctx;
  $("guard-form").hidden = !ctx;
  if (!ctx) $("guard-input").value = "";
}

async function ask(message) {
  if (!message || !context || asking) return;
  asking = true;
  $("guard-send").disabled = true;
  $("guard-input").value = "";
  say(message, "user");
  try {
    const res = await api.assist(message, { filename: context.filename.slice(0, 255), findings: context.findings });
    if ((res.action === "MASK" || res.action === "DELETE") && res.targets.length) {
      const names = joinTypes(res.targets);
      say(`${names}${josa(names, "을", "를")} ${res.action === "MASK" ? "마스킹" : "삭제"}하도록 선택해 두었습니다. 선택 내용을 확인하고 "처리본 만들기"를 누르세요. 원본은 변경하지 않습니다.`);
      actionHandler({ action: res.action, targets: res.targets });
    } else if (res.action === "CANCEL") {
      say("전송을 취소할까요? 아래 버튼을 누르면 취소합니다.");
      setButtons([{ label: "전송 취소", action: "CANCEL", variant: "primary" }]);
    } else {
      // CLARIFY: 서버 설명 + 선택 버튼으로 되묻기
      say(res.reply);
      setButtons(findingButtons(context.findings));
    }
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError("CLIENT_ERROR");
    say(`요청을 해석하지 못했습니다. ${err.userMessage} 버튼으로 선택해 주세요.`, "warn");
  } finally {
    asking = false;
    $("guard-send").disabled = false;
  }
}

function say(text, tone = "") {
  const p = document.createElement("p");
  p.className = "bubble" + (tone ? ` bubble-${tone}` : "");
  p.textContent = text;
  box().appendChild(p);
  box().scrollTop = box().scrollHeight;
}

function setButtons(list) {
  btns().innerHTML = "";
  for (const { label, action, variant = "ghost" } of list) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `btn btn-${variant} btn-sm`;
    b.textContent = label;
    b.addEventListener("click", () => actionHandler(action));
    btns().appendChild(b);
  }
}

export function reset() {
  setContext(null);
  box().innerHTML = "";
  say("파일을 선택하면 민감정보를 검사하고 결과를 설명해 드립니다.");
  setButtons([]);
}

// 새 검사 결과가 나오면 대기 안내 문구를 지운다
function clear() {
  box().innerHTML = "";
}

export function showClean(filename) {
  setContext(null);
  clear();
  say(`${filename}에서 민감정보가 발견되지 않았습니다. 바로 전송할 수 있습니다.`, "ok");
  setButtons([]);
}

export function showFindings(filename, findings) {
  setContext({ filename, findings });
  clear();
  const summary = findings.map((f) => `${typeLabel(f.type)} ${f.count}건`).join(", ");
  say(`선택한 ${filename}에서 민감정보가 발견됐습니다. ${summary}. 원본 파일의 전송이 보류되었습니다.`, "danger");
  say("어떻게 처리할까요? 버튼을 누르거나 \"주민등록번호만 가려줘\"처럼 입력하세요. 원본은 변경하지 않고 별도 처리본을 만든 뒤 다시 검사합니다.");
  setButtons(findingButtons(findings));
}

// PRD 예시: [ 주민등록번호 마스킹 ] [ 해당 항목 삭제 ] [ 전송 취소 ]
function findingButtons(findings) {
  const main = findings.length === 1 ? typeLabel(findings[0].type) : "전체";
  return [
    { label: `${main} 마스킹`, action: "MASK", variant: "primary" },
    { label: "해당 항목 삭제", action: "DELETE" },
    { label: "전송 취소", action: "CANCEL" },
  ];
}

const joinTypes = (types) => types.map(typeLabel).join(", ");

// 받침에 따라 조사 선택 (한글이 아니면 병기)
function josa(word, withBatchim, without) {
  const code = word.charCodeAt(word.length - 1);
  if (code < 0xac00 || code > 0xd7a3) return `${withBatchim}(${without})`;
  return (code - 0xac00) % 28 ? withBatchim : without;
}

export function showSelecting(preset) {
  say(
    preset === "DELETE"
      ? "탐지된 항목을 삭제한 처리본을 만들겠습니다. 유형별로 조치를 바꿀 수 있습니다."
      : "탐지된 항목을 마스킹한 처리본을 만들겠습니다. 유형별로 조치를 바꿀 수 있습니다.",
  );
  setButtons([{ label: "전송 취소", action: "CANCEL" }]);
}

// actions: [{action, targets}], res: /api/sanitize 응답
export function showSanitized(actions, res) {
  const done = actions
    .map(({ action, targets }) => {
      const names = joinTypes(targets);
      return `${names}${josa(names, "을", "를")} ${action === "MASK" ? "마스킹" : "삭제"}`;
    })
    .join("하고 ");
  if (res.clean) {
    say(`${done}했습니다. 다시 검사한 결과 남은 민감정보가 없습니다. 미리보기를 확인한 뒤 승인하면 전송합니다.`, "ok");
    setButtons([{ label: "전송 취소", action: "CANCEL" }]);
  } else {
    const left = res.findings_after.map((f) => `${typeLabel(f.type)} ${f.count}건`).join(", ");
    say(`${done}했습니다. 다시 검사했더니 ${left}이 남아 있습니다. 추가로 정제하거나, 위험을 확인한 뒤 보낼 수 있습니다.`, "warn");
    setButtons([
      { label: "남은 항목도 정제", action: "MORE", variant: "primary" },
      { label: "전송 취소", action: "CANCEL" },
    ]);
  }
}

export function showSent(filename) {
  setContext(null);
  clear();
  say(`${filename} 전송 링크를 만들었습니다. 휴대폰으로 QR 코드를 스캔하세요. 필요 없어지면 바로 폐기할 수 있습니다.`, "ok");
  setButtons([]);
}

export function showRejected(filename, reason) {
  setContext(null);
  clear();
  say(`${filename}: ${reason}`, "danger");
  setButtons([{ label: "다른 파일 선택", action: "CANCEL" }]);
}

export function showUnscannable(filename, reason) {
  setContext(null);
  clear();
  say(`${filename}: ${reason} 내용을 확인한 뒤 전송 여부를 직접 결정해 주세요.`, "warn");
  setButtons([{ label: "전송 취소", action: "CANCEL" }]);
}
