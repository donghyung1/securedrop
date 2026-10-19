"""Run explicitly with: uv run python deploy/verify_live.py (uses real AWS S3)."""

import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from main import app


def http_status(url: str) -> int:
    try:
        with urlopen(url, timeout=20) as response:
            return response.status
    except HTTPError as error:
        return error.code


def main():
    key = app.state.settings.plugin_api_key.get_secret_value()
    if not key:
        raise RuntimeError("Configure PLUGIN_API_KEY before verification")
    headers = {"X-API-Key": key}
    content = b"SecureDrop live S3 verification\n"
    identifier = None
    revoked = False
    with TestClient(app, raise_server_exceptions=False) as client:
        try:
            response = client.post(
                "/plugin/transfers",
                headers=headers,
                files={"file": ("verification.txt", content)},
                data={"expires_in": "300", "user_approved": "true"},
            )
            if response.status_code != 200:
                raise RuntimeError(f"Upload failed with HTTP {response.status_code}")
            item = response.json()
            identifier = item["id"]
            with urlopen(item["url"], timeout=20) as download:
                assert download.read() == content, "Downloaded content mismatch"
            parsed = urlsplit(item["url"])
            anonymous_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
            assert http_status(anonymous_url) == 403, "Anonymous download was not denied"
            assert client.get(f"/api/transfers/{identifier}", headers=headers).status_code == 200
            row = app.state.store.transfer(identifier)
            metadata = app.state.storage.client.head_object(
                Bucket=app.state.settings.s3_bucket, Key=row["object_key"]
            )
            assert metadata["ServerSideEncryption"] == "AES256", "SSE-S3 missing"
            expiry_probe = app.state.storage.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": app.state.settings.s3_bucket, "Key": row["object_key"]},
                ExpiresIn=1,
            )
            # Signing uses the local clock; tolerate a small lead over the S3 clock.
            deadline = time.monotonic() + 30
            expired_status = http_status(expiry_probe)
            while expired_status == 200 and time.monotonic() < deadline:
                time.sleep(2)
                expired_status = http_status(expiry_probe)
            assert expired_status == 403, f"Expired URL returned HTTP {expired_status}"
            response = client.delete(f"/api/transfers/{identifier}", headers=headers)
            assert response.status_code == 200, "Revoke failed"
            revoked = True
            assert http_status(item["url"]) in (403, 404), "Revoked URL still downloads"
            assert client.get(f"/api/transfers/{identifier}", headers=headers).status_code == 404
            result = {
                "upload": "passed",
                "presigned_download": "passed",
                "anonymous_download_blocked": "passed",
                "sse_s3": "passed",
                "expired_url_blocked": "passed",
                "revoke": "passed",
                "revoked_url_blocked": "passed",
                "qr": "passed" if item["qr"].startswith("data:image/png;base64,") else "failed",
            }
            report = Path(__file__).parent / "aws" / "verification.json"
            report.parent.mkdir(exist_ok=True)
            report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result, indent=2))
        finally:
            if identifier and not revoked:
                response = client.delete(f"/api/transfers/{identifier}", headers=headers)
                if response.status_code != 200:
                    raise RuntimeError("Verification object cleanup failed")


if __name__ == "__main__":
    main()
