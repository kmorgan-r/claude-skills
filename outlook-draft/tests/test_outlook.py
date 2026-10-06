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


# --- spec validation and attachments ------------------------------------------

def _new_spec(**over):
    spec = {"mode": "new", "to": ["ana@x.com"], "cc": ["bo@x.com"], "subject": "Hello",
            "body_html": "<p>Hi Ana</p>", "attachments": []}
    spec.update(over)
    return spec


def test_valid_new_spec_passes():
    outlook.validate_spec(_new_spec())


@pytest.mark.parametrize("over", [
    {"attachments": ["C:/definitely/missing.pdf"]},
    {"attachments": ["<DIR>"]},
    {"mode": "reply"},
    {"subject": ""},
    {"mode": "forward"},
    {"to": "ana@x.com"},
    {"cc": [None]},
    {"subject": 5},
    {"body_html": ["<p>x</p>"]},
    {"mode": "reply", "reply_to_id": 7},
])
def test_invalid_spec_is_rejected(over, tmp_path):
    if over.get("attachments") == ["<DIR>"]:
        over = {"attachments": [str(tmp_path)]}
    with pytest.raises(outlook.SpecError):
        outlook.validate_spec(_new_spec(**over))


def test_attachment_over_graph_limit_is_rejected_before_any_call(tmp_path, monkeypatch):
    monkeypatch.setattr(outlook, "MAX_ATTACHMENT", 10)
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 11)
    with pytest.raises(outlook.SpecError, match="150 MB"):
        outlook.validate_spec(_new_spec(attachments=[str(path)]))
    path.write_bytes(b"x" * 10)
    outlook.validate_spec(_new_spec(attachments=[str(path)]))


def test_spec_must_be_an_object():
    with pytest.raises(outlook.SpecError):
        outlook.validate_spec(["mode", "new"])


def test_read_spec_accepts_utf8_bom(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"subject": "Café"}, ensure_ascii=False), encoding="utf-8-sig")
    assert outlook.read_spec(str(path)) == {"subject": "Café"}


def test_read_spec_bad_json_is_spec_error(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(outlook.SpecError):
        outlook.read_spec(str(path))


def _attach_handler(fail_names=()):
    def handler(m, u, d, h):
        if u.endswith("/attachments/createUploadSession"):
            return 201, {}, {"uploadUrl": "https://outlook.office.com/up?authtoken=t"}
        if u.endswith("/attachments"):
            if json.loads(d)["name"] in fail_names:
                return 500, {}, {"error": {"code": "ErrorInternalServerError", "message": "boom"}}
            return 201, {}, {"id": "A1"}
        if u.startswith("https://outlook.office.com/up"):
            return 200, {}, {}
        raise AssertionError(f"unexpected {m} {u}")
    return handler


def _file(tmp_path, name, size):
    path = tmp_path / name
    path.write_bytes((bytes(range(256)) * (size // 256 + 1))[:size])
    return str(path)


def test_small_file_goes_in_one_post(tmp_path):
    path = _file(tmp_path, "notes v2.txt", 10)
    fake = Fake(_attach_handler())
    outlook.attach(graph(fake), "D/1=", path)
    post = fake.json(0)
    assert fake.calls[0].url.endswith("/me/messages/D%2F1=/attachments")
    assert post["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert post["name"] == "notes v2.txt"
    assert base64.b64decode(post["contentBytes"]) == pathlib.Path(path).read_bytes()


def test_just_under_3mb_is_simple_and_exactly_3mb_uses_upload_session(tmp_path):
    under = _file(tmp_path, "under.bin", outlook.SIMPLE_MAX - 1)
    exact = _file(tmp_path, "exact.bin", outlook.SIMPLE_MAX)
    fake = Fake(_attach_handler())
    outlook.attach(graph(fake), "D1", under)
    outlook.attach(graph(fake), "D1", exact)
    assert fake.calls[0].url.endswith("/attachments")
    assert fake.calls[1].url.endswith("/attachments/createUploadSession")
    assert fake.json(1) == {"AttachmentItem": {"attachmentType": "file", "name": "exact.bin",
                                               "size": outlook.SIMPLE_MAX}}


def test_large_file_chunks_cover_file_without_auth_header(tmp_path):
    size = 2 * outlook.CHUNK + 123
    path = _file(tmp_path, "big.pdf", size)
    fake = Fake(_attach_handler())
    outlook.attach(graph(fake), "D1", path)
    puts = [c for c in fake.calls if c.method == "PUT"]
    assert [c.headers["Content-Range"] for c in puts] == [
        f"bytes 0-{outlook.CHUNK - 1}/{size}",
        f"bytes {outlook.CHUNK}-{2 * outlook.CHUNK - 1}/{size}",
        f"bytes {2 * outlook.CHUNK}-{size - 1}/{size}",
    ]
    assert [len(c.data) for c in puts] == [outlook.CHUNK, outlook.CHUNK, 123]
    assert b"".join(c.data for c in puts) == pathlib.Path(path).read_bytes()
    for c in puts:
        assert c.url == "https://outlook.office.com/up?authtoken=t"
        assert "Authorization" not in c.headers
        assert c.headers["Content-Type"] == "application/octet-stream"
        assert c.headers["Content-Length"] == str(len(c.data))


# --- drafts: new and reply ----------------------------------------------------

def _created(m, u, d, h):
    body = json.loads(d)
    return 201, {}, {"id": "D1", "webLink": "https://outlook/D1", "subject": body["subject"],
                     "toRecipients": body["toRecipients"], "ccRecipients": body["ccRecipients"]}


def _draft_handler(fail_names=()):
    attachments = _attach_handler(fail_names)

    def handler(m, u, d, h):
        if u.endswith("/me/messages"):
            return _created(m, u, d, h)
        return attachments(m, u, d, h)
    return handler


def test_insert_reply_after_body_tag_any_case_with_attributes():
    assert outlook.insert_reply('<BODY class="x"><p>q</p></BODY>', "<p>new</p>") == \
        '<BODY class="x"><p>new</p><p>q</p></BODY>'
    assert outlook.insert_reply("<p>q</p>", "<p>new</p>") == "<p>new</p><p>q</p>"


def test_merge_recipients_adds_and_dedupes_case_insensitively():
    existing = [{"emailAddress": {"name": "Ana", "address": "Ana@X.com"}}]
    merged = outlook.merge_recipients(existing, ["ana@x.com", "cy@x.com", "CY@x.com"])
    assert [r["emailAddress"]["address"] for r in merged] == ["Ana@X.com", "cy@x.com"]


def test_new_draft_payload_and_result():
    fake = Fake(_created)
    out = outlook.draft(graph(fake), _new_spec())
    sent = fake.json(0)
    assert fake.calls[0].method == "POST"
    assert fake.calls[0].url.endswith("/v1.0/me/messages")
    assert sent["subject"] == "Hello"
    assert sent["body"] == {"contentType": "HTML", "content": "<p>Hi Ana</p>"}
    assert sent["toRecipients"] == [{"emailAddress": {"address": "ana@x.com"}}]
    assert sent["ccRecipients"] == [{"emailAddress": {"address": "bo@x.com"}}]
    assert out == {"id": "D1", "webLink": "https://outlook/D1", "subject": "Hello",
                   "to": ["ana@x.com"], "cc": ["bo@x.com"], "attachments": [], "failed_attachments": []}


def test_signature_is_appended(private_home):
    (private_home / "signature.html").write_text("<p>-- Kev</p>", encoding="utf-8")
    fake = Fake(_created)
    outlook.draft(graph(fake), _new_spec())
    assert fake.json(0)["body"]["content"] == "<p>Hi Ana</p><p>-- Kev</p>"


def test_new_draft_without_recipients_is_allowed():
    fake = Fake(_created)
    out = outlook.draft(graph(fake), _new_spec(to=[], cc=[]))
    assert fake.json(0)["toRecipients"] == []
    assert out["to"] == []


@pytest.mark.parametrize("over", [{"attachments": ["C:/missing.pdf"]}, {"mode": "replyAll"}])
def test_invalid_draft_makes_no_graph_calls(over):
    fake = Fake()
    with pytest.raises(outlook.SpecError):
        outlook.draft(graph(fake), _new_spec(**over))
    assert fake.calls == []


def test_draft_attachments_are_listed(tmp_path):
    path = _file(tmp_path, "a.txt", 5)
    fake = Fake(_draft_handler())
    out = outlook.draft(graph(fake), _new_spec(attachments=[path]))
    assert fake.calls[1].url.endswith("/me/messages/D1/attachments")
    assert out["attachments"] == ["a.txt"]


def test_failed_attachment_is_partial_with_link(tmp_path):
    ok = _file(tmp_path, "ok.txt", 5)
    bad = _file(tmp_path, "bad.txt", 5)
    fake = Fake(_draft_handler(fail_names={"bad.txt"}))
    with pytest.raises(outlook.Partial) as e:
        outlook.draft(graph(fake), _new_spec(attachments=[ok, bad]))
    r = e.value.result
    assert (r["id"], r["webLink"], r["stage"]) == ("D1", "https://outlook/D1", "attachments")
    assert r["attachments"] == ["ok.txt"]
    assert r["failed_attachments"][0]["path"] == bad
    assert "boom" in r["failed_attachments"][0]["error"]


QUOTED = '<html><head></head><BODY class="x"><hr><p>Original from Ana</p></BODY></html>'


def _reply_handler(draft_html=QUOTED, patch_status=200):
    def handler(m, u, d, h):
        if u.endswith("/createReply") or u.endswith("/createReplyAll"):
            return 201, {}, {"id": "R1", "webLink": "https://outlook/R1", "subject": "RE: Hello"}
        if m == "GET" and "/me/messages/R1?" in u:
            return 200, {}, {"body": {"contentType": "html", "content": draft_html},
                             "toRecipients": [{"emailAddress": {"name": "Ana", "address": "Ana@X.com"}}],
                             "ccRecipients": []}
        if m == "PATCH":
            if patch_status != 200:
                return patch_status, {}, {"error": {"code": "ErrorInvalidRequest", "message": "nope"}}
            return 200, {}, {"id": "R1", "subject": "RE: Hello", **json.loads(d)}
        raise AssertionError(f"unexpected {m} {u}")
    return handler


def _reply_spec(**over):
    spec = {"mode": "reply", "reply_to_id": "M/1=", "to": [], "cc": [],
            "body_html": "<p>Thanks!</p>", "attachments": []}
    spec.update(over)
    return spec


def test_reply_inserts_after_body_tag_and_keeps_quote():
    fake = Fake(_reply_handler())
    out = outlook.draft(graph(fake), _reply_spec())
    assert fake.calls[0].url.endswith("/me/messages/M%2F1=/createReply")
    assert fake.calls[1].headers["Prefer"] == 'outlook.body-content-type="html"'
    assert fake.json(2)["body"] == {
        "contentType": "HTML",
        "content": '<html><head></head><BODY class="x"><p>Thanks!</p><hr><p>Original from Ana</p></BODY></html>',
    }
    assert (out["id"], out["subject"], out["webLink"]) == ("R1", "RE: Hello", "https://outlook/R1")


def test_reply_all_uses_create_reply_all():
    fake = Fake(_reply_handler())
    outlook.draft(graph(fake), _reply_spec(mode="replyAll"))
    assert fake.calls[0].url.endswith("/createReplyAll")


def test_reply_body_without_body_tag_is_prepended():
    fake = Fake(_reply_handler(draft_html="<p>quoted</p>"))
    outlook.draft(graph(fake), _reply_spec())
    assert fake.json(2)["body"]["content"] == "<p>Thanks!</p><p>quoted</p>"


def test_reply_recipients_are_added_not_replaced_and_deduped():
    fake = Fake(_reply_handler())
    out = outlook.draft(graph(fake), _reply_spec(to=["ana@x.com", "cy@x.com"], cc=["dee@x.com"]))
    patch = fake.json(2)
    assert [r["emailAddress"]["address"] for r in patch["toRecipients"]] == ["Ana@X.com", "cy@x.com"]
    assert [r["emailAddress"]["address"] for r in patch["ccRecipients"]] == ["dee@x.com"]
    assert out["to"] == ["Ana@X.com", "cy@x.com"]


def test_reply_signature_goes_before_the_quote(private_home):
    (private_home / "signature.html").write_text("<p>-- Kev</p>", encoding="utf-8")
    fake = Fake(_reply_handler(draft_html="<body><p>quoted</p></body>"))
    outlook.draft(graph(fake), _reply_spec())
    assert fake.json(2)["body"]["content"] == "<body><p>Thanks!</p><p>-- Kev</p><p>quoted</p></body>"


def test_reply_patch_failure_is_partial_and_skips_attachments(tmp_path):
    fake = Fake(_reply_handler(patch_status=400))
    with pytest.raises(outlook.Partial) as e:
        outlook.draft(graph(fake), _reply_spec(attachments=[_file(tmp_path, "a.txt", 5)]))
    r = e.value.result
    assert (r["id"], r["webLink"], r["stage"]) == ("R1", "https://outlook/R1", "reply-body")
    assert len(fake.calls) == 3   # createReply, GET, PATCH: no attachment upload


def test_reply_attachments_go_to_the_reply_draft(tmp_path):
    reply, attachments = _reply_handler(), _attach_handler()

    def handler(m, u, d, h):
        return attachments(m, u, d, h) if "/attachments" in u else reply(m, u, d, h)
    fake = Fake(handler)
    out = outlook.draft(graph(fake), _reply_spec(attachments=[_file(tmp_path, "a.txt", 5)]))
    assert fake.calls[3].url.endswith("/me/messages/R1/attachments")
    assert out["attachments"] == ["a.txt"]


def test_network_failure_after_reply_created_is_partial():
    reply = _reply_handler()

    def handler(m, u, d, h):
        if m == "GET":
            raise TimeoutError("timed out")
        return reply(m, u, d, h)
    with pytest.raises(outlook.Partial) as e:
        outlook.draft(graph(Fake(handler)), _reply_spec())
    assert (e.value.result["id"], e.value.result["stage"]) == ("R1", "reply-body")


@pytest.mark.parametrize("failure", [
    TimeoutError("timed out"), (500, {}, None),
    (503, {}, {"error": {"code": None, "message": None}}),   # null fields must not break the suffix
    (201, {}, None), (201, {}, b"<html>proxy</html>"), (201, {}, {"subject": "Hello"}),   # unreadable 2xx
])
def test_failed_create_that_may_have_landed_says_check_drafts(failure):
    def handler(m, u, d, h):
        if isinstance(failure, Exception):
            raise failure
        return failure
    with pytest.raises(outlook.GraphError) as e:
        outlook.draft(graph(Fake(handler)), _new_spec())
    assert "check Outlook Drafts" in e.value.message


def test_rejected_create_does_not_say_check_drafts():
    err = {"error": {"code": "ErrorInvalidRecipients", "message": "bad address"}}
    with pytest.raises(outlook.GraphError) as e:
        outlook.draft(graph(Fake(lambda m, u, d, h: (400, {}, err))), _new_spec())
    assert e.value.message == "bad address"


# --- lookup and find -------------------------------------------------------------

def test_lookup_maps_people_and_skips_entries_without_email():
    people = {"value": [
        {"displayName": "Maria Ruiz", "companyName": "Acme", "scoredEmailAddresses": [{"address": "maria@acme.com"}]},
        {"displayName": "Maria Group", "companyName": None, "scoredEmailAddresses": []},
        *[{"displayName": f"P{i}", "companyName": None, "scoredEmailAddresses": [{"address": f"p{i}@x.com"}]}
          for i in range(6)],
    ]}
    fake = Fake(lambda m, u, d, h: (200, {}, people))
    out = outlook.lookup(graph(fake), "Maria Acme")
    assert out[0] == {"name": "Maria Ruiz", "email": "maria@acme.com", "company": "Acme"}
    assert len(out) == 5
    assert all(r["email"] for r in out)
    assert "/me/people?$search=%22Maria%20Acme%22" in fake.calls[0].url


def test_search_text_with_quotes_and_ampersand_is_escaped():
    fake = Fake(lambda m, u, d, h: (200, {}, {"value": []}))
    outlook.find(graph(fake), 'RE: "Q3" plan & budget #2')
    term = fake.calls[0].url.split("$search=")[1]
    assert urllib.parse.unquote(term) == '"RE: \\"Q3\\" plan & budget #2"'
    assert "&" not in term and "#" not in term


def _msgs(*overrides):
    base = {"subject": "S", "isDraft": False, "from": {"emailAddress": {"address": "ana@x.com"}},
            "toRecipients": [{"emailAddress": {"address": "me@x.com"}}], "ccRecipients": [],
            "receivedDateTime": "2026-10-01T10:00:00Z", "bodyPreview": "hi"}
    return {"value": [{**base, "id": f"m{i}", "uniqueBody": {"content": f"new part {i}"}, **o}
                      for i, o in enumerate(overrides)]}


def test_find_drops_drafts_and_maps_fields():
    fake = Fake(lambda m, u, d, h: (200, {}, _msgs({"isDraft": True}, {}, {})))
    out = outlook.find(graph(fake), "budget")
    assert [m["id"] for m in out] == ["m1", "m2"]
    assert out[0] == {"id": "m1", "subject": "S", "from": "ana@x.com", "to": ["me@x.com"], "cc": [],
                      "received": "2026-10-01T10:00:00Z", "preview": "hi", "isDraft": False}


def test_find_respects_top_after_dropping_drafts():
    fake = Fake(lambda m, u, d, h: (200, {}, _msgs(*[{}] * 8)))
    assert len(outlook.find(graph(fake), "x", top=3)) == 3


def test_find_sent_full_uses_sent_folder_and_text_unique_body():
    fake = Fake(lambda m, u, d, h: (200, {}, _msgs({})))
    out = outlook.find(graph(fake), None, sent=True, top=1, body="text")
    c = fake.calls[0]
    assert "/me/mailFolders/sentitems/messages?" in c.url
    assert "uniqueBody" in c.url and "$search" not in c.url
    assert "$orderby=receivedDateTime%20desc" in c.url
    assert c.headers["Prefer"] == 'outlook.body-content-type="text"'
    assert out[0]["body"] == "new part 0"


def test_find_html_mode_asks_for_html():
    fake = Fake(lambda m, u, d, h: (200, {}, _msgs({})))
    outlook.find(graph(fake), "x", body="html")
    assert fake.calls[0].headers["Prefer"] == 'outlook.body-content-type="html"'


def test_find_without_query_reads_inbox_and_with_query_searches_all_mail():
    fake = Fake(lambda m, u, d, h: (200, {}, _msgs({})))
    outlook.find(graph(fake))
    outlook.find(graph(fake), "budget")
    assert "/me/mailFolders/inbox/messages?" in fake.calls[0].url
    assert "$orderby=receivedDateTime%20desc" in fake.calls[0].url
    assert "/v1.0/me/messages?" in fake.calls[1].url
    assert "$orderby" not in fake.calls[1].url


# --- auth and CLI ----------------------------------------------------------------

def _fake_msal(monkeypatch, accounts=(), silent=None, interactive=None):
    """Install fake msal + msal_extensions modules; returns the call log."""
    log = []

    class App:
        def __init__(self, client_id, authority, token_cache):
            log.append(("init", client_id, authority))

        def get_accounts(self):
            return list(accounts)

        def acquire_token_silent(self, scopes, account):
            log.append(("silent", tuple(scopes)))
            if isinstance(silent, Exception):
                raise silent
            return silent

        def acquire_token_interactive(self, scopes, timeout, prompt):
            log.append(("interactive", timeout))
            if isinstance(interactive, Exception):
                raise interactive
            return interactive

        def remove_account(self, account):
            log.append(("remove", account["username"]))

    monkeypatch.setitem(sys.modules, "msal", types.SimpleNamespace(PublicClientApplication=App))
    monkeypatch.setitem(sys.modules, "msal_extensions", types.SimpleNamespace(
        build_encrypted_persistence=lambda path: ("persistence", path),
        PersistedTokenCache=lambda persistence: ("cache", persistence)))
    return log


def _config(home):
    (home / "config.json").write_text(json.dumps({"tenant_id": "T", "client_id": "C"}), encoding="utf-8")


def _token(interactive=False):
    return {"access_token": "tok"}


def _write_spec(tmp_path, spec, bom=False):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8-sig" if bom else "utf-8")
    return str(path)


def test_silent_miss_exits_2_without_going_interactive(private_home, monkeypatch, capsys):
    _config(private_home)
    log = _fake_msal(monkeypatch, accounts=[], interactive={"access_token": "x"})
    assert outlook.main(["lookup", "Maria"], transport=Fake()) == 2
    assert not [e for e in log if e[0] == "interactive"]
    assert "outlook.py login" in json.loads(capsys.readouterr().err)["message"]


def test_silent_hit_uses_cached_account(private_home, monkeypatch, capsys):
    _config(private_home)
    log = _fake_msal(monkeypatch, accounts=[{"username": "me"}], silent={"access_token": "tok"})
    fake = Fake(lambda m, u, d, h: (200, {}, {"value": []}))
    assert outlook.main(["lookup", "Maria"], transport=fake) == 0
    assert fake.calls[0].headers["Authorization"] == "Bearer tok"
    assert ("init", "C", "https://login.microsoftonline.com/T") in log
    assert ("silent", ("Mail.ReadWrite", "People.Read")) in log


def test_login_goes_interactive_with_180s_timeout(private_home, monkeypatch, capsys):
    _config(private_home)
    log = _fake_msal(monkeypatch, interactive={
        "access_token": "t", "id_token_claims": {"preferred_username": "me@x.com"}})
    assert outlook.main(["login"]) == 0
    assert ("interactive", 180) in log
    assert json.loads(capsys.readouterr().out) == {"account": "me@x.com"}


def test_msal_exception_exits_2_with_its_message(private_home, monkeypatch, capsys):
    _config(private_home)
    _fake_msal(monkeypatch, accounts=[{"username": "me"}], silent=ConnectionError("offline"))
    assert outlook.main(["lookup", "Maria"], transport=Fake()) == 2
    assert "offline" in json.loads(capsys.readouterr().err)["message"]


def test_rejected_token_exits_2_with_login_hint(capsys):
    err = {"error": {"code": "InvalidAuthenticationToken", "message": "expired"}}
    fake = Fake(lambda m, u, d, h: (401, {}, err))
    assert outlook.main(["lookup", "Maria"], transport=fake, token_fn=_token) == 2
    assert "outlook.py login" in json.loads(capsys.readouterr().err)["message"]


def test_network_failure_exits_1_with_json(capsys):
    def down(m, u, d, h):
        raise urllib.error.URLError("getaddrinfo failed")
    assert outlook.main(["lookup", "Maria"], transport=Fake(down), token_fn=_token) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "network"


@pytest.mark.parametrize("argv", [["find", "--top", "abc"], ["find", "--full", "--html"], ["nope"]])
def test_usage_errors_exit_1_not_2(argv, capsys):
    with pytest.raises(SystemExit) as e:
        outlook.main(argv, transport=Fake(), token_fn=_token)
    assert e.value.code == 1
    assert json.loads(capsys.readouterr().err)["error"] == "usage"


@pytest.mark.parametrize("argv, url_part, prefer, count", [
    (["find", "x", "--sent", "--top", "2", "--full"], "/me/mailFolders/sentitems/messages?$top=12", "text", 2),
    (["find", "--html"], "/me/mailFolders/inbox/messages?$top=15", "html", 3),
    (["find", "x"], "/me/messages?$top=15", None, 3),
])
def test_find_flags_reach_graph(argv, url_part, prefer, count, capsys):
    fake = Fake(lambda m, u, d, h: (200, {}, _msgs({}, {}, {})))
    assert outlook.main(argv, transport=fake, token_fn=_token) == 0
    c = fake.calls[0]
    assert url_part in c.url
    assert c.headers.get("Prefer") == (f'outlook.body-content-type="{prefer}"' if prefer else None)
    assert len(json.loads(capsys.readouterr().out)["results"]) == count


def test_missing_config_exits_2(capsys):
    assert outlook.main(["find", "x"], transport=Fake()) == 2
    assert "config.json" in json.loads(capsys.readouterr().err)["message"]


def test_msal_not_installed_exits_2(private_home, monkeypatch, capsys):
    _config(private_home)
    monkeypatch.setitem(sys.modules, "msal", None)
    assert outlook.main(["find", "x"], transport=Fake()) == 2
    assert "pip install msal" in json.loads(capsys.readouterr().err)["message"]


@pytest.mark.parametrize("over", [
    {"attachments": ["C:/missing.pdf"]}, {"mode": "replyAll"}, {"subject": ""}, {"mode": "x"},
])
def test_bad_spec_exits_1_before_auth_or_graph(over, tmp_path, capsys):
    fake = Fake()

    def no_token(interactive=False):
        raise AssertionError("auth must not run for a bad spec")

    assert outlook.main(["draft", _write_spec(tmp_path, _new_spec(**over))],
                        transport=fake, token_fn=no_token) == 1
    assert fake.calls == []
    assert json.loads(capsys.readouterr().err)["error"] == "spec"


def test_unreadable_spec_exits_1(tmp_path, capsys):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert outlook.main(["draft", str(tmp_path / "bad.json")], transport=Fake(), token_fn=_token) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "spec"


def test_draft_with_bom_spec_and_non_ascii_prints_utf8_json(tmp_path, capsys):
    spec = _new_spec(subject="Café — Zürich", body_html="<p>Grüße</p>")
    fake = Fake(_created)
    assert outlook.main(["draft", _write_spec(tmp_path, spec, bom=True)],
                        transport=fake, token_fn=_token) == 0
    out = capsys.readouterr().out
    assert "Café — Zürich" in out
    assert json.loads(out)["webLink"] == "https://outlook/D1"


def test_graph_error_exits_1_with_code_and_message(tmp_path, capsys):
    err = {"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}}
    fake = Fake(lambda m, u, d, h: (403, {}, err))
    assert outlook.main(["draft", _write_spec(tmp_path, _new_spec())],
                        transport=fake, token_fn=_token) == 1
    assert json.loads(capsys.readouterr().err) == {"error": "ErrorAccessDenied", "message": "Access is denied."}


def test_partial_exits_3_with_link_on_stdout(tmp_path, capsys):
    bad = _file(tmp_path, "bad.txt", 5)
    fake = Fake(_draft_handler(fail_names={"bad.txt"}))
    assert outlook.main(["draft", _write_spec(tmp_path, _new_spec(attachments=[bad]))],
                        transport=fake, token_fn=_token) == 3
    out = json.loads(capsys.readouterr().out)
    assert out["partial"] is True and out["webLink"] == "https://outlook/D1"
    assert out["failed_attachments"][0]["path"] == bad


# --- final review fixes ----------------------------------------------------------

def test_unreadable_reply_draft_is_partial_not_a_crash():
    reply = _reply_handler()

    def handler(m, u, d, h):
        if m == "GET":
            return 200, {}, {"toRecipients": []}   # no "body": a KeyError, not a GraphError
        return reply(m, u, d, h)
    with pytest.raises(outlook.Partial) as e:
        outlook.draft(graph(Fake(handler)), _reply_spec())
    assert (e.value.result["id"], e.value.result["stage"]) == ("R1", "reply-body")


def test_empty_patch_response_exits_3_with_link(tmp_path, capsys):
    reply = _reply_handler()

    def handler(m, u, d, h):
        return (200, {}, None) if m == "PATCH" else reply(m, u, d, h)
    path = _write_spec(tmp_path, _reply_spec())
    assert outlook.main(["draft", path], transport=Fake(handler), token_fn=_token) == 3
    out = json.loads(capsys.readouterr().out)
    assert (out["webLink"], out["stage"], out["partial"]) == ("https://outlook/R1", "recipients", True)


def test_unreadable_created_draft_exits_1_saying_check_drafts(tmp_path, capsys):
    path = _write_spec(tmp_path, _new_spec())
    fake = Fake(lambda m, u, d, h: (201, {}, None))
    assert outlook.main(["draft", path], transport=fake, token_fn=_token) == 1
    assert "check Outlook Drafts" in json.loads(capsys.readouterr().err)["message"]


def test_null_optional_fields_are_allowed():
    fake = Fake(_created)
    outlook.draft(graph(fake), _new_spec(reply_to_id=None, body_html=None))
    assert fake.json(0)["body"]["content"] == ""


def test_login_replaces_cached_accounts(private_home, monkeypatch, capsys):
    _config(private_home)
    log = _fake_msal(monkeypatch, accounts=[{"username": "old@x.com"}], interactive={
        "access_token": "t", "id_token_claims": {"preferred_username": "new@x.com"}})
    assert outlook.main(["login"]) == 0
    assert log.index(("remove", "old@x.com")) < log.index(("interactive", 180))


def test_login_that_raises_says_how_to_retry(private_home, monkeypatch, capsys):
    _config(private_home)
    _fake_msal(monkeypatch, interactive=TimeoutError("no response in 180 s"))
    assert outlook.main(["login"]) == 2
    message = json.loads(capsys.readouterr().err)["message"]
    assert "no response in 180 s" in message and "outlook.py login" in message
