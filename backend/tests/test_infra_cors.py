import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from infra.cors import configure_gateway_responses, extension_origin, update_lambda_origins


def test_manifest_origin_matches_documented_extension():
    manifest = Path(__file__).resolve().parents[2] / "extension" / "manifest.json"
    assert extension_origin(manifest) == "chrome-extension://cbamkobbnpomdhcfndaenamfpoldkcno"


@pytest.mark.parametrize("encoded", [False, True])
def test_lambda_update_preserves_environment_and_existing_origins(encoded):
    lam = Mock()
    existing = ["https://existing.example", "chrome-extension://" + "a" * 32]
    variables = {
        "FILES_BUCKET": "keep-bucket",
        "ALLOWED_ORIGINS": json.dumps(existing) if encoded else ",".join(existing),
    }
    lam.get_function_configuration.return_value = {
        "RevisionId": "revision",
        "Environment": {"Variables": variables},
    }
    origins = update_lambda_origins(lam, "securedrop-api", ["https://new.example", existing[0]])
    assert origins == ["https://new.example", *existing]
    lam.update_function_configuration.assert_called_once_with(
        FunctionName="securedrop-api",
        RevisionId="revision",
        Environment={
            "Variables": {"FILES_BUCKET": "keep-bucket", "ALLOWED_ORIGINS": ",".join(origins)}
        },
    )
    assert variables["ALLOWED_ORIGINS"] != ",".join(origins)


def test_lambda_update_rejects_wildcard():
    lam = Mock()
    lam.get_function_configuration.return_value = {
        "Environment": {"Variables": {"ALLOWED_ORIGINS": "*"}}
    }
    with pytest.raises(ValueError, match="Explicit CORS origins"):
        update_lambda_origins(lam, "securedrop-api", ["https://new.example"])
    lam.update_function_configuration.assert_not_called()


def test_gateway_auth_and_throttle_errors_use_origin_mapping():
    api = Mock()
    configure_gateway_responses(api, "api-id")
    responses = {
        call.kwargs["responseType"]: call.kwargs for call in api.put_gateway_response.call_args_list
    }
    for kind, code in [
        ("UNAUTHORIZED", "UNAUTHORIZED"),
        ("ACCESS_DENIED", "FORBIDDEN"),
        ("INVALID_API_KEY", "UNAUTHORIZED"),
        ("THROTTLED", "RATE_LIMITED"),
        ("QUOTA_EXCEEDED", "RATE_LIMITED"),
    ]:
        response = responses[kind]
        headers = response["responseParameters"]
        assert (
            headers["gatewayresponse.header.Access-Control-Allow-Origin"]
            == "method.request.header.Origin"
        )
        assert headers["gatewayresponse.header.Vary"] == "'Origin'"
        assert headers["gatewayresponse.header.Cache-Control"] == "'no-store'"
        assert "gatewayresponse.header.Access-Control-Allow-Credentials" not in headers
        assert (
            json.loads(response["responseTemplates"]["application/json"])["error"]["code"] == code
        )
