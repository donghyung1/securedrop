"""Shared CORS configuration for full deployments and targeted CORS updates."""

import base64
import hashlib
import json


def extension_origin(manifest_path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    key = base64.b64decode(manifest["key"], validate=True)
    digest = hashlib.sha256(key).hexdigest()[:32]
    identifier = "".join(chr(ord("a") + int(char, 16)) for char in digest)
    return f"chrome-extension://{identifier}"


def configure_gateway_responses(api, api_id):
    # Gateway errors contain only generic messages. Reflect Origin here so each
    # client can read them; Lambda keeps the explicit allowlist for application data.
    # Gateway Responses support header mappings, not conditional VTL headers.
    headers = {
        "gatewayresponse.header.Access-Control-Allow-Origin": "method.request.header.Origin",
        "gatewayresponse.header.Access-Control-Allow-Headers": (
            "'Content-Type,X-API-Key,X-Manage-Token,Authorization'"
        ),
        "gatewayresponse.header.Access-Control-Allow-Methods": "'GET,POST,DELETE,OPTIONS'",
        "gatewayresponse.header.Vary": "'Origin'",
        "gatewayresponse.header.Cache-Control": "'no-store'",
    }
    for kind, code, message in [
        ("DEFAULT_4XX", "VALIDATION_ERROR", "Request rejected"),
        ("DEFAULT_5XX", "STORAGE_ERROR", "Service unavailable"),
        ("UNAUTHORIZED", "UNAUTHORIZED", "Authentication required"),
        ("ACCESS_DENIED", "FORBIDDEN", "Access denied"),
        ("INVALID_API_KEY", "UNAUTHORIZED", "API key required"),
        ("THROTTLED", "RATE_LIMITED", "Too many requests"),
        ("QUOTA_EXCEEDED", "RATE_LIMITED", "Quota exceeded"),
    ]:
        api.put_gateway_response(
            restApiId=api_id,
            responseType=kind,
            responseParameters=headers,
            responseTemplates={
                "application/json": json.dumps({"error": {"code": code, "message": message}})
            },
        )


def update_lambda_origins(lam, function, origins):
    current = lam.get_function_configuration(FunctionName=function)
    variables = current.get("Environment", {}).get("Variables", {}).copy()
    existing = variables.get("ALLOWED_ORIGINS", "")
    if existing:
        existing = (
            json.loads(existing) if existing.lstrip().startswith("[") else existing.split(",")
        )
    combined = list(
        dict.fromkeys(origin.strip() for origin in [*origins, *existing] if origin.strip())
    )
    if "*" in combined:
        raise ValueError("Explicit CORS origins are required")
    variables["ALLOWED_ORIGINS"] = ",".join(combined)
    lam.update_function_configuration(
        FunctionName=function,
        RevisionId=current["RevisionId"],
        Environment={"Variables": variables},
    )
    lam.get_waiter("function_updated_v2").wait(FunctionName=function)
    return combined
