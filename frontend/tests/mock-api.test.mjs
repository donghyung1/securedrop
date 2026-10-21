// 실행: cd frontend && node --test tests/mock-api.test.mjs   (Node 20+)
// mock API가 서버(backend/main.py create_transfer)처럼 다중 파일을 검증·처리하는지 확인한다.
import test from "node:test";
import assert from "node:assert/strict";

// api.js·settings.js 는 브라우저 전역을 읽는다 → import 전에 최소한만 흉내 낸다.
const memory = new Map();
const define = (name, value) => Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
define("window", { SD_CONFIG: { defaultApiMode: "mock" } });
define("location", { search: "", href: "http://localhost/index.html" });
define("localStorage", {
  getItem: (k) => (memory.has(k) ? memory.get(k) : null),
  setItem: (k, v) => memory.set(k, String(v)),
  removeItem: (k) => memory.delete(k),
});

const { api, CONFIG, ApiError, sha256Hex } = await import("../js/api.js");
const { generateKey, encryptBlob, decryptBuffer } = await import("../js/crypto.js");
const { planTransfer, buildCreateBody, uploadAll } = await import("../js/transfer.js");

const MB = 1024 * 1024;
const text = (name, body, type = "text/plain") => new File([body], name, { type });
const pdf = (name, body) => new File([`%PDF-1.4\n${body}`], name, { type: "application/pdf" });
const meta = async (blob, name, extra = {}) => ({
  name, size: blob.size, type: blob.type || "application/octet-stream", sha256: await sha256Hex(blob), ...extra,
});
const base = { expires_in: 600, encrypted: false, acknowledge_risk: false, acknowledge_unsupported: false, user_approved: false };
const rejects = (promise, code, status) => assert.rejects(promise, (e) => e instanceof ApiError && e.code === code && (!status || e.status === status));

test("mock 설정: 최대 5개·합계 20MB", () => {
  assert.equal(CONFIG.USE_MOCK, true);
  assert.equal(CONFIG.MAX_FILES, 5);
  assert.equal(CONFIG.MAX_FILE_MB, 20);
});

test("txt(검사)·csv(정제본)·pdf(검사 미지원) 3개를 한 번에 올리고 받는다", async () => {
  const clean = text("회의록.txt", "안건 1\n안건 2\n");
  const risky = text("고객.csv", "이름,주민번호\n홍길동,900101-1234567\n", "text/csv");
  const doc = pdf("문서.pdf", "hello");

  const scanClean = await api.scanFile(clean);
  const scanRisky = await api.scanFile(risky);
  assert.equal(scanClean.blocked, false);
  assert.equal(scanRisky.blocked, true);
  const san = await api.sanitize(risky, [{ action: "MASK", targets: ["RRN"] }]);
  assert.equal(san.clean, true);

  const processed = {
    res: san,
    blob: new Blob([Uint8Array.from(atob(san.content_b64), (c) => c.charCodeAt(0))], { type: "text/csv" }),
    name: "고객_정제본.csv",
    type: "text/csv",
  };
  const entries = [
    { file: clean, scan: "clean", scanResult: scanClean },
    { file: risky, scan: "blocked", scanResult: scanRisky, processed },
    { file: doc, scan: "unscannable", scanResult: null },
  ];
  const { items, flags } = planTransfer(entries);
  const payloads = [];
  for (const it of items) payloads.push({ blob: it.blob, size: it.blob.size, sha256: await sha256Hex(it.blob) });

  const created = await api.createTransfer(buildCreateBody({ items, payloads, expiresIn: 600, encrypted: false, flags }));
  assert.equal(created.uploads.length, 3);
  assert.equal(new Set(created.uploads.map((u) => u.fields.key)).size, 3);
  await uploadAll(created.uploads, payloads, (u, p, onP) => api.uploadToS3(u, p.blob, onP));
  const done = await api.completeTransfer(created.id, created.manage_token);
  assert.equal(done.status, "ACTIVE");

  const dl = await api.getDownload(created.id);
  assert.deepEqual(dl.files.map((x) => x.name), ["회의록.txt", "고객_정제본.csv", "문서.pdf"]);
  assert.equal(dl.encrypted, false);
  const body = await (await fetch(dl.files[1].url)).text();
  assert.ok(body.includes("900101-1******") && !body.includes("1234567"));

  await api.revokeTransfer(created.id, created.manage_token);
  await rejects(api.getDownload(created.id), "NOT_FOUND");
});

test("파일 6개·합계 20MiB 초과·payload 단위 참조(파일 2개 이상)는 서버처럼 거부한다", async () => {
  const doc = pdf("a.pdf", "x");
  const m = await meta(doc, "a.pdf");
  const ack = { ...base, acknowledge_unsupported: true };
  await rejects(api.createTransfer({ ...ack, files: Array(6).fill(m) }), "VALIDATION_ERROR", 422);
  await rejects(api.createTransfer({ ...ack, files: [] }), "VALIDATION_ERROR", 422);
  await rejects(
    api.createTransfer({ ...ack, files: [{ ...m, size: 10 * MB }, { ...m, size: 10 * MB + 1 }] }),
    "FILE_TOO_LARGE", 413,
  );
  await rejects(api.createTransfer({ ...ack, files: [m, m], scan_id: "x" }), "VALIDATION_ERROR", 422);
  await rejects(api.createTransfer({ ...ack, files: [{ ...m, scan_id: "x", sanitize_id: "y" }] }), "VALIDATION_ERROR", 422);
  // 정확히 5개·합계 20MiB 는 허용
  const ok = await api.createTransfer({ ...ack, files: [m, m, m, m, { ...m, size: 20 * MB - 4 * m.size }] });
  assert.equal(ok.uploads.length, 5);
});

test("파일마다 검증한다: 참조 누락·해시 불일치·민감정보 확인·미지원 확인·E2EE+txt", async () => {
  const clean = text("c.txt", "hello");
  const risky = text("r.txt", "연락처 010-1234-5678");
  const sc = await api.scanFile(clean);
  const sr = await api.scanFile(risky);
  const doc = pdf("d.pdf", "x");
  const mc = await meta(clean, "c.txt", { scan_id: sc.scan_id });
  const mr = await meta(risky, "r.txt", { scan_id: sr.scan_id });
  const md = await meta(doc, "d.pdf");

  // 두 번째 .txt 에 검사 참조가 없음
  await rejects(api.createTransfer({ ...base, files: [mc, { ...mr, scan_id: undefined }] }), "VALIDATION_ERROR", 422);
  // 미지원 형식 확인 없음
  await rejects(api.createTransfer({ ...base, files: [mc, md] }), "VALIDATION_ERROR", 422);
  // 민감정보 원본은 acknowledge_risk 가 있어야 한다
  await rejects(api.createTransfer({ ...base, files: [mc, mr] }), "SENSITIVE_REMAINS", 409);
  // 한 파일이라도 해시가 세션과 다르면 거부
  await rejects(
    api.createTransfer({ ...base, acknowledge_risk: true, files: [mc, { ...mr, sha256: "0".repeat(64) }] }),
    "INTEGRITY_MISMATCH", 409,
  );
  // 검사 대상이 섞이면 E2EE 불가
  await rejects(api.createTransfer({ ...base, encrypted: true, acknowledge_unsupported: true, files: [mc, md] }), "VALIDATION_ERROR", 422);
  // 전부 갖추면 통과
  const ok = await api.createTransfer({ ...base, acknowledge_risk: true, acknowledge_unsupported: true, files: [mc, mr, md] });
  assert.equal(ok.uploads.length, 3);
});

test("한 파일이라도 업로드가 빠졌거나 바뀌면 complete 가 실패한다", async () => {
  const a = pdf("a.pdf", "aaa");
  const b = pdf("b.pdf", "bbb");
  const ack = { ...base, acknowledge_unsupported: true };
  const ma = await meta(a, "a.pdf");
  const mb = await meta(b, "b.pdf");

  // 두 번째만 업로드 → UPLOAD_INCOMPLETE (재시도 가능)
  const t1 = await api.createTransfer({ ...ack, files: [ma, mb] });
  await api.uploadToS3(t1.uploads[0], a);
  await rejects(api.completeTransfer(t1.id, t1.manage_token), "UPLOAD_INCOMPLETE", 409);
  await api.uploadToS3(t1.uploads[1], b);
  assert.equal((await api.completeTransfer(t1.id, t1.manage_token)).status, "ACTIVE");

  // 두 번째가 선언과 다른 내용(같은 크기) → INTEGRITY_MISMATCH, 전송 전체 폐기
  const t2 = await api.createTransfer({ ...ack, files: [ma, mb] });
  await api.uploadToS3(t2.uploads[0], a);
  await api.uploadToS3(t2.uploads[1], pdf("b.pdf", "bXb"));
  await rejects(api.completeTransfer(t2.id, t2.manage_token), "INTEGRITY_MISMATCH", 409);
  await rejects(api.getDownload(t2.id), "NOT_FOUND");
});

test("E2EE: 키 하나로 파일마다 암호화하고, 받는 쪽이 파일마다 복호화한다", async () => {
  const files = [pdf("a.pdf", "첫 번째"), pdf("b.pdf", "두 번째 파일")];
  const { key, encoded } = await generateKey();
  const payloads = [];
  for (const file of files) {
    const blob = await encryptBlob(file, key);
    payloads.push({ blob, size: blob.size, sha256: await sha256Hex(blob) });
  }
  const created = await api.createTransfer({
    ...base,
    encrypted: true,
    acknowledge_unsupported: true,
    files: files.map((file, i) => ({ name: file.name, size: payloads[i].size, type: file.type, sha256: payloads[i].sha256 })),
  });
  await uploadAll(created.uploads, payloads, (u, p, onP) => api.uploadToS3(u, p.blob, onP));
  await api.completeTransfer(created.id, created.manage_token);

  const dl = await api.getDownload(created.id);
  assert.equal(dl.encrypted, true);
  for (const [i, file] of files.entries()) {
    const cipher = await (await fetch(dl.files[i].url)).arrayBuffer();
    assert.equal(cipher.byteLength, dl.files[i].size);
    const plain = await decryptBuffer(cipher, encoded);
    assert.equal(await plain.text(), await file.text());
  }
});

test("폐기하면 모든 파일이 지워지고, 관리 토큰이 없으면 폐기할 수 없다", async () => {
  const a = pdf("a.pdf", "1");
  const b = pdf("b.pdf", "2");
  const payloads = [a, b].map((blob) => ({ blob, size: blob.size }));
  const files = [await meta(a, "a.pdf"), await meta(b, "b.pdf")];
  payloads.forEach((p, i) => { p.sha256 = files[i].sha256; });
  const created = await api.createTransfer({ ...base, acknowledge_unsupported: true, files });
  await uploadAll(created.uploads, payloads, (u, p) => api.uploadToS3(u, p.blob));
  await api.completeTransfer(created.id, created.manage_token);
  assert.equal((await api.getDownload(created.id)).files.length, 2);

  await rejects(api.revokeTransfer(created.id, "wrong"), "FORBIDDEN", 403);
  assert.equal((await api.revokeTransfer(created.id, created.manage_token)).status, "REVOKED");
  const stored = JSON.parse(memory.get("sd.mock.transfers"))[created.id];
  assert.ok(stored.files.every((x) => !x.dataUrl));
  await rejects(api.getDownload(created.id), "NOT_FOUND");
});
