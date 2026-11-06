from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import hebrew, views
from backend.chat import Busy, ChatService
from backend.security import protect
from SDK.agent import ClientFactory
from shared import config, hebcal
from shared.store import DataStore

DEFAULT_PORT = 8765
USER_NAME = "תמר"
FRONTEND = config.PROJECT_ROOT / "frontend"

# Python on Windows takes MIME types from the registry, where .js is sometimes text/plain;
# browsers then refuse to run the page's ES modules.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

# Everything the page needs is served from here (filtered network, and nothing remote
# should ever run next to a backend that acts with the user's Claude Code login).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def default_data_dir() -> Path:
    # Its own folder: the scenario runs wipe data/sdk and data/api, and this one must survive.
    return config.DATA_ROOT / "app"


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DoneIn(BaseModel):
    # Keys name a row of the list: a task, or a checklist item by its text.
    key: str = Field(max_length=120, pattern=r"^(task|checklist):\S.*$")
    done: bool


async def sse(queue):
    while (item := await queue.get()) is not None:
        event, data = item
        yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(data_dir: str | Path | None = None, clock: views.Clock = views.jerusalem_now,
               fetch: hebcal.Fetch = hebcal.fetch_json, port: int = DEFAULT_PORT,
               client_factory: ClientFactory | None = None, user_name: str = USER_NAME,
               url_fetch: hebrew.UrlFetch | None = None) -> FastAPI:
    store = DataStore(data_dir or default_data_dir())
    chat = ChatService(store, clock, fetch, client_factory)
    app = FastAPI(title="Erev Shabbat Countdown", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.clock, app.state.fetch, app.state.chat = store, clock, fetch, chat
    protect(app, port)

    @app.post("/api/chat")
    async def post_chat(body: ChatIn):
        text = body.text.strip()
        if not text:
            raise HTTPException(422, "empty message")
        try:
            queue = chat.start(text)
        except Busy as e:
            raise HTTPException(409, str(e)) from None
        return StreamingResponse(sse(queue), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/history")
    def history():
        return chat.history()

    @app.post("/api/session/new")
    def new_session():
        try:
            chat.new_conversation()
        except Busy as e:
            raise HTTPException(409, str(e)) from None
        return {"ok": True}

    # async on purpose: it runs on the event loop, so two quick clicks are applied one after
    # the other instead of racing on the same file from two threads.
    @app.post("/api/done")
    async def post_done(body: DoneIn):
        return views.set_done(store, clock(), body.key, body.done)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        # The saved files change behind the browser's back (through the agent), and the
        # page files change between phases, so the browser must never reuse an old copy.
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # Plain "def" routes: FastAPI runs them in a thread, so file reads and a Hebcal
    # cache miss never block the event loop that streams the chat.
    @app.get("/api/health")
    def health():
        return {**views.health(store, clock(), config.shown(store.root), user_name), "busy": chat.busy}

    @app.get("/api/profile")
    def profile():
        return views.profile(store)

    @app.get("/api/tasks")
    def tasks():
        return views.tasks(store)

    @app.get("/api/checklist")
    def checklist():
        return views.checklist(store)

    @app.get("/api/week")
    def week():
        return views.week(store, clock())

    @app.get("/api/candles")
    def candles():
        return views.candles(store, clock(), fetch, url_fetch)

    @app.get("/api/timeline")
    def timeline():
        return views.timeline(store, clock(), fetch)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND / "index.html")

    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
    return app
