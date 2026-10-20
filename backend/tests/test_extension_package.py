import io
import json
import re
import zipfile
from pathlib import Path

from infra.extension_package import FOLDER, build_extension_zip

EXTENSION = Path(__file__).resolve().parents[2] / "extension"
API = "https://abc123.execute-api.ap-northeast-2.amazonaws.com/prod/"
WEB = "https://securedrop.example.com/"


def entries():
    data = build_extension_zip(EXTENSION, API, WEB)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}, data


def test_zip_contains_extension_files_under_one_folder():
    files, _ = entries()
    expected = {
        f"{FOLDER}/{p.name}"
        for p in EXTENSION.iterdir()
        if p.is_file() and not p.name.startswith(".")
    }
    assert set(files) == expected
    assert f"{FOLDER}/manifest.json" in files
    assert files[f"{FOLDER}/manifest.json"] == (EXTENSION / "manifest.json").read_bytes()


def test_config_points_to_deployed_addresses():
    files, _ = entries()
    config = files[f"{FOLDER}/config.js"].decode()
    payload = json.loads(re.search(r"const SD_CONFIG = (\{.*\});", config).group(1))
    assert payload == {"apiBase": API.rstrip("/"), "webBase": WEB.rstrip("/"), "failClosed": True}
    assert "localhost" not in config


def test_zip_is_reproducible_and_has_no_secrets():
    files, first = entries()
    _, second = entries()
    assert first == second
    blob = b"".join(files.values())
    assert not re.search(rb"AKIA[0-9A-Z]{16}", blob)
