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
        return GraphError(status, err.get("code", ""), err.get("message", ""))
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
