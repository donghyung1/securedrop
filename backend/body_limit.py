from fastapi.responses import JSONResponse


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
