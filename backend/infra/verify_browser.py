"""Run the deployed demo in Chromium. Requires uv run --with playwright."""

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

OUT = Path(__file__).resolve().parents[1] / "deploy" / "aws"
STATE = json.loads((OUT / "serverless.json").read_text())


def main():
    results = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(STATE["url"])
        expect(page.locator("#health")).to_contain_text("서버 연결됨", timeout=60000)
        page.locator("#sample").click()
        page.locator("#scan").click()
        expect(page.locator("#scan-result")).to_contain_text("민감정보 발견", timeout=60000)
        results["text_scan"] = "passed"
        cases = [
            ("clean.txt", "text/plain", b"SecureDrop browser verification\n", "clean"),
            ("sensitive.txt", "text/plain", b"Contact: 010-1234-5678\n", "sanitize"),
            ("risk.txt", "text/plain", b"Contact: 010-1234-5678\n", "risk"),
            ("large.json", "application/json", b" " * (20 * 1024 * 1024), "unsupported"),
        ]
        for name, mime, content, mode in cases:
            page.locator("#file").set_input_files(
                {"name": name, "mimeType": mime, "buffer": content}
            )
            if mode == "clean":
                expect(page.locator("#upload")).to_be_enabled(timeout=60000)
                page.locator("#upload").click()
                expected = content
            elif mode == "sanitize":
                expect(page.locator("#mask")).to_be_visible(timeout=60000)
                page.locator("#mask").click()
                expect(page.locator("#draft")).to_be_visible(timeout=60000)
                expect(page.locator("#send-draft")).to_be_disabled()
                page.locator("#approve").check()
                expected = page.evaluate(
                    "async () => Array.from(new Uint8Array(await draftFile.arrayBuffer()))"
                )
                expected = bytes(expected)
                assert expected != content and b"010-1234-5678" not in expected
                page.locator("#send-draft").click()
            else:
                expect(page.locator("#sensitive")).to_be_visible(timeout=60000)
                expect(page.locator("#original")).to_be_disabled()
                page.locator("#risk").check()
                page.locator("#original").click()
                expected = content
            try:
                expect(page.locator("#status")).to_contain_text("전송 완료", timeout=120000)
                share = page.locator("#download").get_attribute("href")
                assert share.startswith(STATE["url"] + "/d.html?id=")
                receiver = context.new_page()
                receiver.goto(share)
                expect(receiver.locator("#files a")).to_have_count(1, timeout=60000)
                with receiver.expect_download(timeout=60000) as event:
                    receiver.locator("#files a").click()
                downloaded = event.value
                assert Path(downloaded.path()).read_bytes() == expected
                assert downloaded.suggested_filename == name
                receiver.close()
                results[mode + "_upload_and_download"] = "passed"
            finally:
                if page.locator("#revoke").is_visible():
                    page.locator("#revoke").click()
                    expect(page.locator("#transfer-status")).to_contain_text(
                        "폐기되었습니다", timeout=60000
                    )
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(OUT / "demo-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        results["mobile_layout_and_no_script_errors"] = "passed"
        browser.close()
    (OUT / "browser-verification.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
