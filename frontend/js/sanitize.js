// 정제 (PRD v1.3 F7, 여정 3·4)
// 유형별 조치 선택 → /api/sanitize (원본 + actions) → 처리본·재검사 결과·미리보기 표시
// - 처리는 서버의 검증된 코드(sanitizer.py)가 한다. 브라우저는 결과(content_b64)를 보관만 한다.
// - 처리본은 항상 원본에서 다시 만든다 ("추가 정제"도 원본 + 늘어난 조치).
// - 미리보기에는 남은 민감정보가 있을 수 있으므로 화면에만 그리고 어디에도 기록하지 않는다.
import { api, ApiError, typeLabel } from "./api.js";

const $ = (id) => document.getElementById(id);

// 서버 sanitizer.py의 마스킹 결과 예시
const MASK_HINT = { RRN: "900101-1******", PHONE: "010-****-5678" };
const maskHint = (type) => MASK_HINT[type] || "[REDACTED]";

let ctx = { file: null, findings: [], choices: {}, onResult: null, onCancel: null };
let busy = false;

export function initSanitize({ onResult, onCancel }) {
  ctx.onResult = onResult;
  ctx.onCancel = onCancel;
  $("btn-sanitize").addEventListener("click", run);
  $("btn-sanitize-cancel").addEventListener("click", () => !busy && ctx.onCancel());
  $("btn-resanitize").addEventListener("click", () => openSelect(ctx.file, ctx.findings, ctx.choices));
}

// findings: 원본 검사 결과 [{type, count}]
// preset: "MASK" | "DELETE" (모든 유형에 적용) 또는 { TYPE: "MASK" | "DELETE" | "SKIP" }
export function openSelect(file, findings, preset) {
  ctx.file = file;
  ctx.findings = findings;
  const choices = {};
  for (const f of findings) {
    choices[f.type] = typeof preset === "string" ? preset : preset?.[f.type] || "SKIP";
  }
  ctx.choices = choices;

  const list = $("sanitize-types");
  list.innerHTML = "";
  for (const f of findings) {
    const li = document.createElement("li");
    const label = document.createElement("label");
    label.htmlFor = `act-${f.type}`;
    const name = document.createElement("strong");
    name.textContent = typeLabel(f.type);
    const count = document.createElement("span");
    count.className = "muted";
    count.textContent = ` ${f.count}건`;
    label.append(name, count);

    const select = document.createElement("select");
    select.id = `act-${f.type}`;
    select.add(new Option(`마스킹 (${maskHint(f.type)})`, "MASK"));
    select.add(new Option("삭제", "DELETE"));
    select.add(new Option("그대로 두기", "SKIP"));
    select.value = choices[f.type];
    select.addEventListener("change", () => {
      ctx.choices[f.type] = select.value;
      updateButton();
    });

    li.append(label, select);
    list.appendChild(li);
  }

  $("sanitize-error").hidden = true;
  $("preview-card").hidden = true;
  $("sanitize-card").hidden = false;
  updateButton();
}

export function hideSanitize() {
  $("sanitize-card").hidden = true;
  $("preview-card").hidden = true;
}

// 서버 형식: [{action, targets}] — 유형마다 조치 하나, 빈 그룹은 보내지 않는다
function collectActions() {
  const groups = { MASK: [], DELETE: [] };
  for (const [type, choice] of Object.entries(ctx.choices)) {
    if (groups[choice]) groups[choice].push(type);
  }
  return Object.entries(groups)
    .filter(([, targets]) => targets.length)
    .map(([action, targets]) => ({ action, targets }));
}

function updateButton() {
  const btn = $("btn-sanitize");
  btn.disabled = busy || collectActions().length === 0;
  btn.textContent = busy ? "처리 중…" : "처리본 만들기";
}

async function run() {
  const actions = collectActions();
  if (!actions.length) return;
  busy = true;
  updateButton();
  $("sanitize-error").hidden = true;
  try {
    const res = await api.sanitize(ctx.file, actions);
    showPreview(res);
    ctx.onResult(res, actions);
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError("INTERNAL_ERROR");
    $("sanitize-error").textContent = err.userMessage;
    $("sanitize-error").hidden = false;
  } finally {
    busy = false;
    updateButton();
  }
}

function showPreview(res) {
  const status = $("recheck-status");
  const list = $("recheck-findings");
  list.innerHTML = "";
  if (res.clean) {
    status.className = "status is-ok";
    status.textContent = "다시 검사한 결과 남은 민감정보가 없습니다.";
  } else {
    status.className = "status is-danger";
    status.textContent = "다시 검사한 결과 아래 민감정보가 남아 있습니다.";
    for (const f of res.findings_after) {
      const li = document.createElement("li");
      li.innerHTML = "<span></span><strong></strong>";
      li.children[0].textContent = typeLabel(f.type);
      li.children[1].textContent = `${f.count}건`;
      list.appendChild(li);
    }
  }
  $("preview-text").textContent = res.preview || "(내용 없음)";
  $("sanitize-card").hidden = true;
  $("preview-card").hidden = false;
}

// "남은 항목도 정제": 이전 선택을 유지하고 남은 유형을 마스킹으로 바꿔 선택 화면을 다시 연다
export function reopenWithRemaining(remaining) {
  const preset = { ...ctx.choices };
  for (const f of remaining) if (preset[f.type] === "SKIP" || !preset[f.type]) preset[f.type] = "MASK";
  openSelect(ctx.file, ctx.findings, preset);
}
