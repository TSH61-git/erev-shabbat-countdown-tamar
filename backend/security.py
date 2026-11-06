from __future__ import annotations

from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# The backend acts with the user's Claude Code login, so only this machine's own page may use it.
LOCAL_HOSTS = ["127.0.0.1", "localhost"]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def allowed_origins(port: int) -> set[str]:
    return {f"http://{h}:{port}" for h in LOCAL_HOSTS}


class LocalWritesOnly:
    # A page on another site can send a "simple" cross-origin POST (text/plain, no preflight)
    # to 127.0.0.1. Requiring our own Origin plus a JSON body closes that, without CORS.

    def __init__(self, app: ASGIApp, port: int):
        self.app, self.origins = app, allowed_origins(port)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
            if headers.get("origin") not in self.origins:
                await JSONResponse({"detail": "cross-origin request refused"}, 403)(scope, receive, send)
                return
            if headers.get("content-type", "").split(";")[0].strip() != "application/json":
                await JSONResponse({"detail": "send JSON"}, 415)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def protect(app, port: int) -> None:
    # Added last = runs first: a foreign Host header (DNS rebinding) is rejected before anything else.
    app.add_middleware(LocalWritesOnly, port=port)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)
