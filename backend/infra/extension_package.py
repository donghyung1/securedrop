"""Build the Chrome extension ZIP served at /download/securedrop-extension.zip (PRD 8.1).

The ZIP contains extension/ as-is except config.js, which is rewritten to point at the
deployed API Gateway (apiBase) and CloudFront web (webBase). No keys are included.
"""

import io
import json
import zipfile
from pathlib import Path

ZIP_KEY = "download/securedrop-extension.zip"
FOLDER = "securedrop-extension"  # top-level folder users select in "Load unpacked"


def extension_config(api_base: str, web_base: str) -> bytes:
    config = {"apiBase": api_base.rstrip("/"), "webBase": web_base.rstrip("/"), "failClosed": True}
    return (
        "// Generated at deploy time. Popup settings still take precedence.\n"
        f"const SD_CONFIG = {json.dumps(config)};\n"
    ).encode()


def build_extension_zip(extension_dir: Path, api_base: str, web_base: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(extension_dir.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            data = (
                extension_config(api_base, web_base)
                if path.name == "config.js"
                else path.read_bytes()
            )
            # Fixed timestamp keeps the ZIP byte-identical across deploys of the same sources.
            info = zipfile.ZipInfo(f"{FOLDER}/{path.name}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()
