"""Exercise the deployed HTTPS demo and remove the temporary transfer afterward."""

import base64
import json
import secrets
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import zxingcpp
from PIL import Image


def request(url, method="GET", data=None, headers=None):
    try:
        with urlopen(
            Request(url, data=data, headers=headers or {}, method=method), timeout=60
        ) as r:
            return r.status, r.read()
    except HTTPError as error:
        return error.code, error.read()


def main():
    directory = Path(__file__).parent / "aws"
    state = json.loads((directory / "deployment.json").read_text())
    base = state["url"]
    headers = {"Origin": base}
    result = {}
    status, content = request(base + "/")
    assert status == 200 and b"<html" in content.lower(), f"Frontend HTTP {status}"
    result["frontend"] = "passed"
    status, content = request(base + "/api/health")
    assert status == 200, f"Health HTTP {status}"
    result["health"] = json.loads(content)
    status, _ = request(
        base + "/api/scan", "POST", b'{"text":"hello"}', {"Content-Type": "application/json"}
    )
    assert status == 401, f"Missing credentials HTTP {status}"
    result["missing_credentials_blocked"] = "passed"
    status, content = request(
        base + "/api/scan",
        "POST",
        b'{"text":"010-1234-5678"}',
        {**headers, "Content-Type": "application/json"},
    )
    assert status == 200 and json.loads(content)["blocked"], f"Detection HTTP {status}"
    result["sensitive_scan"] = "passed"
    boundary = "securedrop" + secrets.token_hex(16)
    file_content = b"SecureDrop HTTPS deployment verification\n"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="expires_in"\r\n\r\n300\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="verification.txt"\r\nContent-Type: text/plain\r\n\r\n'
        ).encode()
        + file_content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    status, content = request(
        base + "/api/transfers",
        "POST",
        body,
        {**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert status == 200, f"Upload HTTP {status}"
    item = json.loads(content)
    result["upload"] = "passed"
    try:
        status, downloaded = request(item["url"])
        assert status == 200 and downloaded == file_content, "Presigned download failed"
        result["presigned_download"] = "passed"
        assert item["download_url"] == base.rstrip("/") + "/d/" + item["id"]
        status, downloaded = request(item["download_url"])
        assert status == 200 and downloaded == file_content, "Short download failed"
        result["short_download"] = "passed"
        parsed = urlsplit(item["url"])
        unsigned = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        assert request(unsigned)[0] == 403, "Anonymous download was allowed"
        result["anonymous_download_blocked"] = "passed"
        assert item["qr"].startswith("data:image/png;base64,"), "QR missing"
        image = Image.open(BytesIO(base64.b64decode(item["qr"].split(",", 1)[1])))
        assert image.size == (180, 180), "Unexpected QR dimensions"
        decoded = zxingcpp.read_barcode(image)
        assert decoded is not None and decoded.text == item["download_url"], "QR decode failed"
        result["qr"] = "passed"
    finally:
        status, _ = request(base + "/api/transfers/" + item["id"], "DELETE", headers=headers)
        assert status == 200, f"Cleanup HTTP {status}"
    assert request(item["url"])[0] in (403, 404), "Revoked file still accessible"
    assert request(item["download_url"])[0] == 404, "Revoked short link still accessible"
    result["revoke"] = "passed"
    (directory / "https-verification.json").write_text(
        json.dumps({"url": base, **result}, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
