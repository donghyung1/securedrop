"""배포된 웹(CloudFront)에서 C 담당 흐름을 실제 브라우저로 검증한다. (docs/REMAINING_TASKS.md C 항목)

  - sanitize_mask_recheck_preview_send: 정제(유형별 선택) → 재검사 → 남은 항목 추가 정제 → 상위 20행 미리보기 → 승인 → QR → 수신·내용 확인
  - sanitize_delete_send:               삭제 조치 → 처리본 전송 → 수신 파일에서 값이 지워졌는지 확인
  - assist_mask_delete_cancel_clarify:  자연어 요청 4종이 화면과 연결되고, 사용자가 확정하기 전에는 /api/sanitize를 호출하지 않음
  - e2ee_ciphertext_and_decrypt:        (F12, B·C 공동) S3 객체가 암호문인지, #k= 키로 복호화되는지, 틀린 키는 저장되지 않는지
  - multi_file_mixed_send:              (F10) .txt(안전) + .csv(정제) + .pdf(검사 미지원) 3개를 한 전송으로: 파일별 scan_id/sanitize_id, 수신 3개
  - multi_file_limits_and_e2ee:         (F10) 6번째 파일 거부, .txt가 있으면 E2EE 비활성, .pdf 2개는 키 하나로 암호화·각각 복호화
  - revoked_link_and_mobile_layout:     폐기 후 수신 화면, 390px 폭 레이아웃, 스크립트 오류 없음

실행(backend 개발 의존성 그룹에 playwright 포함, 어느 cwd에서든 동작):
  cd backend
  uv sync
  uv run python -m playwright install chromium   # 최초 1회
  uv run python ../frontend/tests/verify_deployed.py --url https://<CloudFront 도메인>
실제 S3에 작은 파일을 올리며, 각 시나리오가 끝나면 링크를 폐기해 객체를 지운다.
"""

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
DEFAULT_OUT = ROOT / "backend" / "deploy" / "aws" / "c-verification.json"  # git 제외 폴더


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="배포된 웹 주소 (CloudFront)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    url = args.url.rstrip("/")
    results, errors = {}, []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 1000}, accept_downloads=True)
        context.on("weberror", lambda e: errors.append(str(e.error)))
        page = context.new_page()
        page.goto(url + "/")
        assert page.evaluate("window.SD_CONFIG.defaultApiMode") == "real"

        def choose(path):
            page.locator("#file-input").set_input_files(str(path))
            expect(page.locator("#scan-status")).to_contain_text("원본 전송을 보류", timeout=60000)

        def send_and_receive():
            """승인 후 전송 → /complete 응답 → 새 탭에서 수신 → (파일명, 내용, 공유 링크)"""
            with page.expect_response(lambda r: r.url.endswith("/complete") and r.request.method == "POST",
                                      timeout=120000) as completion:
                page.locator("#btn-send").click()
            done = completion.value.json()
            expect(page.locator("#qr-card")).to_be_visible(timeout=60000)
            assert done["share_url"].startswith(url + "/d.html?id=")
            receiver = context.new_page()
            receiver.goto(done["share_url"])
            with receiver.expect_download(timeout=60000) as event:
                receiver.locator("#dl-files button").click()
            body = Path(event.value.path()).read_text(encoding="utf-8")
            name = event.value.suggested_filename
            receiver.close()
            return name, body, done["share_url"]

        def approve():
            """처리본 미리보기 승인 → 전송 목록에 처리본으로 반영 (전송은 따로 "QR 코드 만들기")"""
            expect(page.locator("#btn-send")).to_have_text("처리본 승인")
            page.locator("#btn-send").click()
            expect(page.locator("#btn-send")).to_have_text("QR 코드 만들기")

        def revoke_and_reset():
            page.locator("#btn-revoke").click()
            expect(page.locator("#qr-status")).to_contain_text("폐기됨", timeout=60000)
            page.locator("#btn-new").click()

        # 1) 마스킹 → 재검사(남음) → 추가 정제 → 미리보기 → 승인 → 수신
        choose(SAMPLES / "고객정보.csv")
        page.locator("#guard-buttons button", has_text="마스킹").click()
        page.select_option("#act-EMAIL", "SKIP")
        page.locator("#btn-sanitize").click()
        expect(page.locator("#recheck-findings")).to_contain_text("이메일", timeout=60000)
        expect(page.locator("#btn-send")).to_be_disabled()  # 남은 민감정보 → 확인 없이는 전송 불가
        page.locator("#guard-buttons button", has_text="남은 항목도 정제").click()
        page.locator("#btn-sanitize").click()
        expect(page.locator("#recheck-status")).to_contain_text("남은 민감정보가 없습니다", timeout=60000)
        preview = page.locator("#preview-text").inner_text()
        assert "900101-1******" in preview and "010-****-5678" in preview and "[REDACTED]" in preview
        assert len(preview.splitlines()) <= 20
        approve()
        name, body, _ = send_and_receive()
        assert name == "고객정보_정제본.csv", name
        assert all(v not in body for v in ("900101-1234568", "1234-5678", "@example.com")), body
        revoke_and_reset()
        results["sanitize_mask_recheck_preview_send"] = "passed"

        # 2) 삭제 조치
        choose(SAMPLES / "키포함.txt")
        page.locator("#guard-buttons button", has_text="해당 항목 삭제").click()
        assert page.input_value("#act-AWS_ACCESS_KEY") == "DELETE"
        page.locator("#btn-sanitize").click()
        expect(page.locator("#recheck-status")).to_contain_text("남은 민감정보가 없습니다", timeout=60000)
        approve()
        name, body, _ = send_and_receive()
        assert "AKIA" not in body and "AWS_ACCESS_KEY_ID=" in body, body
        revoke_and_reset()
        results["sanitize_delete_send"] = "passed"

        # 3) 자연어 요청 — 해석 결과는 선택 화면에만 채우고, 확정 전에는 /api/sanitize를 부르지 않는다
        sanitize_calls = []
        page.on("request", lambda r: "/api/sanitize" in r.url and sanitize_calls.append(r.url))
        choose(SAMPLES / "고객정보.csv")

        def ask(text):
            count = page.locator("#guard-messages .bubble").count()
            page.fill("#guard-input", text)
            page.press("#guard-input", "Enter")
            expect(page.locator("#guard-messages .bubble")).to_have_count(count + 2, timeout=60000)

        choices = lambda: {t: page.input_value(f"#act-{t}") for t in ("RRN", "PHONE", "EMAIL")}
        ask("주민등록번호만 가려줘")
        assert choices() == {"RRN": "MASK", "PHONE": "SKIP", "EMAIL": "SKIP"}, choices()
        ask("이메일도 지워줘")
        assert choices() == {"RRN": "MASK", "PHONE": "SKIP", "EMAIL": "DELETE"}, choices()
        ask("이메일 말고 다 가려줘")  # 부정 → CLARIFY
        expect(page.locator("#guard-buttons")).to_contain_text("해당 항목 삭제")
        ask("취소할래")  # CANCEL → 확인 버튼만, 즉시 취소 안 함
        expect(page.locator("#guard-buttons button")).to_have_text(["전송 취소"])
        expect(page.locator("#file-info")).to_be_visible()
        assert not sanitize_calls, sanitize_calls
        page.locator("#guard-buttons button", has_text="전송 취소").click()  # 파일이 하나뿐이라 전송 전체 취소
        expect(page.locator("#file-info")).to_be_hidden()
        results["assist_mask_delete_cancel_clarify"] = "passed"

        # 4) 브라우저 암호화 (F12)
        # 검사 대상(.txt/.csv)은 서버가 평문 해시로 검사 결과를 검증하므로 암호화 체크박스가 꺼지고 비활성화된다.
        page.locator("#file-input").set_input_files(str(SAMPLES / "회의록.txt"))
        expect(page.locator("#scan-status")).to_contain_text("발견되지 않았습니다", timeout=60000)
        expect(page.locator("#encrypt-check")).to_be_disabled()
        expect(page.locator("#encrypt-check")).not_to_be_checked()
        page.locator("#btn-reset").click()  # 파일을 고르면 목록에 더해지므로, 비우고 .pdf만 고른다
        plain = b"%PDF-1.4\n" + b"e2ee-sample\n" * 20  # 검사 미지원 형식은 암호화 가능
        page.locator("#file-input").set_input_files(
            {"name": "암호화시험.pdf", "mimeType": "application/pdf", "buffer": plain}
        )
        expect(page.locator("#encrypt-check")).to_be_enabled(timeout=60000)
        page.locator("#confirm-check").check()
        page.locator("#encrypt-check").check()
        with page.expect_response(lambda r: r.url.endswith("/complete") and r.request.method == "POST",
                                  timeout=120000):
            page.locator("#btn-send").click()
        expect(page.locator("#qr-e2ee-note")).to_be_visible(timeout=60000)
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=url)
        page.locator("#btn-copy").click()
        link = page.evaluate("navigator.clipboard.readText()")
        assert re.search(r"/d\.html\?id=[0-9a-f]+#k=", link), link
        receiver = context.new_page()
        with receiver.expect_response(lambda r: "/api/d/" in r.url, timeout=60000) as meta:
            receiver.goto(link)
        signed = meta.value.json()["files"][0]["url"]
        cipher = context.request.get(signed).body()
        assert cipher != plain and len(cipher) == len(plain) + 28  # S3에는 IV+암호문+태그만
        with receiver.expect_download(timeout=60000) as event:
            receiver.locator("#dl-files button").click()
        assert Path(event.value.path()).read_bytes() == plain
        bad = context.new_page()
        bad.goto(link.split("#k=")[0] + "#k=" + "A" * 43)
        downloads = []
        bad.on("download", lambda d: downloads.append(d))
        bad.locator("#dl-files button").click()
        expect(bad.locator("#toast")).to_contain_text("복호화에 실패", timeout=30000)
        assert not downloads
        receiver.close()
        bad.close()
        revoke_and_reset()
        results["e2ee_ciphertext_and_decrypt"] = "passed"

        # 5) 다중 파일 (F10): 안전 .txt + 정제할 .csv + 검사 미지원 .pdf 를 한 전송으로
        pdf_bytes = b"%PDF-1.4\n" + b"multi-file-sample\n" * 10
        # Playwright는 경로와 버퍼를 한 목록에 섞지 못하므로 전부 버퍼로 넘긴다
        page.locator("#file-input").set_input_files([
            {"name": "회의록.txt", "mimeType": "text/plain", "buffer": (SAMPLES / "회의록.txt").read_bytes()},
            {"name": "고객정보.csv", "mimeType": "text/csv", "buffer": (SAMPLES / "고객정보.csv").read_bytes()},
            {"name": "문서.pdf", "mimeType": "application/pdf", "buffer": pdf_bytes},
        ])
        expect(page.locator("#file-list .file-badge")).to_have_text(
            ["안전", "민감정보 발견", "검사 미지원"], timeout=60000)
        expect(page.locator("#btn-send")).to_be_disabled()  # 확인 전에는 보낼 수 없음
        page.get_by_role("button", name="고객정보.csv 정제").click()
        page.locator("#btn-sanitize").click()  # 기본 선택: 모든 유형 마스킹
        expect(page.locator("#recheck-status")).to_contain_text("남은 민감정보가 없습니다", timeout=60000)
        approve()
        expect(page.locator("#file-list .file-badge")).to_have_text(["안전", "정제 완료", "검사 미지원"])
        expect(page.locator("#btn-send")).to_be_disabled()  # 검사 미지원 파일 확인이 남음
        page.locator("#confirm-check").check()
        with page.expect_request(lambda r: r.url.endswith("/api/transfers") and r.method == "POST") as created:
            with page.expect_response(lambda r: r.url.endswith("/complete") and r.request.method == "POST",
                                      timeout=120000) as completion:
                page.locator("#btn-send").click()
        request_body = created.value.post_data_json
        assert [f["name"] for f in request_body["files"]] == ["회의록.txt", "고객정보_정제본.csv", "문서.pdf"]
        assert "scan_id" in request_body["files"][0] and "sanitize_id" in request_body["files"][1]
        assert not any(k in request_body["files"][2] for k in ("scan_id", "sanitize_id"))
        assert "scan_id" not in request_body and "sanitize_id" not in request_body  # 참조는 파일별
        assert request_body["user_approved"] and request_body["acknowledge_unsupported"]
        assert not request_body["acknowledge_risk"]
        done = completion.value.json()
        expect(page.locator("#qr-card")).to_be_visible(timeout=60000)
        receiver = context.new_page()
        receiver.goto(done["share_url"])
        expect(receiver.locator("#dl-files button")).to_have_count(3, timeout=60000)
        saved = {}
        for index in range(3):
            with receiver.expect_download(timeout=60000) as event:
                receiver.locator("#dl-files button").nth(index).click()
            saved[event.value.suggested_filename] = Path(event.value.path()).read_bytes()
        receiver.close()
        assert set(saved) == {"회의록.txt", "고객정보_정제본.csv", "문서.pdf"}, set(saved)
        assert saved["문서.pdf"] == pdf_bytes
        assert b"900101-1234568" not in saved["고객정보_정제본.csv"]
        revoke_and_reset()
        results["multi_file_mixed_send"] = "passed"

        # 6) 다중 파일 한도·E2EE: 6개째 거부, .txt가 있으면 E2EE 꺼짐, .pdf 2개는 키 하나로 각각 복호화
        extra = [{"name": f"한도{i}.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n" + bytes([65 + i])}
                 for i in range(6)]
        page.locator("#file-input").set_input_files(extra)
        expect(page.locator("#file-list .file-item")).to_have_count(5, timeout=60000)
        expect(page.locator("#toast")).to_contain_text("최대 5개")
        page.locator("#btn-reset").click()
        page.locator("#file-input").set_input_files(str(SAMPLES / "회의록.txt"))
        expect(page.locator("#encrypt-check")).to_be_disabled(timeout=60000)
        page.get_by_role("button", name="회의록.txt 빼기").click()
        expect(page.locator("#encrypt-check")).to_be_enabled()
        pdfs = [b"%PDF-1.4\n" + b"first-file\n" * 20, b"%PDF-1.4\n" + b"second-file\n" * 30]
        page.locator("#file-input").set_input_files([
            {"name": "하나.pdf", "mimeType": "application/pdf", "buffer": pdfs[0]},
            {"name": "둘.pdf", "mimeType": "application/pdf", "buffer": pdfs[1]},
        ])
        expect(page.locator("#file-list .file-badge")).to_have_text(["검사 미지원", "검사 미지원"], timeout=60000)
        page.locator("#confirm-check").check()
        page.locator("#encrypt-check").check()
        with page.expect_response(lambda r: r.url.endswith("/complete") and r.request.method == "POST",
                                  timeout=120000):
            page.locator("#btn-send").click()
        expect(page.locator("#qr-e2ee-note")).to_be_visible(timeout=60000)
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=url)
        page.locator("#btn-copy").click()
        link = page.evaluate("navigator.clipboard.readText()")
        assert re.search(r"/d\.html\?id=[0-9a-f]+#k=", link), link
        receiver = context.new_page()
        with receiver.expect_response(lambda r: "/api/d/" in r.url, timeout=60000) as meta:
            receiver.goto(link)
        signed = meta.value.json()["files"]
        assert len(signed) == 2
        for item, plain_bytes in zip(signed, pdfs):
            cipher = context.request.get(item["url"]).body()
            assert cipher != plain_bytes and len(cipher) == len(plain_bytes) + 28
        for index, plain_bytes in enumerate(pdfs):
            with receiver.expect_download(timeout=60000) as event:
                receiver.locator("#dl-files button").nth(index).click()
            assert Path(event.value.path()).read_bytes() == plain_bytes
        receiver.close()
        revoke_and_reset()
        results["multi_file_limits_and_e2ee"] = "passed"

        # 7) 폐기된 링크 + 모바일 폭
        page.locator("#file-input").set_input_files(str(SAMPLES / "회의록.txt"))
        expect(page.locator("#scan-status")).to_contain_text("발견되지 않았습니다", timeout=60000)
        with page.expect_response(lambda r: r.url.endswith("/complete"), timeout=120000) as completion:
            page.locator("#btn-send").click()
        share = completion.value.json()["share_url"]
        page.locator("#btn-revoke").click()
        expect(page.locator("#qr-status")).to_contain_text("폐기됨", timeout=60000)
        phone = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        phone.goto(share)
        expect(phone.locator("#dl-error-title")).to_contain_text("폐기", timeout=60000)
        assert phone.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        results["revoked_link_and_mobile_layout"] = "passed"
        browser.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
