"""
tests/test_internal_error_text.py — an unexpected error never shows its
internal text to the client.

The global error handler returned str(e) and the exception's type for any
uncaught error, and /api/comps and /api/vin did the same in their own
`except Exception` blocks. Whatever the exception carried — database
constraint details with customer data, connection strings, file paths — went
to whoever triggered the error. The client now gets a generic message with a
short reference; the full error, under the same reference, goes to the
server log. SYNTHETIC; no network.
"""
from __future__ import annotations

import logging

from tests.conftest import register, set_plan

LEAK = ("DETAIL: Key (email)=(someone@private.example) already exists; "
        "postgresql://flip:hunter2@db.internal:5432/flipfirst")


def _boom(*a, **k):
    raise RuntimeError(LEAK)


def _assert_hidden(body: str) -> None:
    for secret in ("someone@private.example", "hunter2", "db.internal", "RuntimeError"):
        assert secret not in body, f"{secret!r} reached the client: {body!r}"


def _logged(caplog, ref: str) -> bool:
    return any(ref in r.getMessage() and "someone@private.example" in r.getMessage()
               for r in caplog.records)


def test_uncaught_api_error_hides_its_text_and_logs_it_under_a_reference(client, monkeypatch,
                                                                         caplog):
    import workspace
    register(client, "u@example.com")
    monkeypatch.setattr(workspace, "list_for", _boom)
    with caplog.at_level(logging.ERROR, logger="sniper"):
        r = client.get("/api/opportunities")
    assert r.status_code == 500
    _assert_hidden(r.get_data(as_text=True))
    ref = r.get_json()["ref"]
    assert ref and ref in r.get_json()["error"]
    assert _logged(caplog, ref)                       # the owner can still find the real error


def test_uncaught_page_error_hides_its_text(app):
    import server
    with app.test_request_context("/m"):
        try:
            raise RuntimeError(LEAK)
        except RuntimeError as e:
            body, code = server._json_error(e)
    assert code == 500
    _assert_hidden(body)


def test_comps_error_hides_its_text(client, monkeypatch):
    import comps
    register(client, "s@example.com")
    set_plan("s@example.com", "starter")
    monkeypatch.setattr(comps, "comps_for_listing", _boom)
    r = client.post("/api/comps", json={"year": 2015, "make": "Toyota", "model": "Camry"})
    assert r.status_code == 500
    _assert_hidden(r.get_data(as_text=True))


def test_vin_error_hides_its_text(client, monkeypatch):
    import vin
    register(client, "p@example.com")
    set_plan("p@example.com", "pro")
    monkeypatch.setattr(vin, "report", _boom)
    r = client.post("/api/vin", json={"vin": "1HGCM82633A004352"})
    assert r.status_code == 500
    _assert_hidden(r.get_data(as_text=True))


def test_expected_errors_keep_their_messages(client):
    """Unchanged: HTTP errors (404, 405) and validation messages still say
    what is wrong."""
    register(client, "v@example.com")
    r = client.get("/api/no-such-route")
    assert r.status_code == 404 and r.get_json()["error"]
    set_plan("v@example.com", "starter")
    r = client.post("/api/comps", json={"year": 2015})
    assert r.status_code == 400 and "need year/make/model" in r.get_json()["error"]
