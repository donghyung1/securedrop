// SecureGuard 패널 (F8)
// - 규칙 기반 문구 + 선택 버튼. 파일명과 탐지 유형·개수만 다루고 원문은 받지 않는다.
// - 자연어 입력(/api/assist)은 Day 4에 추가한다.
import { typeLabel } from "./api.js";

const box = () => document.getElementById("guard-messages");
const btns = () => document.getElementById("guard-buttons");
let actionHandler = () => {};

export function onAction(fn) {
  actionHandler = fn;
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
  box().innerHTML = "";
  say("파일을 선택하면 민감정보를 검사하고 결과를 설명해 드립니다.");
  setButtons([]);
}

// 새 검사 결과가 나오면 대기 안내 문구를 지운다
function clear() {
  box().innerHTML = "";
}

export function showClean(filename) {
  clear();
  say(`${filename}에서 민감정보가 발견되지 않았습니다. 바로 전송할 수 있습니다.`, "ok");
  setButtons([]);
}

export function showFindings(filename, findings) {
  clear();
  const summary = findings.map((f) => `${typeLabel(f.type)} ${f.count}건`).join(", ");
  say(`선택한 ${filename}에서 민감정보가 발견됐습니다. ${summary}. 원본 파일의 전송이 보류되었습니다.`, "danger");
  say("어떻게 처리할까요? 원본은 변경하지 않고 별도 처리본을 만든 뒤 다시 검사합니다.");
  const main = findings[0] ? typeLabel(findings[0].type) : "민감정보";
  setButtons([
    { label: `${findings.length > 1 ? "민감정보" : main} 마스킹`, action: "MASK", variant: "primary" },
    { label: "해당 항목 삭제", action: "DELETE" },
    { label: "전송 취소", action: "CANCEL" },
  ]);
}

export function showDeferred(filename, reason) {
  clear();
  say(`${filename}: ${reason} 민감정보가 발견되면 전송을 보류하고 다시 안내해 드립니다.`, "warn");
  setButtons([]);
}

export function showSent(filename) {
  clear();
  say(`${filename} 전송 링크를 만들었습니다. 휴대폰으로 QR 코드를 스캔하세요. 필요 없어지면 바로 폐기할 수 있습니다.`, "ok");
  setButtons([]);
}

export function showRejected(filename, reason) {
  clear();
  say(`${filename}: ${reason}`, "danger");
  setButtons([{ label: "다른 파일 선택", action: "CANCEL" }]);
}

export function showUnscannable(filename, reason) {
  clear();
  say(`${filename}: ${reason} 내용을 확인한 뒤 전송 여부를 직접 결정해 주세요.`, "warn");
  setButtons([{ label: "전송 취소", action: "CANCEL" }]);
}
