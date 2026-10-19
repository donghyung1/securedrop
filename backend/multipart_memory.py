"""Keep bounded multipart uploads in RAM, including Starlette's parsing stage."""

from fastapi.routing import APIRoute
from starlette.formparsers import MultiPartException, MultiPartParser

from errors import APIError


class MemoryParser(MultiPartParser):
    # BodyLimitMiddleware bounds the entire body before parsing.
    spool_max_size = 21 * 1024 * 1024


class MemoryUploadRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            if (
                request.headers.get("content-type", "").split(";", 1)[0].lower()
                != "multipart/form-data"
            ):
                return await handler(request)
            parser = MemoryParser(request.headers, request.stream(), max_files=1, max_fields=10)
            try:
                form = await parser.parse()
            except (MultiPartException, ValueError) as exc:
                for file in parser._files_to_close_on_error:
                    file.close()
                raise APIError(422, "VALIDATION_ERROR", "파일 요청 형식을 확인하세요.") from exc
            except BaseException:
                for file in parser._files_to_close_on_error:
                    file.close()
                raise
            try:
                request._form = form
                return await handler(request)
            finally:
                await form.close()

        return handle
