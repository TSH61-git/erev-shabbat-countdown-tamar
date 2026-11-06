from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import views
from backend.security import protect
from shared import config, hebcal
from shared.store import DataStore

DEFAULT_PORT = 8765
FRONTEND = config.PROJECT_ROOT / "frontend"


def default_data_dir() -> Path:
    # Its own folder: the scenario runs wipe data/sdk and data/api, and this one must survive.
    return config.DATA_ROOT / "app"


def create_app(data_dir: str | Path | None = None, clock: views.Clock = views.jerusalem_now,
               fetch: hebcal.Fetch = hebcal.fetch_json, port: int = DEFAULT_PORT) -> FastAPI:
    store = DataStore(data_dir or default_data_dir())
    app = FastAPI(title="Erev Shabbat Countdown", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.clock, app.state.fetch = store, clock, fetch
    protect(app, port)

    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        response = await call_next(request)
        # The saved files change behind the browser's back (through the agent), so never reuse an old answer.
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # Plain "def" routes: FastAPI runs them in a thread, so file reads and a Hebcal
    # cache miss never block the event loop that streams the chat.
    @app.get("/api/health")
    def health():
        return views.health(store, clock(), config.shown(store.root))

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
        return views.candles(store, clock(), fetch)

    @app.get("/api/timeline")
    def timeline():
        return views.timeline(store, clock(), fetch)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND / "index.html")

    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
    return app
