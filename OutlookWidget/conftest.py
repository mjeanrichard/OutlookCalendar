"""Pytest fixtures for the widgets in this repo.

Tesserae's own root conftest builds its test app with
``plugins_dir=REPO_ROOT/"plugins"``, and pytest only picks up conftest files
from a test's ancestor directories, so widgets living outside the clone need
their own ``app`` / ``client`` fixtures. They mirror the upstream ones plus the
extra-scan-dir injection ``devserver.py`` uses.

On top of that this module provides the plumbing every test here wants: the
loaded plugin modules and their data dirs, and ``fake_http``, which replaces
``urllib.request.urlopen`` so no test can reach the network.

Plugin discovery is pointed at this repo through ``_devsupport``, the same
module ``devserver.py`` uses, so tests and the dev server agree.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _devsupport import ensure_tesserae_importable, with_widget_root

ensure_tesserae_importable()

from app import plugin_loader  # noqa: E402
from app.main import REPO_ROOT, create_app  # noqa: E402
from flask import Flask  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Flask:
    monkeypatch.setattr(plugin_loader, "discover", with_widget_root(plugin_loader.discover))
    return create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


# ----- plugin handles -------------------------------------------------


def _plugin(app: Flask, plugin_id: str) -> Any:
    plugin = app.config["PLUGIN_REGISTRY"].get(plugin_id)
    assert plugin is not None, f"{plugin_id} was not discovered"
    assert plugin.server_module is not None, f"{plugin_id} has no server.py"
    return plugin


@pytest.fixture
def core(app: Flask) -> Any:
    """The loaded ``outlook_core`` server module."""
    return _plugin(app, "outlook_core").server_module


@pytest.fixture
def core_dir(app: Flask) -> Path:
    """``outlook_core``'s data dir, inside the test's tmp_path."""
    path: Path = _plugin(app, "outlook_core").data_dir
    return path


@pytest.fixture
def week(app: Flask) -> Any:
    """The loaded ``outlook_week`` server module."""
    return _plugin(app, "outlook_week").server_module


@pytest.fixture
def cfg(core: Any) -> Any:
    return core.AppConfig(client_id="test-client-id", tenant="common")


@pytest.fixture
def signed_in(core: Any, core_dir: Path) -> dict[str, Any]:
    """A token that is valid for another hour, so nothing refreshes."""
    import time

    token = {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "expires_at": time.time() + 3600,
        "scope": core.SCOPES,
        "account": "Test User (test@example.com)",
    }
    core.save_token(core_dir, token)
    return token


# ----- fake HTTP ------------------------------------------------------


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._raw = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeHttp:
    """Stand-in for ``urllib.request.urlopen``.

    Routes are matched by substring against the request URL, in the order they
    were added. Each route holds a queue of responses; the last one repeats
    once the queue is drained, so "pending, pending, then signed in" is just
    three queued payloads.
    """

    def __init__(self) -> None:
        self.routes: list[tuple[str, list[tuple[int, Any]]]] = []
        self.requests: list[Any] = []

    def add(self, match: str, payload: Any, status: int = 200) -> FakeHttp:
        for route_match, responses in self.routes:
            if route_match == match:
                responses.append((status, payload))
                return self
        self.routes.append((match, [(status, payload)]))
        return self

    @property
    def urls(self) -> list[str]:
        return [getattr(r, "full_url", str(r)) for r in self.requests]

    def calls_matching(self, needle: str) -> int:
        return sum(1 for url in self.urls if needle in url)

    def last_request(self, needle: str) -> Any:
        for request in reversed(self.requests):
            if needle in getattr(request, "full_url", str(request)):
                return request
        raise AssertionError(f"no request matched {needle!r}")

    def __call__(self, request: Any, timeout: float | None = None) -> FakeResponse:
        url = getattr(request, "full_url", str(request))
        self.requests.append(request)
        for match, responses in self.routes:
            if match in url:
                status, payload = responses[0] if len(responses) == 1 else responses.pop(0)
                if status >= 400:
                    raise urllib.error.HTTPError(
                        url,
                        status,
                        "error",
                        {},  # type: ignore[arg-type]
                        io.BytesIO(json.dumps(payload).encode("utf-8")),
                    )
                return FakeResponse(payload, status)
        raise AssertionError(f"unexpected HTTP request to {url}")


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    """Every test gets one; nothing in this repo may touch the network."""
    http = FakeHttp()
    monkeypatch.setattr(urllib.request, "urlopen", http)
    return http
