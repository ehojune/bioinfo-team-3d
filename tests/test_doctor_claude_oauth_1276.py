"""#1276: `claude auth status` exits 0 for a login whose refresh token is gone, so doctor must read the dates itself."""
import json

import pytest

from labhq import doctor
from tests.test_claude_staff_home import _doctor_rows

FAR = 9_999_999_999_999


def _credentials(folder, **oauth):
    (folder / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "t", "refreshToken": "r", **oauth}}), encoding="utf-8")


@pytest.mark.parametrize("oauth,expired", [
    ({"expiresAt": 1000, "refreshTokenExpiresAt": 2000}, True),  # the refresh token ran out: nothing can renew the login
    ({"expiresAt": 1000, "refreshTokenExpiresAt": FAR}, False),  # an old access token is renewed on use
    ({"expiresAt": 1000, "refreshToken": ""}, True),  # no refresh token and the access token ran out
    ({"expiresAt": FAR, "refreshToken": ""}, False),
    ({"expiresAt": 1000}, False),  # a refresh token without a date is trusted
])
def test_session_expired_reads_only_the_dates_in_the_credentials_file(tmp_path, oauth, expired):
    _credentials(tmp_path, **oauth)
    assert doctor.claude_session_expired({"CLAUDE_CONFIG_DIR": str(tmp_path)}, now=5000) is expired


@pytest.mark.parametrize("text", [None, "", "not json", "[]", '{"claudeAiOauth": null}', '{"claudeAiOauth": []}'])
def test_session_expired_has_no_opinion_without_readable_dates(tmp_path, text):
    if text is not None:
        (tmp_path / ".credentials.json").write_text(text, encoding="utf-8")
    assert doctor.claude_session_expired({"CLAUDE_CONFIG_DIR": str(tmp_path)}, now=5000) is False


@pytest.mark.parametrize("oauth,status", [({"expiresAt": 1, "refreshTokenExpiresAt": 2}, "fail"),
                                          ({"expiresAt": 1, "refreshTokenExpiresAt": FAR}, "ok")])
def test_doctor_fails_the_login_row_when_status_passes_but_the_login_cannot_refresh(tmp_path, monkeypatch, oauth, status):
    staff = tmp_path / "home" / ".labhq" / "claude-staff"
    staff.mkdir(parents=True)
    _credentials(staff, **oauth)
    seen, make = [], doctor._row
    monkeypatch.setattr(doctor, "_row", lambda *a, **k: seen.append(make(*a, **k)) or seen[-1])
    _doctor_rows(tmp_path, monkeypatch, {"CLAUDE_CONFIG_DIR": str(staff)}, login_rc=0)
    login = next(r for r in seen if r["group"] == "login" and r["name"] == "claude_code")
    assert login["status"] == status
    if status == "fail":
        assert "/login" in login["hint"] and "refresh" in login["detail"]
        assert "accessToken" not in json.dumps(seen) and str(staff) not in json.dumps(seen)


@pytest.mark.parametrize("raw,active", [
    ('{"loggedIn": true, "authMethod": "claude.ai"}', True),
    ('{"loggedIn": true, "authMethod": "api_key"}', False),  # an old OAuth file does not matter when a key is in use
    ('{"loggedIn": true, "authMethod": "oauth_token", "apiProvider": "firstParty"}', False),
    ('{"loggedIn": true}', True), ("", True), ("Logged in using claude.ai", True), ("[]", True),
])
def test_the_saved_login_file_is_checked_only_when_it_is_the_active_source(raw, active):
    assert doctor._saved_oauth_is_active(raw) is active
