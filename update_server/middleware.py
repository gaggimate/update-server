from __future__ import annotations

from fastapi import HTTPException, status
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from update_server import config
from update_server.security import is_admin_authorized

ADMIN_PATH_PREFIX = "/api/admin/"

# Everything the flasher page needs is served from this origin (see templates/flash.html and static/).
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "  # esp-web-tools (lit) injects component styles at runtime
    "font-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class AdminGuardMiddleware:
    """Authenticate admin requests and cap their size *before* any of the request body is read.

    FastAPI parses multipart bodies (spooling files to disk) before route dependencies or handler code run,
    so a check inside the route would let anonymous clients push arbitrarily large uploads at the server.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(ADMIN_PATH_PREFIX):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if not is_admin_authorized(headers.get("x-api-key")):
            response = JSONResponse({"detail": "Not found"}, status_code=status.HTTP_404_NOT_FOUND)
            await response(scope, receive, send)
            return

        limit = config.MAX_UPLOAD_REQUEST_SIZE_BYTES
        declared = headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            response = JSONResponse(
                {"detail": f"Request exceeds max upload size of {limit} bytes"},
                status_code=413,
            )
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            # Content-Length can be absent (chunked) or wrong, so count what actually arrives.
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Request exceeds max upload size of {limit} bytes",
                    )
            return message

        await self.app(scope, limited_receive, send)
