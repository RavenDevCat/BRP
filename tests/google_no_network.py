"""Opt-in test plugin: fail rather than make an unmocked external HTTP call."""
import pytest
import requests

@pytest.fixture(autouse=True)
def block_unmocked_http(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("External HTTP is disabled in the Google isolation suite")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
