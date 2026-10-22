import os

import pytest
import requests

from shared.net import use_os_trust_store


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # Offline tests must never depend on Hebcal or the Claude API being reachable.
    if os.environ.get("HEBCAL_LIVE") == "1":
        use_os_trust_store()
        return

    def blocked(*args, **kwargs):
        raise RuntimeError("network is blocked in tests")

    monkeypatch.setattr(requests.Session, "request", blocked)
