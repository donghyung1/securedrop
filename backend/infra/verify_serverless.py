"""Exercise deployed v1.3 API, real signed S3 POST, QR, access controls and cleanup."""

import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import boto3
import httpx
import zxingcpp
from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "deploy" / "aws"
STATE = json.loads((OUT / "serverless.json").read_text())
CONFIG = json.loads((OUT / "resources.json").read_text())


def main():
    results = {}
    with httpx.Client(
        base_url=STATE["api_base"], timeout=60, headers={"Origin": STATE["url"]}
    ) as client:

        def call(method, path, expected=200, **kwargs):
            response = client.request(method, path, **kwargs)
            assert response.status_code == expected, (
                path,
                response.status_code,
                response.text[:200],
            )
            assert response.headers.get("access-control-allow-origin") == STATE["url"]
            return response.json()

        call("GET", "/api/health")
        assert call("POST", "/api/scan", json={"text": "010-1234-5678"})["blocked"]
        results["health_and_sensitive_scan"] = "passed"
        content = b"Contact: 010-1234-5678\n"
        scan = call(
            "POST", "/api/scan/file", files={"file": ("verification.txt", content, "text/plain")}
        )
        assert scan["blocked"]
        clean = call(
            "POST",
            "/api/sanitize",
            files={"file": ("verification.txt", content, "text/plain")},
            data={"actions": json.dumps([{"action": "MASK", "targets": ["PHONE"]}])},
        )
        processed = base64.b64decode(clean["content_b64"])
        assert clean["clean"] and processed != content
        assert clean["sha256"] == hashlib.sha256(processed).hexdigest()
        results["multipart_scan_and_sanitize"] = "passed"
        metadata = {
            "name": "verification.txt",
            "size": len(processed),
            "type": "text/plain",
            "sha256": clean["sha256"],
        }
        payload = {
            "files": [metadata],
            "sanitize_id": clean["sanitize_id"],
            "user_approved": True,
            "expires_in": 300,
        }
        call("POST", "/api/transfers", expected=422, json={**payload, "user_approved": False})
        pending = call("POST", "/api/transfers", json=payload)
        identifier = pending["id"]
        managed = {"X-Manage-Token": pending["manage_token"]}
        try:
            call("GET", f"/api/transfers/{identifier}", expected=403)
            call(
                "POST",
                f"/api/transfers/{identifier}/complete",
                expected=403,
                headers={"X-Manage-Token": "wrong"},
            )
            call("POST", f"/api/transfers/{identifier}/complete", expected=409, headers=managed)
            upload = pending["uploads"][0]
            response = httpx.post(
                upload["url"],
                data=upload["fields"],
                files={"file": (metadata["name"], processed, metadata["type"])},
                headers={"Origin": STATE["url"]},
                timeout=60,
            )
            assert response.status_code == 204, (response.status_code, response.text[:300])
            assert response.headers["access-control-allow-origin"] == STATE["url"]
            complete = call("POST", f"/api/transfers/{identifier}/complete", headers=managed)
            assert complete["share_url"] == STATE["url"] + "/d.html?id=" + identifier
            repeated = call("POST", f"/api/transfers/{identifier}/complete", headers=managed)
            assert repeated["share_url"] == complete["share_url"]
            image = Image.open(BytesIO(base64.b64decode(complete["qr"].split(",", 1)[1])))
            qr = zxingcpp.read_barcode(image)
            assert qr and qr.text == complete["share_url"]
            download = call("GET", f"/api/d/{identifier}")
            url = download["files"][0]["url"]
            assert httpx.get(url, timeout=60).content == processed
            parsed = urlsplit(url)
            unsigned = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
            assert httpx.get(unsigned, timeout=60).status_code == 403
            results["signed_s3_post_complete_download_qr"] = "passed"
            results["manage_token_approval_and_private_bucket"] = "passed"
        finally:
            call("DELETE", f"/api/transfers/{identifier}", headers=managed)
        call("GET", f"/api/d/{identifier}", expected=404)
        assert httpx.get(url, timeout=60).status_code in (403, 404)
        results["revoke_removes_s3_object"] = "passed"
        # API Gateway key enforcement and authenticated plugin integration.
        response = client.post("/api/plugin/scan", json={"text": "hello"})
        assert response.status_code == 403
        session = boto3.Session(profile_name=CONFIG["profile"], region_name=STATE["region"])
        key = session.client("apigateway").get_api_key(
            apiKey=STATE["plugin_key_id"], includeValue=True
        )["value"]
        assert not call(
            "POST", "/api/plugin/scan", headers={"X-API-Key": key}, json={"text": "hello"}
        )["blocked"]
        call("GET", "/api/plugin/openapi.json")
        results["plugin_key_enforcement"] = "passed"
        response = client.options(
            "/api/transfers",
            headers={
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-manage-token",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == STATE["url"]
        results["cors_preflight"] = "passed"
    for asset in (
        "/",
        "/d.html",
        "/js/main.js",
        "/js/download.js",
        "/config.js",
        "/css/style.css",
        "/admin.html",
    ):
        response = httpx.get(STATE["url"] + asset, timeout=60)
        assert response.status_code == 200, (asset, response.status_code)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert STATE["api_base"].split("/prod")[0] in response.headers["content-security-policy"]
    results["cloudfront_assets_and_security_headers"] = "passed"
    frontend = OUT.parents[2] / "frontend"
    assert (frontend / "index.html").is_file(), "Frontend source directory not found"
    for path in frontend.rglob("*"):
        if not path.is_file() or path.suffix not in {".html", ".js", ".css"}:
            continue
        asset = path.relative_to(frontend).as_posix()
        response = httpx.get(STATE["url"] + "/" + asset, timeout=60)
        assert response.status_code == 200, asset
        if asset == "config.js":
            assert STATE["api_base"] in response.text
            assert '"defaultApiMode": "real"' in response.text
        else:
            assert response.content == path.read_bytes(), (asset, "Deployed asset mismatch")
    results["frontend_matches_local_source"] = "passed"
    (OUT / "serverless-verification.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
