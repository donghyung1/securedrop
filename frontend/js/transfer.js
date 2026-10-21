// 다중 파일 전송의 순수 로직 (DOM·네트워크 없음 → node --test 로 검증)
// - 선택 한도 검사, 전송 요청 본문(파일별 scan_id/sanitize_id)·전송 단위 확인 플래그 계산, 동시 업로드
const MB = 1024 * 1024;

export const extOf = (name) => (name.split(".").pop() || "").toLowerCase();
export const mimeOf = (file) => file.type || "application/octet-stream";

export const isScannable = (name, cfg) => cfg.SCANNABLE_EXTS.includes(extOf(name));

// 검사 대상(.txt/.csv)이 하나라도 있으면 종단간 암호화는 전송 전체에서 쓸 수 없다 (서버가 평문 해시로 검증)
export const blocksE2ee = (files, cfg) => files.some((f) => isScannable(f.name, cfg));

const sameFile = (a, b) => a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;

// current: 이미 목록에 있는 File[] (처리본이 있으면 처리본 크기로 바꿔 넘겨도 된다)
// 더할 수 있으면 null, 아니면 토스트에 보여줄 사유
export function checkAddition(current, file, cfg) {
  const ext = extOf(file.name);
  if (!cfg.ALLOWED_EXTS.includes(ext)) return `${file.name}: .${ext} 형식은 업로드할 수 없습니다.`;
  if (file.size === 0) return `${file.name}: 빈 파일은 보낼 수 없습니다.`;
  if (current.some((f) => sameFile(f, file))) return `${file.name}: 이미 추가한 파일입니다.`;
  if (current.length >= cfg.MAX_FILES) return `파일은 한 번에 최대 ${cfg.MAX_FILES}개까지 보낼 수 있습니다.`;
  if (file.size > cfg.MAX_FILE_MB * MB) return `${file.name}: 파일이 ${cfg.MAX_FILE_MB}MB를 초과합니다.`;
  const total = current.reduce((n, f) => n + f.size, 0) + file.size;
  if (total > cfg.MAX_FILE_MB * MB) return `${file.name}: 파일 합계가 ${cfg.MAX_FILE_MB}MB를 초과합니다.`;
  return null;
}

// entries: [{ file, scan, scanResult, processed }]
//   processed(승인된 처리본): { res(/api/sanitize 응답), blob, name, type }
// → items: 파일별 업로드 대상 + 서버 참조, flags: 전송 전체에 걸리는 확인 플래그
//    (하나라도 필요하면 전송 전체에 true — 서버는 payload 단위로만 받는다)
export function planTransfer(entries) {
  const items = entries.map(({ file, scanResult, processed }) => {
    if (processed) {
      return {
        blob: processed.blob,
        name: processed.name,
        type: processed.type,
        ref: { sanitize_id: processed.res.sanitize_id },
        expectedSha256: processed.res.sha256,
      };
    }
    return {
      blob: file,
      name: file.name,
      type: mimeOf(file),
      ref: scanResult ? { scan_id: scanResult.scan_id } : {},
      expectedSha256: scanResult?.sha256,
    };
  });
  const flags = {
    acknowledge_unsupported: entries.some((e) => !e.processed && e.scan === "unscannable"),
    acknowledge_risk: entries.some((e) => (e.processed ? !e.processed.res.clean : e.scan === "blocked")),
    user_approved: entries.some((e) => e.processed),
  };
  return { items, flags };
}

// payloads: 실제로 올릴 Blob(암호화했다면 암호문)과 그 sha256 — [{ size, sha256 }]
export function buildCreateBody({ items, payloads, expiresIn, encrypted, flags }) {
  return {
    files: items.map((it, i) => ({
      name: it.name,
      size: payloads[i].size,
      type: it.type,
      sha256: payloads[i].sha256,
      ...it.ref, // 파일별 scan_id | sanitize_id
    })),
    expires_in: expiresIn,
    encrypted,
    ...flags,
  };
}

// uploads[i] 에 payloads[i] 를 올린다. 최대 concurrency 개 동시, 하나라도 실패하면 새 업로드를 시작하지 않고 그 오류를 던진다.
// onProgress(0~1): 바이트 기준 전체 진행률
export async function uploadAll(uploads, payloads, upload, onProgress, concurrency = 3) {
  if (uploads.length !== payloads.length) throw new Error("UPLOAD_COUNT_MISMATCH");
  const total = payloads.reduce((n, p) => n + p.size, 0);
  const sent = payloads.map(() => 0);
  const report = () => onProgress?.(total ? sent.reduce((a, b) => a + b, 0) / total : 1);
  let next = 0;
  let failure = null;
  const worker = async () => {
    while (!failure && next < payloads.length) {
      const i = next++;
      try {
        await upload(uploads[i], payloads[i], (r) => { sent[i] = r * payloads[i].size; report(); });
        sent[i] = payloads[i].size;
        report();
      } catch (e) {
        failure ??= { error: e };
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, payloads.length) }, worker));
  if (failure) throw failure.error;
}
