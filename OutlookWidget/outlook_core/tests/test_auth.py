"""outlook_core: app config, the device-code sign-in, and token refresh."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

DEVICE_CODE_URL = "oauth2/v2.0/devicecode"
TOKEN_URL = "oauth2/v2.0/token"
ME_URL = "/me?"


def _device_code_payload(**over: Any) -> dict[str, Any]:
    payload = {
        "device_code": "dc-secret",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://microsoft.com/devicelogin",
        "expires_in": 900,
        "interval": 5,
        "message": "sign in please",
    }
    payload.update(over)
    return payload


def _token_payload(**over: Any) -> dict[str, Any]:
    payload = {
        "access_token": "at-new",
        "refresh_token": "rt-new",
        "expires_in": 3600,
        "scope": "offline_access Calendars.Read User.Read",
    }
    payload.update(over)
    return payload


# ----- configuration --------------------------------------------------


def test_app_config_requires_a_client_id(core: Any) -> None:
    with pytest.raises(core.OutlookAuthError) as excinfo:
        core.app_config({"client_id": "   "})
    assert "client) ID" in str(excinfo.value)


def test_app_config_trims_and_defaults_the_tenant(core: Any) -> None:
    cfg = core.app_config({"client_id": "  cid  "})
    assert cfg.client_id == "cid"
    assert cfg.tenant == "common"
    assert cfg.devicecode_url.endswith("/common/oauth2/v2.0/devicecode")
    assert cfg.token_url.endswith("/common/oauth2/v2.0/token")


def test_app_config_keeps_an_explicit_tenant(core: Any) -> None:
    cfg = core.app_config({"client_id": "cid", "tenant": "contoso.onmicrosoft.com"})
    assert "/contoso.onmicrosoft.com/" in cfg.devicecode_url


# ----- starting a sign-in ---------------------------------------------


def test_start_device_code_stores_the_secret_and_returns_the_user_code(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(DEVICE_CODE_URL, _device_code_payload())

    pending = core.start_device_code(cfg, core_dir)

    assert pending["user_code"] == "ABCD-EFGH"
    assert pending["verification_uri"] == "https://microsoft.com/devicelogin"
    # The device_code is what proves the session; it must not travel to the UI.
    assert "device_code" not in pending
    stored = core.pending_device_code(core_dir)
    assert stored["user_code"] == "ABCD-EFGH"
    assert "device_code" not in stored
    assert (core_dir / "device_code.json").exists()


def test_start_device_code_sends_client_id_and_scopes(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(DEVICE_CODE_URL, _device_code_payload())

    core.start_device_code(cfg, core_dir)

    body = fake_http.last_request(DEVICE_CODE_URL).data.decode()
    assert "client_id=test-client-id" in body
    assert "offline_access" in body
    assert "Calendars.Read" in body


def test_start_device_code_explains_a_public_client_misconfiguration(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(DEVICE_CODE_URL, {"error": "unauthorized_client"}, status=400)

    with pytest.raises(core.OutlookAuthError) as excinfo:
        core.start_device_code(cfg, core_dir)

    assert "public client flows" in str(excinfo.value)


def test_start_device_code_reports_an_unreachable_endpoint(
    core: Any, cfg: Any, core_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    with pytest.raises(core.OutlookApiError) as excinfo:
        core.start_device_code(cfg, core_dir)

    assert "Couldn't reach" in str(excinfo.value)


# ----- polling --------------------------------------------------------


def test_poll_without_a_pending_flow(core: Any, cfg: Any, core_dir: Path) -> None:
    assert core.poll_device_code(cfg, core_dir) == {"status": "none"}


def test_poll_reports_pending_while_the_user_signs_in(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(DEVICE_CODE_URL, _device_code_payload())
    core.start_device_code(cfg, core_dir)
    fake_http.add(TOKEN_URL, {"error": "authorization_pending"}, status=400)

    result = core.poll_device_code(cfg, core_dir)

    assert result["status"] == "pending"
    assert (core_dir / "device_code.json").exists()


def test_poll_stores_the_token_and_account_on_success(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(DEVICE_CODE_URL, _device_code_payload())
    core.start_device_code(cfg, core_dir)
    fake_http.add(TOKEN_URL, {"error": "authorization_pending"}, status=400)
    fake_http.add(TOKEN_URL, _token_payload())
    fake_http.add(ME_URL, {"displayName": "Ada", "userPrincipalName": "ada@example.com"})

    assert core.poll_device_code(cfg, core_dir)["status"] == "pending"
    result = core.poll_device_code(cfg, core_dir)

    assert result["status"] == "signed_in"
    assert result["account"] == "Ada (ada@example.com)"
    token = core.load_token(core_dir)
    assert token["refresh_token"] == "rt-new"
    assert token["expires_at"] > time.time()
    # The finished flow's secret is gone.
    assert not (core_dir / "device_code.json").exists()


def test_poll_survives_a_failing_account_lookup(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(DEVICE_CODE_URL, _device_code_payload())
    core.start_device_code(cfg, core_dir)
    fake_http.add(TOKEN_URL, _token_payload())
    fake_http.add(ME_URL, {"error": {"code": "boom"}}, status=500)

    result = core.poll_device_code(cfg, core_dir)

    assert result["status"] == "signed_in"
    assert core.load_token(core_dir)["refresh_token"] == "rt-new"


def test_poll_backs_off_when_told_to_slow_down(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(DEVICE_CODE_URL, _device_code_payload(interval=5))
    core.start_device_code(cfg, core_dir)
    fake_http.add(TOKEN_URL, {"error": "slow_down"}, status=400)

    result = core.poll_device_code(cfg, core_dir)

    assert result["status"] == "pending"
    assert result["interval"] == 10
    assert core.pending_device_code(core_dir)["interval"] == 10


def test_poll_clears_a_declined_sign_in(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(DEVICE_CODE_URL, _device_code_payload())
    core.start_device_code(cfg, core_dir)
    fake_http.add(TOKEN_URL, {"error": "authorization_declined"}, status=400)

    result = core.poll_device_code(cfg, core_dir)

    assert result["status"] == "declined"
    assert not (core_dir / "device_code.json").exists()


def test_poll_gives_up_on_an_expired_code_without_calling_microsoft(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(DEVICE_CODE_URL, _device_code_payload(expires_in=-1))
    core.start_device_code(cfg, core_dir)
    calls_before = len(fake_http.requests)

    result = core.poll_device_code(cfg, core_dir)

    assert result["status"] == "expired"
    assert len(fake_http.requests) == calls_before
    assert not (core_dir / "device_code.json").exists()


# ----- access tokens --------------------------------------------------


def test_access_token_uses_the_cached_one_when_it_is_still_valid(
    core: Any, cfg: Any, core_dir: Path, signed_in: dict[str, Any], fake_http: Any
) -> None:
    assert core.access_token(cfg, core_dir) == "at-1"
    assert fake_http.requests == []


def test_access_token_refreshes_when_the_cached_one_is_stale(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    core.save_token(
        core_dir,
        {
            "access_token": "at-old",
            "refresh_token": "rt-old",
            "expires_at": time.time() + 5,  # inside the skew window
            "account": "Ada",
        },
    )
    fake_http.add(TOKEN_URL, _token_payload(access_token="at-fresh", refresh_token="rt-rotated"))

    assert core.access_token(cfg, core_dir) == "at-fresh"

    stored = core.load_token(core_dir)
    # Refresh tokens rotate; losing the new one would strand the sign-in.
    assert stored["refresh_token"] == "rt-rotated"
    assert stored["account"] == "Ada"


def test_refresh_keeps_the_old_refresh_token_when_none_comes_back(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    core.save_token(core_dir, {"access_token": "", "refresh_token": "rt-keep", "expires_at": 0})
    payload = _token_payload()
    payload.pop("refresh_token")
    fake_http.add(TOKEN_URL, payload)

    core.access_token(cfg, core_dir)

    assert core.load_token(core_dir)["refresh_token"] == "rt-keep"


def test_access_token_without_any_sign_in(core: Any, cfg: Any, core_dir: Path) -> None:
    with pytest.raises(core.OutlookAuthError) as excinfo:
        core.access_token(cfg, core_dir)
    assert "Not signed in" in str(excinfo.value)


def test_rejected_refresh_token_asks_the_user_to_sign_in_again(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    core.save_token(core_dir, {"refresh_token": "rt-dead", "expires_at": 0})
    fake_http.add(TOKEN_URL, {"error": "invalid_grant"}, status=400)

    with pytest.raises(core.OutlookAuthError) as excinfo:
        core.access_token(cfg, core_dir)

    assert "Sign in again" in str(excinfo.value)


def test_clear_token_removes_both_files(
    core: Any, cfg: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(DEVICE_CODE_URL, _device_code_payload())
    core.start_device_code(cfg, core_dir)
    core.save_token(core_dir, {"refresh_token": "rt"})

    core.clear_token(core_dir)

    assert not (core_dir / "token.json").exists()
    assert not (core_dir / "device_code.json").exists()
    assert core.load_token(core_dir) == {}


def test_a_corrupt_token_file_reads_as_signed_out(core: Any, core_dir: Path) -> None:
    (core_dir / "token.json").write_text("{ not json", encoding="utf-8")
    assert core.load_token(core_dir) == {}
