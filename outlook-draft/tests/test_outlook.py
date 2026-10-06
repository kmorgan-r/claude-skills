import base64
import http.client
import io
import json
import pathlib
import sys
import types
import urllib.error
import urllib.parse

import pytest

from conftest import _load

outlook = _load("outlook")


class Fake:
    """Stands in for urllib_transport: records every call, answers from a handler."""

    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler or (lambda method, url, data, headers: (200, {}, {}))

    def __call__(self, method, url, data, headers):
        self.calls.append(types.SimpleNamespace(method=method, url=url, data=data, headers=headers))
        status, hdrs, body = self.handler(method, url, data, headers)
        raw = body if isinstance(body, bytes) else b"" if body is None else json.dumps(body).encode()
        return status, hdrs, raw

    def json(self, i):
        return json.loads(self.calls[i].data)


def _sequence(*responses):
    it = iter(responses)
    return lambda m, u, d, h: next(it)


def graph(fake, sleeps=None):
    return outlook.Graph("tok", fake, (sleeps if sleeps is not None else []).append)


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("OUTLOOK_DRAFT_HOME", str(home))
    return home


# --- Graph wrapper -----------------------------------------------------------

def test_load_does_not_import_msal(monkeypatch):
    monkeypatch.setitem(sys.modules, "msal", None)
    monkeypatch.setitem(sys.modules, "msal_extensions", None)
    _load("outlook")


def test_call_sends_bearer_and_json_and_parses_response():
    fake = Fake(lambda m, u, d, h: (201, {}, {"id": "m1"}))
    out = graph(fake).call("POST", "/me/messages", {"subject": "Hi"})
    c = fake.calls[0]
    assert out == {"id": "m1"}
    assert c.url == "https://graph.microsoft.com/v1.0/me/messages"
    assert c.headers["Authorization"] == "Bearer tok"
    assert c.headers["Content-Type"] == "application/json"
    assert json.loads(c.data) == {"subject": "Hi"}


def test_absolute_url_without_auth_has_no_authorization_header():
    fake = Fake()
    graph(fake).call("PUT", "https://outlook.office.com/up?authtoken=x", data=b"abc", auth=False)
    assert fake.calls[0].url == "https://outlook.office.com/up?authtoken=x"
    assert "Authorization" not in fake.calls[0].headers


def test_empty_response_body_returns_none():
    fake = Fake(lambda m, u, d, h: (201, {}, None))
    assert graph(fake).call("PUT", "https://outlook.office.com/up", data=b"a", auth=False) is None


def test_429_is_retried_after_retry_after_seconds():
    sleeps = []
    fake = Fake(_sequence((429, {"Retry-After": "2"}, None), (200, {}, {"ok": 1})))
    assert graph(fake, sleeps).call("GET", "/me") == {"ok": 1}
    assert sleeps == [2]
    assert len(fake.calls) == 2


def test_429_gives_up_after_three_retries():
    sleeps = []
    err = {"error": {"code": "TooManyRequests", "message": "slow down"}}
    fake = Fake(lambda m, u, d, h: (429, {"Retry-After": "1"}, err))
    with pytest.raises(outlook.GraphError) as e:
        graph(fake, sleeps).call("POST", "/me/messages", {})
    assert len(fake.calls) == 4
    assert sleeps == [1, 1, 1]
    assert (e.value.status, e.value.code) == (429, "TooManyRequests")


@pytest.mark.parametrize("headers, expected", [
    ({}, 1),
    ({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, 1),
    ({"retry-after": "5"}, 5),
    ({"Retry-After": "600"}, 30),
])
def test_retry_after_parsing(headers, expected):
    sleeps = []
    fake = Fake(_sequence((429, headers, None), (200, {}, {})))
    graph(fake, sleeps).call("GET", "/me")
    assert sleeps == [expected]


def test_503_is_retried_on_get():
    fake = Fake(_sequence((503, {}, None), (200, {}, {"ok": 1})))
    assert graph(fake).call("GET", "/me") == {"ok": 1}


def test_503_on_create_is_not_retried():
    fake = Fake(lambda m, u, d, h: (503, {}, None))
    with pytest.raises(outlook.GraphError):
        graph(fake).call("POST", "/me/messages", {"subject": "x"})
    assert len(fake.calls) == 1


def test_503_is_retried_when_caller_opts_in():
    fake = Fake(_sequence((503, {}, None), (200, {}, None)))
    graph(fake).call("PUT", "https://outlook.office.com/up", data=b"a", auth=False, retry_503=True)
    assert len(fake.calls) == 2


def test_graph_error_body_is_parsed():
    fake = Fake(lambda m, u, d, h: (404, {}, {"error": {"code": "ErrorItemNotFound", "message": "gone"}}))
    with pytest.raises(outlook.GraphError) as e:
        graph(fake).call("GET", "/me/messages/x")
    assert (e.value.status, e.value.code, e.value.message) == (404, "ErrorItemNotFound", "gone")


def test_non_json_error_body_is_kept_as_message():
    fake = Fake(lambda m, u, d, h: (502, {}, b"<html>bad gateway</html>"))
    with pytest.raises(outlook.GraphError) as e:
        graph(fake).call("POST", "/me/messages", {})
    assert e.value.code == "" and "bad gateway" in e.value.message


@pytest.mark.parametrize("exc", [
    urllib.error.URLError("getaddrinfo failed"), TimeoutError("timed out"), http.client.IncompleteRead(b"ab"),
])
def test_network_failure_is_graph_error_with_status_0(exc):
    def down(m, u, d, h):
        raise exc
    with pytest.raises(outlook.GraphError) as e:
        graph(Fake(down)).call("GET", "/me")
    assert (e.value.status, e.value.code, e.value.message) == (0, "network", str(exc))


def test_non_json_success_body_is_graph_error():
    fake = Fake(lambda m, u, d, h: (200, {}, b"<html>proxy login</html>"))
    with pytest.raises(outlook.GraphError) as e:
        graph(fake).call("GET", "/me")
    assert (e.value.status, e.value.code) == (200, "bad-response")


def test_urllib_transport_returns_http_errors_instead_of_raising(monkeypatch):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "3"}, io.BytesIO(b"{}"))
    monkeypatch.setattr(outlook.urllib.request, "urlopen", urlopen)
    assert outlook.urllib_transport("GET", "https://graph.microsoft.com/v1.0/me", None, {}) == \
        (429, {"Retry-After": "3"}, b"{}")


def test_unreadable_config_is_setup_error(private_home):
    (private_home / "config.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(outlook.SetupError):
        outlook.load_config()


@pytest.mark.parametrize("error", ["invalid_token", None, [1]])
def test_error_value_that_is_not_an_object_stays_graph_error(error):
    fake = Fake(lambda m, u, d, h: (401, {}, {"error": error}))
    with pytest.raises(outlook.GraphError) as e:
        graph(fake).call("GET", "/me")
    assert (e.value.status, e.value.code) == (401, "")
