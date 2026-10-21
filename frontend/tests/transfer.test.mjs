// 실행: cd frontend && node --test tests/transfer.test.mjs   (Node 20+, 빌드·의존성 없음)
// 다중 파일 전송의 순수 로직: 선택 한도, 요청 본문(파일별 참조·전송 단위 플래그), 동시 업로드
import test from "node:test";
import assert from "node:assert/strict";
import { checkAddition, blocksE2ee, planTransfer, buildCreateBody, uploadAll } from "../js/transfer.js";

const MB = 1024 * 1024;
const CFG = {
  MAX_FILES: 5,
  MAX_FILE_MB: 20,
  SCANNABLE_EXTS: ["txt", "csv"],
  ALLOWED_EXTS: ["txt", "csv", "json", "pdf", "png"],
};
const f = (name, size = 10, lastModified = 1) => ({ name, size, lastModified, type: "" });

test("허용 형식·빈 파일은 거부하고 사유를 돌려준다", () => {
  assert.match(checkAddition([], f("a.exe"), CFG), /\.exe 형식은 업로드할 수 없습니다/);
  assert.match(checkAddition([], f("a.txt", 0), CFG), /빈 파일은 보낼 수 없습니다/);
  assert.equal(checkAddition([], f("a.txt"), CFG), null);
});

test("파일은 최대 5개까지, 6번째는 거부한다", () => {
  const five = ["a", "b", "c", "d", "e"].map((n) => f(`${n}.pdf`));
  assert.equal(checkAddition(five.slice(0, 4), five[4], CFG), null);
  assert.match(checkAddition(five, f("f.pdf"), CFG), /최대 5개/);
});

test("합계 20MB를 넘으면 거부하고, 정확히 20MB는 허용한다", () => {
  assert.equal(checkAddition([f("a.pdf", 10 * MB)], f("b.pdf", 10 * MB), CFG), null);
  assert.match(checkAddition([f("a.pdf", 10 * MB)], f("b.pdf", 10 * MB + 1), CFG), /합계가 20MB를 초과/);
  assert.match(checkAddition([], f("big.pdf", 20 * MB + 1), CFG), /20MB를 초과/);
});

test("같은 파일(이름·크기·수정시각)을 두 번 더하는 것은 거부한다", () => {
  assert.match(checkAddition([f("a.pdf", 5, 7)], f("a.pdf", 5, 7), CFG), /이미 추가한 파일/);
  assert.equal(checkAddition([f("a.pdf", 5, 7)], f("a.pdf", 6, 7), CFG), null);
});

test("검사 대상(.txt/.csv)이 하나라도 있으면 E2EE를 쓸 수 없다", () => {
  assert.equal(blocksE2ee([f("a.pdf"), f("b.png")], CFG), false);
  assert.equal(blocksE2ee([f("a.pdf"), f("b.CSV")], CFG), true);
  assert.equal(blocksE2ee([], CFG), false);
});

const result = (id, sha = "a".repeat(64)) => ({ scan_id: id, sha256: sha, blocked: false, findings: [] });
const processed = (clean) => ({
  res: { sanitize_id: "san1", sha256: "b".repeat(64), clean, findings_after: clean ? [] : [{ type: "EMAIL", count: 1 }] },
  blob: { size: 7 },
  name: "x_정제본.csv",
  type: "text/csv",
});

test("파일마다 자기 scan_id/sanitize_id를 갖고, 참조 없는 형식은 비어 있다", () => {
  const { items, flags } = planTransfer([
    { file: f("clean.txt"), scan: "clean", scanResult: result("s1") },
    { file: f("risk.csv"), scan: "blocked", scanResult: result("s2"), processed: processed(true) },
    { file: f("doc.pdf"), scan: "unscannable", scanResult: null },
  ]);
  assert.deepEqual(items.map((i) => i.ref), [{ scan_id: "s1" }, { sanitize_id: "san1" }, {}]);
  assert.deepEqual(items.map((i) => i.name), ["clean.txt", "x_정제본.csv", "doc.pdf"]);
  assert.equal(items[0].expectedSha256, "a".repeat(64));
  assert.equal(items[1].expectedSha256, "b".repeat(64));
  assert.equal(items[2].expectedSha256, undefined);
  // 하나라도 필요하면 전송 전체에 건다
  assert.deepEqual(flags, { acknowledge_unsupported: true, acknowledge_risk: false, user_approved: true });
});

test("확인 플래그: 원본 민감정보·남은 민감정보가 있을 때만 acknowledge_risk", () => {
  const clean = { file: f("a.txt"), scan: "clean", scanResult: result("s1") };
  assert.equal(planTransfer([clean]).flags.acknowledge_risk, false);
  const original = { file: f("b.txt"), scan: "blocked", scanResult: result("s2") };
  assert.equal(planTransfer([clean, original]).flags.acknowledge_risk, true);
  const dirty = { file: f("c.csv"), scan: "blocked", scanResult: result("s3"), processed: processed(false) };
  assert.equal(planTransfer([clean, dirty]).flags.acknowledge_risk, true);
  const cleaned = { ...dirty, processed: processed(true) };
  const flags = planTransfer([clean, cleaned]).flags;
  assert.deepEqual(flags, { acknowledge_unsupported: false, acknowledge_risk: false, user_approved: true });
});

test("요청 본문: 크기·해시는 실제 업로드할 바이트(암호문) 기준, 참조는 files[] 안에만", () => {
  const { items, flags } = planTransfer([
    { file: f("clean.txt"), scan: "clean", scanResult: result("s1") },
    { file: f("doc.pdf"), scan: "unscannable", scanResult: null },
  ]);
  const body = buildCreateBody({
    items,
    payloads: [{ size: 100, sha256: "1".repeat(64) }, { size: 228, sha256: "2".repeat(64) }],
    expiresIn: 600,
    encrypted: false,
    flags,
  });
  assert.deepEqual(body.files, [
    { name: "clean.txt", size: 100, type: "application/octet-stream", sha256: "1".repeat(64), scan_id: "s1" },
    { name: "doc.pdf", size: 228, type: "application/octet-stream", sha256: "2".repeat(64) },
  ]);
  assert.equal(body.scan_id, undefined);
  assert.equal(body.sanitize_id, undefined);
  assert.equal(body.expires_in, 600);
  assert.equal(body.acknowledge_unsupported, true);
});

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

test("uploadAll: uploads[i]에 payloads[i]를 올리고 동시 3개를 넘기지 않으며 진행률은 1로 끝난다", async () => {
  const payloads = [10, 20, 30, 40, 50].map((size) => ({ size }));
  const uploads = payloads.map((_, i) => ({ url: `u${i}` }));
  let running = 0, peak = 0;
  const seen = [];
  const progress = [];
  await uploadAll(uploads, payloads, async (up, p, onP) => {
    running++; peak = Math.max(peak, running);
    seen.push([up.url, p.size]);
    onP(0.5);
    await delay(5);
    onP(1);
    running--;
  }, (r) => progress.push(r));
  assert.equal(peak, 3);
  assert.deepEqual(seen.sort(), [["u0", 10], ["u1", 20], ["u2", 30], ["u3", 40], ["u4", 50]]);
  assert.equal(progress.at(-1), 1);
  assert.ok(progress.every((r) => r >= 0 && r <= 1));
});

test("uploadAll: 하나가 실패하면 그 오류를 던지고 남은 파일은 시작하지 않는다", async () => {
  const payloads = [1, 1, 1, 1, 1].map((size) => ({ size }));
  const uploads = payloads.map((_, i) => i);
  const started = [];
  await assert.rejects(
    uploadAll(uploads, payloads, async (i) => {
      started.push(i);
      await delay(5);
      if (i === 1) throw new Error("boom");
    }, null, 2),
    { message: "boom" },
  );
  assert.ok(started.length < 5, `시작된 업로드: ${started}`);
});

test("uploadAll: 업로드 주소 수가 파일 수와 다르면 거부한다", async () => {
  await assert.rejects(uploadAll([{}], [{ size: 1 }, { size: 1 }], async () => {}), { message: "UPLOAD_COUNT_MISMATCH" });
});
