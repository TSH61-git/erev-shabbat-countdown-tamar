from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import backend.__main__ as entry
from backend.app import create_app, default_data_dir
from shared import config, memory_files as mem
from tests.fakes import FakeHebcal
from tests.sample_data import CHECKLIST, TASKS

PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
FRI_10 = datetime(2026, 11, 6, 10, 0)


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data" / "app"


@pytest.fixture
def client(data_dir) -> TestClient:
    app = create_app(data_dir, clock=lambda: FRI_10, fetch=FakeHebcal(), port=PORT)
    # The real browser address, so the Host check sees what it will see in use.
    return TestClient(app, base_url=ORIGIN)


def test_read_only_endpoints_on_an_empty_folder(client):
    for path in ("/api/profile", "/api/tasks", "/api/checklist", "/api/week",
                 "/api/candles", "/api/timeline", "/api/health"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-store"
    assert client.get("/api/profile").json() is None
    assert client.get("/api/timeline").json()["state"] == "empty"
    h = client.get("/api/health").json()
    assert h["ok"] and h["friday"] == "2026-11-06" and h["session_id"] is None


def test_endpoints_return_the_saved_state(client):
    store = client.app.state.store
    mem.update_profile(store, city="Jerusalem", ready_buffer_min=10)
    mem.upsert_tasks(store, TASKS)
    mem.set_checklist(store, CHECKLIST)
    store.write_json("session.json", {"session_id": "sess-1"})
    assert client.get("/api/profile").json()["ready_buffer_min"] == 10
    assert len(client.get("/api/tasks").json()["tasks"]) == 10
    assert client.get("/api/candles").json()["minutes_until_ready"] == 355
    tl = client.get("/api/timeline").json()
    assert tl["source"] == "preview" and tl["blocks"][-1]["kind"] == "checklist"
    assert client.get("/api/health").json()["session_id"] == "sess-1"


def test_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>ערב שבת</title>" in r.text
    assert '<html lang="he" dir="rtl">' in r.text


@pytest.mark.parametrize("host", ["evil.example", "192.168.1.5:8765"])
def test_foreign_host_header_is_refused(client, host):
    assert client.get("/api/profile", headers={"Host": host}).status_code == 400


def test_localhost_name_is_accepted(client):
    assert client.get("/api/profile", headers={"Host": f"localhost:{PORT}"}).status_code == 200


@pytest.mark.parametrize("headers, status", [
    ({"Content-Type": "application/json"}, 403),                                  # no Origin
    ({"Origin": "https://evil.example", "Content-Type": "application/json"}, 403),
    ({"Origin": f"http://127.0.0.1:{PORT + 1}", "Content-Type": "application/json"}, 403),
    ({"Origin": ORIGIN, "Content-Type": "text/plain"}, 415),                      # simple CORS request
    ({"Origin": ORIGIN, "Content-Type": "application/json"}, 405),                # passes the guard
])
def test_writes_need_our_origin_and_json(client, headers, status):
    assert client.post("/api/profile", content="{}", headers=headers).status_code == status


def test_default_folder_is_data_app_not_the_scenario_folders():
    assert default_data_dir() == config.DATA_ROOT / "app"
    assert create_app().state.store.root == (config.DATA_ROOT / "app").resolve()


def test_start_refuses_when_api_key_is_set(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(entry.uvicorn.Server, "run", lambda self: pytest.fail("server started"))
    assert entry.main(["--no-browser"]) == 2
    assert "ANTHROPIC_API_KEY is set" in capsys.readouterr().out


def test_start_binds_to_localhost_only(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seen = {}
    monkeypatch.setattr(entry, "use_os_trust_store", lambda: None)
    monkeypatch.setattr(entry.uvicorn.Server, "run", lambda self: seen.update(
        host=self.config.host, port=self.config.port, use_subprocess=self.config.use_subprocess))
    assert entry.main(["--no-browser", "--port", "8799"]) == 0
    # use_subprocess (reload/workers) is what makes uvicorn pick the selector loop on Windows.
    assert seen == {"host": "127.0.0.1", "port": 8799, "use_subprocess": False}


def test_editor_port_is_not_passed_to_claude_code(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "51234")
    monkeypatch.setattr(entry, "use_os_trust_store", lambda: None)
    monkeypatch.setattr(entry.uvicorn.Server, "run", lambda self: None)
    assert entry.main(["--no-browser"]) == 0
    assert "CLAUDE_CODE_SSE_PORT" not in entry.os.environ
    assert "ignoring CLAUDE_CODE_SSE_PORT" in capsys.readouterr().out


def test_ctrl_c_stops_quietly(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(entry, "use_os_trust_store", lambda: None)

    def interrupted(self):
        raise KeyboardInterrupt  # what uvicorn re-raises after its graceful shutdown
    monkeypatch.setattr(entry.uvicorn.Server, "run", interrupted)
    assert entry.main(["--no-browser"]) == 0
    out = capsys.readouterr()
    assert out.out.strip().endswith("[backend] stopped") and "Traceback" not in out.err


@pytest.mark.parametrize("path, kind", [
    ("/static/app.js", "text/javascript"), ("/static/chat.js", "text/javascript"),
    ("/static/rows.js", "text/javascript"), ("/static/tasks.js", "text/javascript"),
    ("/static/util.js", "text/javascript"),
    ("/static/styles.css", "text/css"),
])
def test_page_files_are_served_with_types_browsers_accept(client, path, kind):
    # ES modules are refused by the browser unless served as JavaScript.
    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith(kind)


def test_page_may_load_only_its_own_files(client):
    csp = client.get("/").headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "http" not in csp


def test_page_references_no_remote_assets():
    import re
    from backend.app import FRONTEND
    for f in FRONTEND.iterdir():
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"https?://(?!127\.0\.0\.1|www\.w3\.org/2000/svg)", text), f.name


def test_ticking_a_row_is_saved_for_this_friday(client):
    r = client.post("/api/done", json={"key": "task:להכין_חצילים", "done": True}, headers={"Origin": ORIGIN})
    assert r.status_code == 200 and r.json() == {"friday": "2026-11-06", "done": ["task:להכין_חצילים"]}
    saved = client.app.state.store.read_json("done/2026-11-06.json")
    assert saved["done"] == ["task:להכין_חצילים"]


@pytest.mark.parametrize("body, headers, status", [
    ({"key": "task:x", "done": True}, {}, 403),                                     # no Origin
    ({"key": "task:x", "done": True}, {"Origin": "https://evil.example"}, 403),
    ({"key": "rm -rf", "done": True}, {"Origin": ORIGIN}, 422),                      # unknown kind
    ({"key": "task:", "done": True}, {"Origin": ORIGIN}, 422),
    ({"key": "task:" + "x" * 200, "done": True}, {"Origin": ORIGIN}, 422),
    ({"key": "task:x", "done": "maybe"}, {"Origin": ORIGIN}, 422),
])
def test_done_endpoint_keeps_the_write_rules(client, body, headers, status):
    assert client.post("/api/done", json=body, headers=headers).status_code == status
    assert not client.app.state.store.exists("done")


def test_done_endpoint_refuses_text_plain(client):
    r = client.post("/api/done", content='{"key":"task:x","done":true}',
                    headers={"Origin": ORIGIN, "Content-Type": "text/plain"})
    assert r.status_code == 415


def test_health_names_the_user(data_dir):
    default = TestClient(create_app(data_dir, clock=lambda: FRI_10, port=PORT), base_url=ORIGIN)
    assert default.get("/api/health").json()["user"] == {"name": "תמר", "initial": "ת"}
    other = TestClient(create_app(data_dir, clock=lambda: FRI_10, port=PORT, user_name="Dana"), base_url=ORIGIN)
    assert other.get("/api/health").json()["user"] == {"name": "Dana", "initial": "D"}


def test_name_option_reaches_the_app(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(entry, "use_os_trust_store", lambda: None)
    seen = {}
    real = entry.create_app

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)
    monkeypatch.setattr(entry, "create_app", spy)
    monkeypatch.setattr(entry.uvicorn.Server, "run", lambda self: None)
    assert entry.main(["--no-browser", "--name", "רחל"]) == 0
    assert seen["user_name"] == "רחל"
