import asyncio
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware

from config import Settings
from detector import TYPES, scan, scan_file
from errors import APIError
from intent import interpret
from multipart_memory import MemoryUploadRoute
from sanitizer import Drafts, decode_text, sanitize
from storage import Storage, qr_data
from store import Store, now

DetectionType = Literal["RRN", "PHONE", "EMAIL", "AWS_ACCESS_KEY", "API_KEY", "CARD"]
Source = Literal["web", "extension", "plugin"]
SCAN_EXTENSIONS = {".txt", ".csv"}
ALLOWED_EXTENSIONS = SCAN_EXTENSIONS | {
    ".json",
    ".md",
    ".log",
    ".xml",
    ".yaml",
    ".yml",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
}


class ScanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=50_000)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: DetectionType
    count: int = Field(ge=1)


class SanitizeAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["MASK", "DELETE", "CANCEL"]
    targets: list[DetectionType] = Field(default_factory=list, max_length=len(TYPES))


class SanitizeResult(BaseModel):
    sanitize_id: str | None
    findings_before: list[Finding]
    findings_after: list[Finding]
    preview: str
    clean: bool
    cancelled: bool = False


class AssistContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(default="", max_length=255)
    findings: list[Finding] = Field(max_length=len(TYPES))


class AssistInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="", max_length=2000)
    context: AssistContext


class AssistOption(BaseModel):
    action: Literal["MASK", "DELETE", "CANCEL"]
    targets: list[DetectionType]
    label: str


class AssistResult(BaseModel):
    action: Literal["MASK", "DELETE", "CANCEL", "CLARIFY"]
    targets: list[DetectionType]
    reply: str
    options: list[AssistOption]


class ScanResult(BaseModel):
    blocked: bool
    findings: list[Finding]


class FileScanResult(ScanResult):
    filename: str


class TransferResult(BaseModel):
    id: str
    url: str
    download_url: str
    qr: str
    expires_at: str


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResult(BaseModel):
    error: ErrorDetail


class UnscannedResult(ErrorResult):
    confirmation_required: bool
    scan_supported: bool


class SensitiveResult(ErrorResult, ScanResult):
    confirmation_required: bool
    sanitize_available: bool


class LogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: Literal["BLOCK"]
    types: list[DetectionType] = Field(min_length=1, max_length=len(TYPES))
    source: Literal["web", "extension"]


class BodyLimitMiddleware:
    """Bound chunked requests too, before multipart parsing creates temporary files."""

    def __init__(self, app, limit: int, scan_limit: int):
        self.app, self.limit, self.scan_limit = app, limit, scan_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        body = bytearray()
        limit = self.scan_limit if scope["path"].rstrip("/") == "/api/scan/file" else self.limit
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > limit:
                response = JSONResponse(
                    {
                        "error": {
                            "code": "FILE_TOO_LARGE",
                            "message": "요청 크기 제한을 초과했습니다.",
                        }
                    },
                    status_code=413,
                )
                return await response(scope, receive, send)
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None, storage=None) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings.database_path)
    storage = storage or Storage(settings)
    drafts = Drafts(settings.sanitize_ttl_sec, settings.sanitize_memory_mb * 1024 * 1024)

    @asynccontextmanager
    async def lifespan(app):
        async def expire_drafts():
            while True:
                await asyncio.sleep(min(1, settings.sanitize_ttl_sec))
                drafts.purge()

        task = asyncio.create_task(expire_drafts())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            drafts.clear()

    app = FastAPI(
        title="SecureDrop Cloud",
        version="0.2.0",
        lifespan=lifespan,
        description="민감정보 탐지·정제 및 QR 전송 API. 플러그인은 /plugin/openapi.json을 사용합니다.",
        responses={
            code: {"model": ErrorResult}
            for code in (401, 403, 404, 410, 413, 415, 422, 429, 500, 502, 503)
        },
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=settings.allowed_origins,
                allow_methods=["GET", "POST", "DELETE"],
                allow_headers=["Content-Type", "X-API-Key", "X-Source"],
            ),
            Middleware(
                BodyLimitMiddleware,
                limit=settings.max_file_mb * 1024 * 1024 + 65_536,
                scan_limit=settings.scan_file_max_mb * 1024 * 1024 + 65_536,
            ),
        ],
    )
    app.router.route_class = MemoryUploadRoute
    app.state.store = store
    app.state.storage = storage
    app.state.settings = settings
    app.state.drafts = drafts
    frontend = Path(__file__).resolve().parent / "frontend"
    app.mount("/static", StaticFiles(directory=frontend), name="static")

    @app.get("/", include_in_schema=False)
    def frontend_index():
        return FileResponse(frontend / "index.html")

    key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def authenticate(request: Request, api_key: Annotated[str | None, Depends(key_header)]):
        declared_source = request.headers.get("X-Source", "web")
        if api_key is not None:
            expected = settings.plugin_api_key.get_secret_value()
            if not expected or not hmac.compare_digest(api_key.encode(), expected.encode()):
                raise APIError(401, "UNAUTHORIZED", "유효한 API Key가 필요합니다.")
            identity = hashlib.sha256(api_key.encode()).hexdigest()
            if not store.allow_plugin(identity, time.time()):
                raise APIError(429, "RATE_LIMITED", "분당 30회 호출 한도를 초과했습니다.")
            request.state.source = "plugin"
        elif (
            request.url.path.startswith("/plugin/")
            or declared_source == "plugin"
            or request.headers.get("origin") not in settings.allowed_origins
        ):
            raise APIError(401, "UNAUTHORIZED", "허용된 웹 출처 또는 API Key가 필요합니다.")
        else:
            if declared_source not in ("web", "extension"):
                raise APIError(422, "VALIDATION_ERROR", "X-Source 값이 올바르지 않습니다.")
            request.state.source = declared_source

    auth = Depends(authenticate)

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        headers = {"Retry-After": "60"} if exc.status == 429 else None
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}},
            status_code=exc.status,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's default detail contains the submitted input. Never echo it.
        return JSONResponse(
            {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "요청 형식이나 입력값을 확인하세요.",
                }
            },
            status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": "NOT_FOUND" if exc.status_code == 404 else "VALIDATION_ERROR",
                    "message": "요청을 처리할 수 없습니다.",
                }
            },
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "서버에서 요청을 처리하지 못했습니다.",
                }
            },
            status_code=500,
        )

    @app.middleware("http")
    async def privacy_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "region": settings.aws_region}

    @app.post("/api/scan", response_model=ScanResult, operation_id="scan_text", dependencies=[auth])
    @app.post(
        "/plugin/scan",
        response_model=ScanResult,
        operation_id="plugin_scan_text",
        dependencies=[auth],
    )
    def scan_text(payload: ScanInput):
        return scan(payload.text)

    def validate_filename(filename, allowed=ALLOWED_EXTENSIONS):
        filename = PurePosixPath((filename or "").replace("\\", "/")).name
        extension = PurePosixPath(filename).suffix.lower()
        if extension not in allowed or any(ord(c) < 32 for c in filename):
            status = 415 if allowed == SCAN_EXTENSIONS else 422
            code = "UNSUPPORTED_TYPE" if status == 415 else "VALIDATION_ERROR"
            raise APIError(status, code, "허용되지 않는 파일 형식입니다.")
        return filename, extension

    async def read_file(file, max_mb=None):
        max_mb = settings.max_file_mb if max_mb is None else max_mb
        content = await file.read(max_mb * 1024 * 1024 + 1)
        if len(content) > max_mb * 1024 * 1024:
            raise APIError(413, "FILE_TOO_LARGE", f"파일 크기는 최대 {max_mb}MiB입니다.")
        if not content:
            raise APIError(422, "VALIDATION_ERROR", "빈 파일은 처리할 수 없습니다.")
        return content

    @app.post("/api/scan/file", response_model=FileScanResult, dependencies=[auth])
    async def scan_uploaded_file(file: Annotated[UploadFile, File()]):
        try:
            filename, _ = validate_filename(file.filename, SCAN_EXTENSIONS)
            content = await read_file(file, settings.scan_file_max_mb)
            result = await run_in_threadpool(scan_file, decode_text(content))
            return {"filename": filename, **result}
        finally:
            await file.close()

    @app.post("/api/assist", response_model=AssistResult, dependencies=[auth])
    def assist(payload: AssistInput):
        return interpret(
            payload.message, [finding.model_dump() for finding in payload.context.findings]
        )

    @app.post("/api/sanitize", response_model=SanitizeResult, dependencies=[auth])
    async def sanitize_file(
        request: Request,
        file: Annotated[UploadFile, File()],
        actions: Annotated[str, Form()],
    ):
        try:
            filename, _ = validate_filename(file.filename, SCAN_EXTENSIONS)
            try:
                parsed = TypeAdapter(list[SanitizeAction]).validate_json(actions)
            except ValidationError as exc:
                raise APIError(422, "VALIDATION_ERROR", "정제 조치 형식을 확인하세요.") from exc
            if not parsed or len(parsed) > len(TYPES):
                raise APIError(422, "VALIDATION_ERROR", "정제 조치를 선택하세요.")
            cancelled = any(item.action == "CANCEL" for item in parsed)
            selected = {}
            for item in parsed:
                if (cancelled and (len(parsed) != 1 or item.targets)) or (
                    not cancelled and not item.targets
                ):
                    raise APIError(
                        422, "VALIDATION_ERROR", "취소와 정제 조치를 함께 적용할 수 없습니다."
                    )
                for target in item.targets:
                    if target in selected:
                        raise APIError(422, "VALIDATION_ERROR", "유형별 조치는 하나만 선택하세요.")
                    selected[target] = item.action
            content = await read_file(file)
            text = decode_text(content)
            before = await run_in_threadpool(scan_file, text)
            if cancelled:
                return {
                    "sanitize_id": None,
                    "findings_before": before["findings"],
                    "findings_after": [],
                    "preview": "",
                    "clean": False,
                    "cancelled": True,
                }
            processed = await run_in_threadpool(sanitize, text, selected)
            after = await run_in_threadpool(scan_file, processed)
            processed_bytes = processed.encode("utf-8")
            if len(processed_bytes) > settings.max_file_mb * 1024 * 1024:
                raise APIError(413, "FILE_TOO_LARGE", "UTF-8 처리본 크기 제한을 초과했습니다.")
            identifier = drafts.add(processed_bytes, filename)
            try:
                await run_in_threadpool(store.log, "SANITIZE", list(selected), request.state.source)
            except Exception:
                drafts.finish(identifier, True)
                raise
            return {
                "sanitize_id": identifier,
                "findings_before": before["findings"],
                "findings_after": after["findings"],
                "preview": "\n".join(processed.splitlines()[:20])[:10_000],
                "clean": not after["blocked"],
            }
        finally:
            await file.close()

    @app.delete("/api/sanitize/{identifier}", dependencies=[auth])
    def cancel_draft(identifier: str):
        drafts.checkout(identifier)
        drafts.finish(identifier, True)
        return {"status": "cancelled"}

    @app.post(
        "/api/transfers",
        response_model=TransferResult,
        operation_id="create_transfer",
        dependencies=[auth],
        responses={409: {"model": SensitiveResult}, 415: {"model": UnscannedResult}},
    )
    @app.post(
        "/plugin/transfers",
        response_model=TransferResult,
        operation_id="plugin_create_transfer",
        dependencies=[auth],
        responses={409: {"model": SensitiveResult}, 415: {"model": UnscannedResult}},
    )
    async def create_transfer(
        request: Request,
        file: Annotated[UploadFile | None, File()] = None,
        sanitize_id: Annotated[str | None, Form()] = None,
        expires_in: Annotated[int | None, Form()] = None,
        confirm_sensitive: Annotated[bool, Form()] = False,
        confirm_unscanned: Annotated[bool, Form()] = False,
        user_approved: Annotated[bool, Form()] = False,
    ):
        """플러그인과 처리본 전송은 미리보기 등 사용자 확인 후 user_approved=true가 필요합니다."""
        checked_out = False
        success = False
        try:
            expires = settings.default_expires_sec if expires_in is None else expires_in
            if expires not in (300, 600, 3600) or expires > settings.max_expires_sec:
                raise APIError(
                    422, "VALIDATION_ERROR", "만료 시간은 300, 600, 3600초 중 선택하세요."
                )
            if request.state.source == "plugin" and not user_approved:
                raise APIError(
                    403, "APPROVAL_REQUIRED", "전송 전 사용자의 명시적 승인이 필요합니다."
                )
            if (file is None) == (sanitize_id is None):
                raise APIError(
                    422, "VALIDATION_ERROR", "file 또는 sanitize_id 중 하나만 지정하세요."
                )
            if sanitize_id is not None:
                if not user_approved:
                    raise APIError(
                        403, "APPROVAL_REQUIRED", "처리본 미리보기 확인 후 승인이 필요합니다."
                    )
                draft = drafts.checkout(sanitize_id)
                checked_out = True
                filename, extension = validate_filename(draft.filename)
                content = draft.content
                if len(content) > settings.max_file_mb * 1024 * 1024:
                    raise APIError(413, "FILE_TOO_LARGE", "처리본 크기 제한을 초과했습니다.")
            else:
                filename, extension = validate_filename(file.filename)
                content = await read_file(file)
            findings = []
            if extension not in SCAN_EXTENSIONS and not confirm_unscanned:
                return JSONResponse(
                    {
                        "error": {
                            "code": "UNSUPPORTED_TYPE",
                            "message": "이 형식은 민감정보 검사를 지원하지 않습니다. 확인 후 전송하세요.",
                        },
                        "confirmation_required": True,
                        "scan_supported": False,
                    },
                    status_code=415,
                )
            if extension in SCAN_EXTENSIONS:
                text = decode_text(content)
                result = await run_in_threadpool(scan_file, text)
                findings = [item["type"] for item in result["findings"]]
                if result["blocked"] and not confirm_sensitive:
                    await run_in_threadpool(store.log, "BLOCK", findings, request.state.source)
                    return JSONResponse(
                        {
                            "error": {
                                "code": "SENSITIVE_REMAINS" if sanitize_id else "SENSITIVE_DATA",
                                "message": "민감정보가 감지되었습니다. 정제하거나 위험을 확인한 후 다시 전송하세요.",
                            },
                            **result,
                            "confirmation_required": True,
                            "sanitize_available": extension in SCAN_EXTENSIONS,
                        },
                        status_code=409,
                    )
            identifier = uuid4().hex
            key = f"transfers/{identifier}"
            # Start the deadline before signing, so the status never outlives the signed URL.
            expires_at = (now() + timedelta(seconds=expires)).isoformat()
            url = await run_in_threadpool(storage.create, key, content, filename, expires)
            try:
                if settings.public_base_url:
                    download_url = f"{str(settings.public_base_url).rstrip('/')}/d/{identifier}"
                else:
                    download_url = str(request.url_for("download_transfer", identifier=identifier))
                qr = await run_in_threadpool(qr_data, download_url)
                await run_in_threadpool(
                    store.save_transfer, identifier, key, expires_at, findings, request.state.source
                )
            except Exception:
                await run_in_threadpool(storage.delete, key)
                raise
            success = True
            return {
                "id": identifier,
                "url": url,
                "download_url": download_url,
                "qr": qr,
                "expires_at": expires_at,
            }
        finally:
            if checked_out:
                drafts.finish(sanitize_id, success)
            if file is not None:
                await file.close()

    def get_transfer(identifier):
        row = store.transfer(identifier)
        if row is None or row["revoked"]:
            raise APIError(404, "NOT_FOUND", "전송을 찾을 수 없습니다.")
        return row

    @app.get("/d/{identifier}", include_in_schema=False)
    def download_transfer(identifier: str):
        # The random 128-bit ID is a bearer capability, like the S3 URL itself.
        row = get_transfer(identifier)
        remaining = int((datetime.fromisoformat(row["expires_at"]) - now()).total_seconds())
        if remaining <= 0:
            raise APIError(410, "EXPIRED", "만료된 전송입니다.")
        url = storage.download_url(row["object_key"], remaining)
        return RedirectResponse(url, status_code=302, headers={"Referrer-Policy": "no-referrer"})

    @app.get("/api/transfers/{identifier}", dependencies=[auth])
    def transfer_status(identifier: str):
        row = get_transfer(identifier)
        if datetime.fromisoformat(row["expires_at"]) <= now():
            raise APIError(410, "EXPIRED", "만료된 전송입니다.")
        return {
            "id": identifier,
            "status": "active",
            "expires_at": row["expires_at"],
            "downloaded": False,
        }

    @app.delete("/api/transfers/{identifier}", dependencies=[auth])
    def revoke_transfer(identifier: str, request: Request):
        row = get_transfer(identifier)
        storage.delete(row["object_key"])
        store.revoke(identifier, request.state.source)
        return {"id": identifier, "status": "revoked"}

    @app.post("/api/logs", dependencies=[auth])
    def create_log(payload: LogInput, request: Request):
        source = request.state.source
        if source == "plugin" or payload.source != source:
            raise APIError(422, "VALIDATION_ERROR", "로그 출처가 요청 출처와 일치하지 않습니다.")
        store.log(payload.event, list(payload.types), source)
        return {"ok": True}

    @app.get("/api/logs", dependencies=[auth])
    def get_logs(limit: Annotated[int, Query(ge=1, le=100)] = 50):
        return {"items": store.logs(limit)}

    @app.get("/plugin/openapi.json", include_in_schema=False)
    def plugin_schema():
        schema = get_openapi(title="SecureDrop plugin", version="0.2.0", routes=app.routes)
        schema["paths"] = {
            path: value
            for path, value in schema["paths"].items()
            if path in ("/plugin/scan", "/plugin/transfers")
        }
        # Only these two operations are exposed to agents; neither lists nor downloads files.
        for path, operations in schema["paths"].items():
            operations["post"]["operationId"] = (
                "scan_text" if path.endswith("scan") else "create_transfer"
            )
        return JSONResponse(json.loads(json.dumps(schema)))

    return app


app = create_app()
