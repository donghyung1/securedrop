"""Verify the deployed frontend against the real API and signed S3 uploads."""

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
        expect(page).to_have_title("SecureDrop Cloud")
        expect(page.locator("#api-mode")).to_contain_text(STATE["api_base"].split("://")[1])
        assert page.evaluate("window.SD_CONFIG.defaultApiMode") == "real"
        results["new_frontend_and_real_api_default"] = "passed"
        cases = [
            ("clean.txt", "text/plain", b"SecureDrop browser verification\n", "clean"),
            ("risk.txt", "text/plain", b"Contact: 010-1234-5678\n", "risk"),
            ("large.json", "application/json", b" " * (20 * 1024 * 1024), "unsupported"),
        ]
        for name, mime, content, mode in cases:
            page.locator("#file-input").set_input_files(
                {"name": name, "mimeType": mime, "buffer": content}
            )
            if mode == "clean":
                expect(page.locator("#scan-status")).to_contain_text(
                    "민감정보가 발견되지 않았습니다", timeout=60000
                )
            else:
                expect(page.locator("#confirm-row")).to_be_visible(timeout=60000)
                expect(page.locator("#btn-send")).to_be_disabled()
                if mode == "risk":
                    expect(page.locator("#scan-status")).to_contain_text("원본 전송을 보류")
                page.locator("#confirm-check").check()
            expect(page.locator("#btn-send")).to_be_enabled(timeout=60000)
            with page.expect_response(
                lambda response: (
                    response.url.endswith("/complete") and response.request.method == "POST"
                ),
                timeout=120000,
            ) as completion:
                page.locator("#btn-send").click()
            done = completion.value.json()
            expect(page.locator("#qr-card")).to_be_visible(timeout=60000)
            assert done["share_url"].startswith(STATE["url"] + "/d.html?id=")
            try:
                receiver = context.new_page()
                receiver.on("pageerror", lambda error: errors.append(str(error)))
                receiver.goto(done["share_url"])
                expect(receiver.locator("#dl-files button")).to_have_count(1, timeout=60000)
                with receiver.expect_download(timeout=60000) as event:
                    receiver.locator("#dl-files button").click()
                downloaded = event.value
                assert Path(downloaded.path()).read_bytes() == content
                assert downloaded.suggested_filename == name
                receiver.close()
                results[mode + "_upload_and_download"] = "passed"
            finally:
                page.locator("#btn-revoke").click()
                expect(page.locator("#qr-status")).to_contain_text("폐기됨", timeout=60000)
            page.locator("#btn-new").click()
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
