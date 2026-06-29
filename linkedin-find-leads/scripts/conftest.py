# conftest.py
import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _hermetic_connectsafely(monkeypatch):
    """Make every test offline: a dummy key + a stub connectsafely module.

    Without this, importing connectsafely (via get_client) runs its module-level
    cs = ConnectSafely(), which sys.exit()s when the key is unset and requires
    ~/marketing/connectsafely.py to exist. The stub removes both dependencies.
    Tests that need the keyless path override with monkeypatch.delenv(...).
    """
    monkeypatch.setenv("CONNECTSAFELY_API_KEY", "test-dummy")
    stub = types.ModuleType("connectsafely")

    class ConnectSafelyError(Exception):
        pass

    stub.ConnectSafelyError = ConnectSafelyError
    stub.cs = object()
    monkeypatch.setitem(sys.modules, "connectsafely", stub)
    # reset the cached client so each test reconstructs against the stub
    mod = sys.modules.get("linkedin_lead_finder")
    if mod is not None:
        mod._CLIENT = None
    yield
