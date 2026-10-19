// 관리 토큰 보관 (PRD v1.3 F9·§10.6)
// - POST /api/transfers 응답에 한 번만 오는 manage_token을 이 탭의 sessionStorage에 둔다.
// - 조회·완료·폐기 요청에 X-Manage-Token 헤더로 보낸다.
// - 탭을 닫으면 사라진다. 이 경우 전송은 만료(최대 60분)와 S3 자동 삭제로 정리된다.
// - F11(내 전송 목록)을 켜면 localStorage로 바꾼다.

const KEY = "sd.transfers";

function load() {
  try {
    return JSON.parse(sessionStorage.getItem(KEY)) || {};
  } catch {
    return {};
  }
}

function save(all) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(all));
  } catch { /* 저장 실패해도 현재 화면의 전송은 메모리 값으로 계속 동작 */ }
}

// 메모리 사본: 저장소가 막혀도 현재 페이지에서는 폐기할 수 있게
const memory = {};

export function saveToken(id, token, expiresAt = null) {
  memory[id] = { token, expires_at: expiresAt };
  const all = load();
  all[id] = { token, expires_at: expiresAt };
  save(all);
}

export function setExpiry(id, expiresAt) {
  if (memory[id]) memory[id].expires_at = expiresAt;
  const all = load();
  if (all[id]) {
    all[id].expires_at = expiresAt;
    save(all);
  }
}

export function getToken(id) {
  return memory[id]?.token || load()[id]?.token || null;
}

export function removeToken(id) {
  delete memory[id];
  const all = load();
  delete all[id];
  save(all);
}
