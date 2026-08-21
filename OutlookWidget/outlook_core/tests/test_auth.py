"""outlook_core: app config, the device-code sign-in, and token refresh."""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
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


# ----- the optional shared-calendar scope -----------------------------


def test_read_shared_is_off_by_default(core: Any) -> None:
    cfg = core.app_config({"client_id": "cid"})
    assert cfg.read_shared is False
    assert core.SHARED_SCOPE not in cfg.scopes


def test_read_shared_adds_the_scope_when_enabled(core: Any) -> None:
    cfg = core.app_config({"client_id": "cid", "read_shared": True})
    assert cfg.read_shared is True
    assert core.SHARED_SCOPE in cfg.scopes
    # the base scopes must survive alongside it
    assert "Calendars.Read" in cfg.scopes
    assert "offline_access" in cfg.scopes


def test_sign_in_requests_the_shared_scope_only_when_asked(
    core: Any, core_dir: Path, fake_http: Any
) -> None:
    fake_http.add(DEVICE_CODE_URL, _device_code_payload())
    plain = core.AppConfig(client_id="cid")
    core.start_device_code(plain, core_dir)
    assert core.SHARED_SCOPE not in fake_http.last_request(DEVICE_CODE_URL).data.decode()

    core.start_device_code(core.AppConfig(client_id="cid", read_shared=True), core_dir)
    body = fake_http.last_request(DEVICE_CODE_URL).data.decode()
    assert "Calendars.Read.Shared" in urllib.parse.unquote_plus(body)


def test_refresh_asks_for_the_granted_scopes_not_the_configured_ones(
    core: Any, core_dir: Path, fake_http: Any
) -> None:
    """Widening the request on a refresh is an error, so a token issued before
    the setting was turned on must keep refreshing against its own scopes."""
    core.save_token(
        core_dir,
        {"access_token": "", "refresh_token": "rt-old", "expires_at": 0, "scope": core.SCOPES},
    )
    fake_http.add(TOKEN_URL, _token_payload())
    core.access_token(core.AppConfig(client_id="cid", read_shared=True), core_dir)
    body = urllib.parse.unquote_plus(fake_http.last_request(TOKEN_URL).data.decode())
    assert "Calendars.Read.Shared" not in body
    assert "Calendars.Read" in body


def test_refresh_falls_back_to_base_scopes_for_a_token_with_none_recorded(
    core: Any, core_dir: Path, fake_http: Any
) -> None:
    core.save_token(core_dir, {"access_token": "", "refresh_token": "rt-old", "expires_at": 0})
    fake_http.add(TOKEN_URL, _token_payload())
    core.access_token(core.AppConfig(client_id="cid"), core_dir)
    assert "Calendars.Read" in urllib.parse.unquote_plus(
        fake_http.last_request(TOKEN_URL).data.decode()
    )


def test_has_shared_scope_reads_what_was_actually_granted(core: Any) -> None:
    assert core.has_shared_scope({"scope": "Calendars.Read Calendars.Read.Shared User.Read"})
    assert not core.has_shared_scope({"scope": core.SCOPES})
    assert not core.has_shared_scope({})
    # Calendars.Read is a prefix of Calendars.Read.Shared; don't match on it
    assert not core.has_shared_scope({"scope": "Calendars.Read"})
    # the identity platform is inconsistent about case
    assert core.has_shared_scope({"scope": "calendars.read.shared"})


def test_status_separates_wanting_the_scope_from_having_it(
    core: Any, core_dir: Path, signed_in: dict[str, Any]
) -> None:
    state = core.status(core.AppConfig(client_id="cid", read_shared=True), core_dir)
    assert state["wants_shared"] is True
    assert state["shared_granted"] is False

    core.save_token(core_dir, {**signed_in, "scope": f"{core.SCOPES} {core.SHARED_SCOPE}"})
    state = core.status(core.AppConfig(client_id="cid", read_shared=True), core_dir)
    assert state["shared_granted"] is True


def test_status_reports_no_shared_grant_when_signed_out(core: Any, core_dir: Path) -> None:
    state = core.status(core.AppConfig(client_id="cid", read_shared=True), core_dir)
    assert state["signed_in"] is False
    assert state["shared_granted"] is False


def test_consent_failure_names_the_setting_to_turn_off(core: Any) -> None:
    message = core._auth_error_message(
        {
            "error": "invalid_grant",
            "error_description": "AADSTS65001: The user or administrator has not consented.",
        },
        "fallback",
    )
    assert "Read calendars shared with me" in message
    assert message != "fallback"


def test_admin_consent_failure_is_explained_too(core: Any) -> None:
    message = core._auth_error_message(
        {"error": "invalid_grant", "error_description": "AADSTS90094: Admin consent is required."},
        "fallback",
    )
    assert "administrator" in message
