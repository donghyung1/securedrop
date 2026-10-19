import base64
import json
import tempfile
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from threading import Event

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from moto import mock_aws
from PIL import Image

from config import Settings
from detector import scan, scan_file
from main import create_app
from store import now

ORIGIN = {"Origin": "http://localhost:5500"}
KEY = {"X-API-Key": "test-key-only"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        s3 = boto3.client("s3", region_name="ap-northeast-2")
        s3.create_bucket(
            Bucket="securedrop-test",
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"},
        )
        settings = Settings(
            _env_file=None,
            s3_bucket="securedrop-test",
            plugin_api_key="test-key-only",
            database_path=tmp_path / "test.db",
            max_file_mb=1,
            aws_profile=None,
        )
        app = create_app(settings)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, app, s3


def upload(client, content=b"hello", filename="sample.txt", **kwargs):
    headers = kwargs.pop("headers", ORIGIN)
    return client.post(
        "/api/transfers", files={"file": (filename, content)}, headers=headers, **kwargs
    )


@pytest.mark.parametrize(
    "text,kind",
    [
        ("010-1234-5678", "PHONE"),
        ("someone@example.com", "EMAIL"),
        ("AKIA" + "A" * 16, "AWS_ACCESS_KEY"),
        ("sk-" + "a" * 24, "API_KEY"),
        ("api_key=" + "a" * 24, "API_KEY"),
        ("4111-1111-1111-1111", "CARD"),
        ("900101-1234568", "RRN"),
    ],
)
def test_detection(text, kind):
    assert {"type": kind, "count": 1} in scan(text)["findings"]
    assert text not in str(scan(text))


def test_detection_negative_and_counts():
    assert not scan("일반 문장 900101-1234567 4111-1111-1111-1112")["blocked"]
    assert scan("010-1234-5678 010-2222-3333")["findings"] == [{"type": "PHONE", "count": 2}]
    text = " " * 48_995 + "010-1234-5678" + " " * 51_000 + "010-9999-8888"
    assert scan_file(text)["findings"] == [{"type": "PHONE", "count": 2}]


def test_scan_validation_privacy_and_health(env):
    client, _, _ = env
    assert client.get("/api/health").json() == {"status": "ok", "region": "ap-northeast-2"}
    assert client.post("/api/scan", json={"text": "hello"}, headers=ORIGIN).json() == {
        "blocked": False,
        "findings": [],
    }
    secret = "PRIVATE" * 8000
    response = client.post("/api/scan", json={"text": secret}, headers=ORIGIN)
    assert response.status_code == 422
    assert "PRIVATE" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/logs", headers=ORIGIN).json() == {"items": []}


def test_transfer_s3_qr_and_revoke(env):
    client, app, s3 = env
    response = upload(client, data={"expires_in": "300"})
    assert response.status_code == 200, response.text
    item = response.json()
    assert "X-Amz-Expires=300" in item["url"]
    assert "X-Amz-Signature=" in item["url"]
    assert item["url"].startswith("https://securedrop-test.s3.ap-northeast-2.amazonaws.com/")
    image = Image.open(BytesIO(base64.b64decode(item["qr"].split(",", 1)[1])))
    assert image.format == "PNG"
    identifier = item["id"]
    key = app.state.store.transfer(identifier)["object_key"]
    obj = s3.get_object(Bucket="securedrop-test", Key=key)
    assert obj["Body"].read() == b"hello"
    assert obj["ServerSideEncryption"] == "AES256"
    assert client.get(f"/api/transfers/{identifier}", headers=ORIGIN).json()["status"] == "active"
    assert client.delete(f"/api/transfers/{identifier}", headers=ORIGIN).status_code == 200
    assert client.get(f"/api/transfers/{identifier}", headers=ORIGIN).status_code == 404
    with pytest.raises(ClientError):
        s3.get_object(Bucket="securedrop-test", Key=key)
    logs = client.get("/api/logs", headers=ORIGIN).json()["items"]
    assert [row["event"] for row in logs] == ["REVOKE", "TRANSFER"]
    assert all(set(row) == {"time", "event", "types", "source"} for row in logs)
    assert "sample.txt" not in str(logs) and item["url"] not in str(logs)


def test_text_confirmation_and_full_scan(env):
    client, _, s3 = env
    content = (" " * 60_000 + "010-1234-5678").encode()
    response = upload(client, content)
    assert response.status_code == 409
    assert response.json()["findings"] == [{"type": "PHONE", "count": 1}]
    assert "Contents" not in s3.list_objects_v2(Bucket="securedrop-test")
    assert upload(client, content, data={"confirm_sensitive": "true"}).status_code == 200
    logs = client.get("/api/logs", headers=ORIGIN).json()["items"]
    assert logs[0]["types"] == ["PHONE"]
    assert "010-1234-5678" not in str(logs)


def test_limits_and_bad_encodings(env):
    client, _, _ = env
    assert upload(client, filename="bad.exe").status_code == 422
    assert upload(client, content=b"").status_code == 422
    assert upload(client, data={"expires_in": "3601"}).status_code == 422
    assert upload(client, data={"expires_in": "0"}).status_code == 422
    assert upload(client, content=b"a" * (1024 * 1024 + 1)).status_code == 413
    assert upload(client, content=b"a" * (2 * 1024 * 1024)).status_code == 413
    assert upload(client, content=b"\x00\x00binary").status_code == 422
    assert upload(client, content="안녕하세요".encode("cp949")).status_code == 200
    assert upload(client, content="010-1234-5678".encode("utf-16")).status_code == 409


def test_expiration_and_storage_failure(env):
    client, app, _ = env
    identifier = upload(client).json()["id"]
    with app.state.store.connect() as conn:
        conn.execute(
            "UPDATE transfers SET expires_at=? WHERE id=?",
            (
                (now() - timedelta(seconds=1)).isoformat(),
                identifier,
            ),
        )
    assert client.get(f"/api/transfers/{identifier}", headers=ORIGIN).status_code == 410
    assert client.delete(f"/api/transfers/{identifier}", headers=ORIGIN).status_code == 200
    app.state.settings.s3_bucket = "no-such-bucket"
    response = upload(client)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "STORAGE_ERROR"


def test_delete_failure_preserves_status_and_failed_commit_cleans_s3(env, monkeypatch):
    client, app, s3 = env
    identifier = upload(client).json()["id"]
    from errors import APIError

    def failed_delete(key):
        raise APIError(502, "STORAGE_ERROR", "storage failed")

    with monkeypatch.context() as patch:
        patch.setattr(app.state.storage, "delete", failed_delete)
        assert client.delete(f"/api/transfers/{identifier}", headers=ORIGIN).status_code == 502
    assert client.get(f"/api/transfers/{identifier}", headers=ORIGIN).status_code == 200

    def failed_commit(*args):
        raise RuntimeError("internal failure containing private data")

    monkeypatch.setattr(app.state.store, "save_transfer", failed_commit)
    response = upload(client)
    assert response.status_code == 500
    assert "private data" not in response.text
    assert s3.list_objects_v2(Bucket="securedrop-test")["KeyCount"] == 1


def test_streamed_oversize_request(env):
    client, _, _ = env

    def chunks():
        for _ in range(20):
            yield b"a" * 100_000

    response = client.post(
        "/api/scan", headers={**ORIGIN, "Content-Type": "application/json"}, content=chunks()
    )
    assert response.status_code == 413


def test_twenty_mib_boundary(env):
    _, app, s3 = env
    settings = app.state.settings.model_copy(update={"max_file_mb": 20})
    with TestClient(create_app(settings)) as client:
        response = upload(client, b"a" * (20 * 1024 * 1024), filename="sample.pdf")
        assert response.status_code == 200, response.text
        objects = s3.list_objects_v2(Bucket="securedrop-test")["Contents"]
        assert objects[0]["Size"] == 20 * 1024 * 1024
        response = upload(client, b"a" * (20 * 1024 * 1024 + 1), filename="sample.pdf")
        assert response.status_code == 413


def test_auth_plugin_consent_and_schema(env):
    client, _, _ = env
    assert client.post("/api/scan", json={"text": "hello"}).status_code == 401
    assert (
        client.post(
            "/api/scan", headers={"Origin": "https://evil.test"}, json={"text": ""}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/scan", headers={**ORIGIN, "X-API-Key": "wrong"}, json={"text": ""}
        ).status_code
        == 401
    )
    assert client.post("/plugin/scan", headers=ORIGIN, json={"text": ""}).status_code == 401
    assert client.post("/plugin/scan", headers=KEY, json={"text": ""}).status_code == 200
    assert upload(client, headers=KEY).status_code == 403
    assert upload(client, headers=KEY, data={"user_approved": "true"}).status_code == 200
    schema = client.get("/plugin/openapi.json").json()
    assert set(schema["paths"]) == {"/plugin/scan", "/plugin/transfers"}
    assert schema["paths"]["/plugin/transfers"]["post"]["security"] == [{"APIKeyHeader": []}]


def test_plugin_rate_limit_shared_between_apps(env):
    client, app, _ = env
    other_app = create_app(app.state.settings)
    with TestClient(other_app) as other:
        for i in range(30):
            target = other if i % 2 else client
            assert target.post("/plugin/scan", headers=KEY, json={"text": ""}).status_code == 200
        response = client.post("/plugin/scan", headers=KEY, json={"text": ""})
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
        with app.state.store.connect() as conn:
            conn.execute("UPDATE rate_limits SET window=window-61")
        assert client.post("/plugin/scan", headers=KEY, json={"text": ""}).status_code == 200


def test_logs_and_cors(env):
    client, _, _ = env
    headers = {**ORIGIN, "X-Source": "extension"}
    payload = {"event": "BLOCK", "types": ["RRN"], "source": "extension"}
    assert client.post("/api/logs", json=payload, headers=headers).status_code == 200
    assert (
        client.post("/api/logs", json={**payload, "text": "private"}, headers=headers).status_code
        == 422
    )
    assert (
        client.post("/api/logs", json={**payload, "event": "TRANSFER"}, headers=headers).status_code
        == 422
    )
    assert client.get("/api/logs?limit=101", headers=ORIGIN).status_code == 422
    response = client.options(
        "/api/scan",
        headers={
            **ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert response.headers["access-control-allow-origin"] == ORIGIN["Origin"]
    response = client.options(
        "/api/scan",
        headers={
            "Origin": "https://evil.test",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def sanitize_upload(client, content, actions, filename="customers.csv"):
    return client.post(
        "/api/sanitize",
        headers=ORIGIN,
        files={"file": (filename, content)},
        data={"actions": json.dumps(actions)},
    )


def send_draft(client, identifier, **fields):
    return client.post("/api/transfers", headers=ORIGIN, data={"sanitize_id": identifier, **fields})


def test_sanitize_preview_approval_and_consumption(env):
    client, app, s3 = env
    original = (
        b"name,rrn,phone,token\nKim,900101-1234568,010-1234-5678,sk-aaaaaaaaaaaaaaaaaaaaaaaa\n"
    )
    assert upload(client, original, filename="customers.csv").status_code == 409
    actions = [{"action": "MASK", "targets": ["RRN", "PHONE", "API_KEY"]}]
    response = sanitize_upload(client, original, actions)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["clean"] and not result["findings_after"]
    assert "900101-1******" in result["preview"]
    assert "010-****-5678" in result["preview"]
    assert "[REDACTED]" in result["preview"]
    assert b"900101-1234568" in original
    assert "Contents" not in s3.list_objects_v2(Bucket="securedrop-test")
    identifier = result["sanitize_id"]
    assert send_draft(client, identifier).status_code == 403
    sent = send_draft(client, identifier, user_approved="true")
    assert sent.status_code == 200, sent.text
    key = app.state.store.transfer(sent.json()["id"])["object_key"]
    processed = s3.get_object(Bucket="securedrop-test", Key=key)["Body"].read()
    assert processed.decode() == result["preview"] + "\n"
    assert identifier not in app.state.drafts.entries
    assert send_draft(client, identifier, user_approved="true").status_code == 410
    logs = app.state.store.logs(10)
    assert [row["event"] for row in logs] == ["TRANSFER", "SANITIZE", "BLOCK"]
    for path in app.state.settings.database_path.parent.glob("test.db*"):
        persisted = path.read_bytes()
        for secret in (b"900101-1234568", b"010-1234-5678", b"sk-aaaaaaaaaaaaaaaaaaaaaaaa"):
            assert secret not in persisted


def test_sanitize_residual_requires_separate_confirmation(env):
    client, app, s3 = env
    response = sanitize_upload(
        client, b"900101-1234568,010-1234-5678", [{"action": "MASK", "targets": ["RRN"]}]
    )
    result = response.json()
    assert not result["clean"]
    assert result["findings_after"] == [{"type": "PHONE", "count": 1}]
    identifier = result["sanitize_id"]
    blocked = send_draft(client, identifier, user_approved="true")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SENSITIVE_REMAINS"
    assert identifier in app.state.drafts.entries
    assert "Contents" not in s3.list_objects_v2(Bucket="securedrop-test")
    assert send_draft(client, identifier, confirm_sensitive="true").status_code == 403
    assert (
        send_draft(client, identifier, confirm_sensitive="true", user_approved="true").status_code
        == 200
    )


def test_delete_preserves_csv_structure_and_allows_empty_result(env):
    client, _, _ = env
    response = sanitize_upload(
        client,
        b"name,email,card\nKim,a@example.com,4111-1111-1111-1111",
        [{"action": "DELETE", "targets": ["EMAIL", "CARD"]}],
    )
    assert response.json()["preview"] == "name,email,card\nKim,,"
    assert response.json()["clean"]
    empty = sanitize_upload(
        client, b"010-1234-5678", [{"action": "DELETE", "targets": ["PHONE"]}]
    ).json()
    assert empty["clean"] and empty["preview"] == ""
    assert send_draft(client, empty["sanitize_id"], user_approved="true").status_code == 200


@pytest.mark.parametrize(
    "actions",
    [
        [],
        {},
        [{"action": "EXECUTE", "targets": ["RRN"]}],
        [{"action": "MASK", "targets": ["UNKNOWN"]}],
        [{"action": "MASK"}],
        [{"action": "MASK", "targets": ["RRN", "RRN"]}],
        [{"action": "CANCEL"}, {"action": "MASK", "targets": ["RRN"]}],
        [{"action": "MASK", "targets": ["RRN"], "text": "private"}],
    ],
)
def test_sanitize_invalid_actions_do_not_persist(env, actions):
    client, app, s3 = env
    response = sanitize_upload(client, b"900101-1234568", actions)
    assert response.status_code == 422
    assert "900101-1234568" not in response.text
    assert not app.state.drafts.entries
    assert not app.state.store.logs(10)
    assert "Contents" not in s3.list_objects_v2(Bucket="securedrop-test")


def test_sanitize_file_validation_cancel_and_preview_limit(env):
    client, app, _ = env
    action = [{"action": "MASK", "targets": ["PHONE"]}]
    assert sanitize_upload(client, b"hi", action, "file.pdf").status_code == 415
    assert sanitize_upload(client, b"", action).status_code == 422
    assert sanitize_upload(client, b"\x00binary", action).status_code == 422
    assert sanitize_upload(client, b"a" * (1024 * 1024 + 1), action).status_code == 413
    cancelled = sanitize_upload(client, b"010-1234-5678", [{"action": "CANCEL"}]).json()
    assert cancelled["cancelled"] and cancelled["sanitize_id"] is None
    assert not app.state.drafts.entries
    content = ("전화,010-1234-5678\n" * 30).encode("cp949")
    result = sanitize_upload(client, content, action).json()
    assert len(result["preview"].splitlines()) == 20
    assert result["clean"]
    identifier = result["sanitize_id"]
    assert client.delete(f"/api/sanitize/{identifier}", headers=ORIGIN).status_code == 200
    assert identifier not in app.state.drafts.entries


def test_sanitize_expiry_retry_and_conflicting_inputs(env, monkeypatch):
    client, app, s3 = env
    actions = [{"action": "MASK", "targets": ["PHONE"]}]
    identifier = sanitize_upload(client, b"010-1234-5678", actions).json()["sanitize_id"]
    assert (
        client.post(
            "/api/transfers",
            headers=ORIGIN,
            files={"file": ("a.txt", b"hi")},
            data={"sanitize_id": identifier, "user_approved": "true"},
        ).status_code
        == 422
    )
    app.state.settings.s3_bucket = "no-such-bucket"
    assert send_draft(client, identifier, user_approved="true").status_code == 502
    assert identifier in app.state.drafts.entries
    app.state.settings.s3_bucket = "securedrop-test"
    assert send_draft(client, identifier, user_approved="true").status_code == 200
    identifier = sanitize_upload(client, b"010-1234-5678", actions).json()["sanitize_id"]
    app.state.drafts.entries[identifier] = replace(app.state.drafts.entries[identifier], deadline=0)
    assert send_draft(client, identifier, user_approved="true").status_code == 410
    assert identifier not in app.state.drafts.entries
    assert s3.list_objects_v2(Bucket="securedrop-test")["KeyCount"] == 1


def test_large_multipart_never_rolls_to_disk(env, monkeypatch):
    _, app, _ = env

    def forbidden_rollover(self):
        raise AssertionError("File contents must stay in memory")

    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", forbidden_rollover)
    settings = app.state.settings.model_copy(update={"max_file_mb": 20})
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        content = b"name,phone\n" + b"Kim,010-1234-5678\n" * 65_000
        assert len(content) > 1024 * 1024
        result = sanitize_upload(client, content, [{"action": "MASK", "targets": ["PHONE"]}])
        assert result.status_code == 200, result.text
        assert result.json()["clean"]
        assert upload(client, b"x" * (2 * 1024 * 1024), filename="a.pdf").status_code == 200


@pytest.mark.parametrize(
    "message,action,targets",
    [
        ("주민등록번호만 가리고 보내줘", "MASK", ["RRN"]),
        ("전화번호 삭제해줘", "DELETE", ["PHONE"]),
        ("전체 마스킹", "MASK", ["RRN", "PHONE"]),
        ("전송 취소", "CANCEL", []),
        ("파일을 보내줘", "CLARIFY", []),
        ("주민번호 마스킹하지 마", "CLARIFY", []),
        ("주민번호 마스킹하고 전화번호 삭제", "CLARIFY", []),
        ("이메일 삭제", "CLARIFY", []),
    ],
)
def test_assistant_metadata_and_fixed_actions(env, message, action, targets):
    client, _, _ = env
    context = {
        "filename": "private-name.csv",
        "findings": [{"type": "RRN", "count": 2}, {"type": "PHONE", "count": 1}],
    }
    response = client.post(
        "/api/assist", headers=ORIGIN, json={"message": message, "context": context}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["action"] == action and result["targets"] == targets
    assert {option["action"] for option in result["options"]} == {"MASK", "DELETE", "CANCEL"}
    assert "private-name.csv" not in response.text
    assert client.get("/api/logs", headers=ORIGIN).json() == {"items": []}
    context["text"] = "900101-1234568"
    rejected = client.post(
        "/api/assist", headers=ORIGIN, json={"message": message, "context": context}
    )
    assert rejected.status_code == 422 and "900101-1234568" not in rejected.text


def test_scanning_and_sanitization_use_same_boundary_rules(env):
    client, _, _ = env
    # A token may exceed the old 1,000-character overlap used by file scanning.
    text = " " * 48_995 + "sk-" + "a" * 2000 + " someone@example.com"
    assert scan_file(text) == scan(text)
    response = sanitize_upload(
        client, text.encode(), [{"action": "MASK", "targets": ["API_KEY", "EMAIL"]}]
    )
    result = response.json()
    assert result["findings_before"] == scan(text)["findings"]
    assert result["clean"] and not result["findings_after"]


def test_drafts_expire_without_requests_and_clear_on_shutdown(env, monkeypatch):
    _, app, _ = env
    separate = create_app(app.state.settings)
    drafts = separate.state.drafts
    with TestClient(separate) as client:
        identifier = sanitize_upload(
            client, b"010-1234-5678", [{"action": "MASK", "targets": ["PHONE"]}]
        ).json()["sanitize_id"]
        drafts.entries[identifier] = replace(drafts.entries[identifier], deadline=0)
        purged = Event()
        original_purge = drafts.purge

        def observed_purge():
            original_purge()
            purged.set()

        monkeypatch.setattr(drafts, "purge", observed_purge)
        assert purged.wait(3), "Periodic expiration did not run"
        assert identifier not in drafts.entries
        identifier = sanitize_upload(
            client, b"010-1234-5678", [{"action": "MASK", "targets": ["PHONE"]}]
        ).json()["sanitize_id"]
        assert identifier in drafts.entries
    assert not drafts.entries


def test_draft_capacity_and_simultaneous_transfer_protection():
    from errors import APIError
    from sanitizer import Drafts

    drafts = Drafts(ttl=600, max_bytes=10)
    identifier = drafts.add(b"123456", "file.txt")
    with pytest.raises(APIError) as capacity:
        drafts.add(b"12345", "another.txt")
    assert capacity.value.status == 503
    assert drafts.checkout(identifier).content == b"123456"
    with pytest.raises(APIError) as busy:
        drafts.checkout(identifier)
    assert busy.value.status == 409
    drafts.finish(identifier, False)
    assert drafts.checkout(identifier).content == b"123456"
    drafts.finish(identifier, True)
    assert not drafts.entries
