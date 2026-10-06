#!/usr/bin/env python3
"""Create Outlook drafts through Microsoft Graph.

Drafts only: the Entra app behind this script is never granted Mail.Send.
Commands: login, lookup, find, draft. Each prints one JSON object.
Exit codes: 0 ok, 1 bad input or Graph error, 2 setup/sign-in, 3 partial.
"""
import argparse
import base64
import http.client
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.ReadWrite", "People.Read"]
SIMPLE_MAX = 3 * 1024 * 1024   # Graph: files under 3 MB go in one POST
CHUNK = 10 * 320 * 1024        # upload-session chunk, under Graph's 4 MB per PUT
MAX_ATTACHMENT = 150 * 1024 * 1024   # Graph's upload-session limit
MAX_RETRIES = 3
MAX_WAIT = 30                  # cap Retry-After so one throttled call cannot stall for minutes
MODES = ("new", "reply", "replyAll")
SIGNATURE_IMAGES = (".png", ".jpg", ".jpeg", ".gif")   # never config.json or the token cache
LOGIN_HINT = "Not signed in. Run: ! python ~/.claude/skills/outlook-draft/scripts/outlook.py login"


class SpecError(Exception):
    """The draft spec is unusable; nothing was sent to Graph."""


class SetupError(Exception):
    """Config, dependency or sign-in missing."""


class GraphError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


class Partial(Exception):
    """The draft exists, but a later step failed."""

    def __init__(self, result):
        super().__init__(result.get("error", "partial"))
        self.result = result


def home():
    override = os.environ.get("OUTLOOK_DRAFT_HOME")
    return pathlib.Path(override) if override else pathlib.Path.home() / ".claude" / "outlook-draft"


def load_config():
    path = home() / "config.json"
    if not path.is_file():
        raise SetupError(f"Missing {path}. Follow the Setup section of the outlook-draft SKILL.md.")
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise SetupError(f"Cannot read {path}: {e}") from e
    if not isinstance(cfg, dict) or not cfg.get("tenant_id") or not cfg.get("client_id"):
        raise SetupError(f"{path} needs tenant_id and client_id.")
    return cfg


def urllib_transport(method, url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _retry_after(headers):
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return min(max(int(value), 0), MAX_WAIT)
            except ValueError:
                return 1
    return 1


def _graph_error(status, raw):
    try:
        err = json.loads(raw)["error"]
        return GraphError(status, str(err.get("code") or ""), str(err.get("message") or ""))
    except (ValueError, KeyError, TypeError, AttributeError):   # "error" may not be an object
        return GraphError(status, "", raw.decode("utf-8", "replace")[:500])


class Graph:
    def __init__(self, token, transport=urllib_transport, sleep=time.sleep):
        self.token, self.transport, self.sleep = token, transport, sleep

    def call(self, method, url, body=None, *, data=None, headers=None, auth=True, retry_503=None):
        """One Graph request. 429 is always retried; 503 only on GETs or when
        retry_503=True, because a 503 on a create may have been processed."""
        if not url.startswith("https://"):
            url = GRAPH + url
        hdrs = dict(headers or {})
        if auth:
            hdrs["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if retry_503 is None:
            retry_503 = method == "GET"
        for attempt in range(MAX_RETRIES + 1):
            try:
                status, resp_headers, raw = self.transport(method, url, data, hdrs)
            except (OSError, http.client.HTTPException) as e:   # URLError, timeout, reset, short read
                raise GraphError(0, "network", str(e)) from e
            retryable = status == 429 or (status == 503 and retry_503)
            if not retryable or attempt == MAX_RETRIES:
                break
            self.sleep(_retry_after(resp_headers))
        if status >= 400:
            raise _graph_error(status, raw)
        try:
            return json.loads(raw) if raw else None
        except ValueError as e:   # e.g. a proxy's HTML page with a 200
            raise GraphError(status, "bad-response", raw.decode("utf-8", "replace")[:500]) from e


def _q(message_id):
    return urllib.parse.quote(message_id, safe="=")


def _addr(recipient):
    return ((recipient or {}).get("emailAddress") or {}).get("address")


def _rcpt(address):
    return {"emailAddress": {"address": address}}


def validate_spec(spec):
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    mode = spec.get("mode")
    if mode not in MODES:
        raise SpecError(f"mode must be one of {', '.join(MODES)}; got {mode!r}")
    for key in ("subject", "reply_to_id", "body_html"):
        if spec.get(key) is not None and not isinstance(spec[key], str):
            raise SpecError(f"{key} must be a string")
    if mode == "new" and not spec.get("subject"):
        raise SpecError("new mode needs a subject")
    if mode != "new" and not spec.get("reply_to_id"):
        raise SpecError(f"{mode} mode needs reply_to_id")
    for key in ("to", "cc", "attachments"):
        value = spec.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise SpecError(f"{key} must be a list of strings")
    for path in spec.get("attachments", []):
        if not pathlib.Path(path).is_file():
            raise SpecError(f"attachment is not a file: {path}")
        if pathlib.Path(path).stat().st_size > MAX_ATTACHMENT:
            raise SpecError(f"attachment is over Graph's 150 MB limit: {path}")


def read_spec(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise SpecError(f"cannot read spec {path}: {e}") from e


def attach(graph, msg_id, path, inline=False):
    p = pathlib.Path(path)
    size = p.stat().st_size
    base = f"/me/messages/{_q(msg_id)}/attachments"
    # an inline image shows where the body has <img src="cid:<its contentId>">
    extra = {"isInline": True, "contentId": p.name} if inline else {}
    if size < SIMPLE_MAX:
        graph.call("POST", base, {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": p.name,
            "contentBytes": base64.b64encode(p.read_bytes()).decode("ascii"),
            **extra,
        })
        return
    session = graph.call("POST", base + "/createUploadSession", {
        "AttachmentItem": {"attachmentType": "file", "name": p.name, "size": size, **extra},
    })
    with p.open("rb") as f:
        start = 0
        while start < size:
            chunk = f.read(CHUNK)
            end = start + len(chunk) - 1
            # uploadUrl is pre-authenticated: Graph rejects an Authorization header here.
            graph.call("PUT", session["uploadUrl"], data=chunk, auth=False, retry_503=True, headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {start}-{end}/{size}",
            })
            start = end + 1


def signature():
    """signature.html, and the images beside it that it shows as <img src="cid:<file name>">."""
    sig = home() / "signature.html"
    if not sig.is_file():
        return "", []
    html = sig.read_text(encoding="utf-8-sig")
    names = dict.fromkeys(re.findall(r'src=["\']cid:([^"\'/\\]+)["\']', html, re.IGNORECASE))
    return html, [home() / n for n in names if n.lower().endswith(SIGNATURE_IMAGES)]


_BODY_TAG = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


def insert_reply(draft_html, new_html):
    """Put new_html right after the opening <body> tag, keeping the quoted thread."""
    m = _BODY_TAG.search(draft_html)
    if not m:
        return new_html + draft_html
    return draft_html[:m.end()] + new_html + draft_html[m.end():]


def merge_recipients(existing, extra):
    seen = {(_addr(r) or "").lower() for r in existing}
    merged = list(existing)
    for address in extra:
        if address.lower() not in seen:
            seen.add(address.lower())
            merged.append(_rcpt(address))
    return merged


def _fill_reply(graph, draft_id, body, to, cc):
    current = graph.call(
        "GET", f"/me/messages/{_q(draft_id)}?$select=body,toRecipients,ccRecipients",
        headers={"Prefer": 'outlook.body-content-type="html"'},
    )
    return graph.call("PATCH", f"/me/messages/{_q(draft_id)}", {
        "body": {"contentType": "HTML", "content": insert_reply(current["body"]["content"], body)},
        "toRecipients": merge_recipients(current.get("toRecipients") or [], to),
        "ccRecipients": merge_recipients(current.get("ccRecipients") or [], cc),
    })


MAYBE_CREATED = " The draft may have been created anyway: check Outlook Drafts before running draft again."


def _create(graph, spec, body, to, cc):
    if spec["mode"] == "new":
        return graph.call("POST", "/me/messages", {
            "subject": spec["subject"],
            "body": {"contentType": "HTML", "content": body},
            "toRecipients": [_rcpt(a) for a in to],
            "ccRecipients": [_rcpt(a) for a in cc],
        })
    action = "createReply" if spec["mode"] == "reply" else "createReplyAll"
    return graph.call("POST", f"/me/messages/{_q(spec['reply_to_id'])}/{action}")


def draft(graph, spec):
    validate_spec(spec)
    sig_html, sig_images = signature()
    body = (spec.get("body_html") or "") + sig_html
    to, cc = spec.get("to", []), spec.get("cc", [])
    try:
        msg = _create(graph, spec, body, to, cc)
        result = {"id": msg["id"], "webLink": msg.get("webLink"), "subject": msg.get("subject"),
                  "to": [], "cc": [], "attachments": [], "failed_attachments": []}
    except GraphError as e:
        if 400 <= e.status < 500:   # Graph refused the create: no draft exists
            raise
        # timeout, server error or a 2xx we cannot read: the create may have gone through
        raise GraphError(e.status, e.code, e.message + MAYBE_CREATED) from e
    except Exception as e:   # a 2xx without a usable id: the draft probably exists
        raise GraphError(0, "bad-response", f"{type(e).__name__}: {e}.{MAYBE_CREATED}") from e
    # From here on the draft exists: any failure, of any type, is reported as partial
    # (exit 3), so Claude never re-runs draft and makes a duplicate.
    stage = "reply-body"
    try:
        if spec["mode"] != "new":
            msg = _fill_reply(graph, result["id"], body, to, cc)
        stage = "recipients"
        result["to"] = [_addr(r) for r in msg.get("toRecipients") or []]
        result["cc"] = [_addr(r) for r in msg.get("ccRecipients") or []]
    except Exception as e:
        raise Partial({**result, "stage": stage, "error": str(e)}) from e
    for path in sig_images:   # a missing logo is reported, not silently left as a broken image
        try:
            attach(graph, result["id"], path, inline=True)
        except Exception as e:
            result["failed_attachments"].append({"path": str(path), "error": str(e)})
    for path in spec.get("attachments", []):
        try:
            attach(graph, result["id"], path)
            result["attachments"].append(pathlib.Path(path).name)
        except Exception as e:
            result["failed_attachments"].append({"path": path, "error": str(e)})
    if result["failed_attachments"]:
        raise Partial({**result, "stage": "attachments", "error": "some attachments failed"})
    return result


def _kql(text):
    """A $search term: double-quoted, inner quotes and backslashes escaped, URL-encoded."""
    return urllib.parse.quote('"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"')


def lookup(graph, query):
    res = graph.call("GET", f"/me/people?$search={_kql(query)}&$top=10"
                            "&$select=displayName,scoredEmailAddresses,companyName")
    out = []
    for person in res.get("value", []):
        emails = person.get("scoredEmailAddresses") or []
        if emails and emails[0].get("address"):
            out.append({"name": person.get("displayName"), "email": emails[0]["address"],
                        "company": person.get("companyName")})
    return out[:5]


def find(graph, query=None, sent=False, top=5, body=None):
    """body: None, "text" or "html" (adds uniqueBody: only the new part of each message)."""
    if sent:
        folder = "/me/mailFolders/sentitems/messages"
    elif query:
        folder = "/me/messages"
    else:   # newest mail: the Inbox, since drafts (receivedDateTime = creation time) sort first in /me/messages
        folder = "/me/mailFolders/inbox/messages"
    fields = "id,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview,isDraft"
    if body:
        fields += ",uniqueBody"
    params = [f"$top={top + 10}", f"$select={fields}"]   # +10: drafts are dropped below
    if query:
        params.append(f"$search={_kql(query)}")            # $orderby cannot combine with $search
    else:
        params.append("$orderby=receivedDateTime%20desc")
    headers = {"Prefer": f'outlook.body-content-type="{body}"'} if body else None
    res = graph.call("GET", folder + "?" + "&".join(params), headers=headers)
    out = []
    for m in res.get("value", []):
        if m.get("isDraft"):
            continue
        item = {"id": m["id"], "subject": m.get("subject"), "from": _addr(m.get("from")),
                "to": [_addr(r) for r in m.get("toRecipients") or []],
                "cc": [_addr(r) for r in m.get("ccRecipients") or []],
                "received": m.get("receivedDateTime"), "preview": m.get("bodyPreview"),
                "isDraft": False}
        if body:
            item["body"] = (m.get("uniqueBody") or {}).get("content", "")
        out.append(item)
    return out[:top]


def get_token(interactive=False):
    """Return MSAL's token result. Only `login` passes interactive=True."""
    cfg = load_config()
    try:
        import msal
        from msal_extensions import PersistedTokenCache, build_encrypted_persistence
    except ImportError as e:
        raise SetupError("Run: python -m pip install msal msal-extensions") from e
    try:
        cache = PersistedTokenCache(build_encrypted_persistence(str(home() / "token_cache.bin")))
        app = msal.PublicClientApplication(
            cfg["client_id"],
            authority=f"https://login.microsoftonline.com/{cfg['tenant_id']}",
            token_cache=cache,
        )
        if interactive:
            for account in app.get_accounts():   # one account only: other commands use accounts[0]
                app.remove_account(account)
            result = app.acquire_token_interactive(SCOPES, timeout=180, prompt="select_account")
        else:
            accounts = app.get_accounts()
            result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    except Exception as e:   # msal raises on network, authority-discovery and cache-lock failures
        hint = f"\n{LOGIN_HINT}" if interactive else ""   # e.g. the 180 s sign-in window ran out
        raise SetupError(f"Sign-in failed: {type(e).__name__}: {e}{hint}") from e
    if not result or "access_token" not in result:
        detail = (result or {}).get("error_description")
        raise SetupError(f"{detail}\n{LOGIN_HINT}" if detail else LOGIN_HINT)
    return result


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    return 0


def _fail(code, kind, message):
    print(json.dumps({"error": kind, "message": message}, ensure_ascii=False), file=sys.stderr)
    return code


class _Parser(argparse.ArgumentParser):
    def error(self, message):   # argparse's own exit 2 would read as "sign in needed"
        sys.exit(_fail(1, "usage", message))


def main(argv=None, transport=urllib_transport, token_fn=get_token, sleep=time.sleep):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")   # Windows consoles default to cp1252
    ap = _Parser(prog="outlook.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login")
    sub.add_parser("lookup").add_argument("query")
    p = sub.add_parser("find")
    p.add_argument("query", nargs="?")
    p.add_argument("--sent", action="store_true")
    p.add_argument("--top", type=int, default=5)
    body = p.add_mutually_exclusive_group()
    body.add_argument("--full", action="store_true")
    body.add_argument("--html", action="store_true")
    sub.add_parser("draft").add_argument("spec")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "draft":
            spec = read_spec(args.spec)
            validate_spec(spec)   # a bad spec fails before sign-in or any Graph call
        if args.cmd == "login":
            res = token_fn(interactive=True)
            return _emit({"account": (res.get("id_token_claims") or {}).get("preferred_username")})
        graph = Graph(token_fn()["access_token"], transport, sleep)
        if args.cmd == "lookup":
            return _emit({"results": lookup(graph, args.query)})
        if args.cmd == "find":
            mode = "text" if args.full else "html" if args.html else None
            return _emit({"results": find(graph, args.query, args.sent, args.top, mode)})
        return _emit(draft(graph, spec))
    except SpecError as e:
        return _fail(1, "spec", str(e))
    except SetupError as e:
        return _fail(2, "setup", str(e))
    except Partial as e:
        _emit({**e.result, "partial": True})
        return 3
    except GraphError as e:
        if e.status == 401:   # token rejected (consent revoked, session killed): sign in again
            return _fail(2, "setup", LOGIN_HINT)
        return _fail(1, e.code or "graph", e.message)
    except Exception as e:   # keep the JSON contract even for a bug
        return _fail(1, "unexpected", f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
