"""GitHub token at setup and its real permissions (#687, PI request 2026-10-16).

`labhq init` asks for a token without requiring one and lists the permissions a result repository needs; the value goes
to an owner-only file, never the config or the screen. doctor judges the token by probes that create nothing (measured
2026-10-16: 422 with the permission, 403 without), not by its prefix."""

import json
import os

import httpx
import pytest

from labhq import doctor, github_token, init_wizard, private_paths
from labhq.settings import Settings

REAL_LABHQ_ENTRIES = private_paths._labhq_entries  # taken at import: conftest stubs it for each test


def fine_grained(extra="Q7"):
    return "github_pat_" + extra * 12  # built here so the file holds no token-shaped literal


def classic(extra="k3"):
    return "ghp_" + extra * 12


def settings_in(tmp_path):
    settings = Settings()
    settings.github.token_file = str(tmp_path / "secrets-github" / "github_token")
    settings.runner.state_dir = str(tmp_path / "state")
    settings.runner.workspace_root = str(tmp_path / "runs")
    settings.gateway.state_dir = str(tmp_path / "gateway")
    return settings


def github(routes, calls=None):
    """A fake GitHub: routes maps (method, path) to (status, json body, headers)."""
    def handler(request):
        if calls is not None:
            calls.append((request.method, request.url.path, request.content.decode() or None))
        status, body, headers = routes.get((request.method, request.url.path), (404, {"message": "Not Found"}, {}))
        return httpx.Response(status, json=body, headers=headers)
    return httpx.MockTransport(handler)


FULL = {("GET", "/user"): (200, {"login": "me"}, {}),
        ("POST", "/user/repos"): (422, {"message": "New repository name must not be blank"}, {}),
        ("GET", "/user/repos"): (200, [{"full_name": "me/notes"}, {"full_name": "me/old", "archived": True}], {}),
        ("PUT", "/repos/me/notes/contents/.labhq-permission-probe"): (422, {"message": "Invalid request."}, {}),
        ("POST", "/repos/me/notes/issues"): (422, {"message": "Invalid request."}, {})}


def test_the_token_comes_from_the_variable_first_then_the_owner_only_file(tmp_path, monkeypatch):
    settings = settings_in(tmp_path)
    assert github_token.read_token(settings) == "" and github_token.token_source(settings) is None
    github_token.save_token(settings, fine_grained())
    assert github_token.read_token(settings) == fine_grained() and github_token.token_source(settings) == "file"
    assert fine_grained() not in json.dumps(settings.model_dump(mode="json"))  # the config holds only the location
    monkeypatch.setenv("GITHUB_TOKEN", classic())
    assert github_token.read_token(settings) == classic() and github_token.token_source(settings) == "env"


def test_staff_may_not_read_the_stored_token(tmp_path, monkeypatch):
    """Like the AlphaGenome key, the token file is a private path: only the gateway reads it."""
    settings = settings_in(tmp_path)
    assert str(github_token.token_path(settings)) not in [p for p, _ in REAL_LABHQ_ENTRIES(settings, str(tmp_path))]
    github_token.save_token(settings, fine_grained())
    assert str(github_token.token_path(settings)) in [p for p, _ in REAL_LABHQ_ENTRIES(settings, str(tmp_path))]


def test_the_gateway_loads_a_stored_token_only_when_the_variable_is_unset(tmp_path, monkeypatch):
    settings = settings_in(tmp_path)
    assert github_token.export_to_env(settings) is False and "GITHUB_TOKEN" not in os.environ
    github_token.save_token(settings, fine_grained())
    assert github_token.export_to_env(settings) is True and os.environ["GITHUB_TOKEN"] == fine_grained()
    monkeypatch.setenv("GITHUB_TOKEN", classic())
    assert github_token.export_to_env(settings) is False and os.environ["GITHUB_TOKEN"] == classic()


def test_the_token_folder_holds_only_the_token_and_names_its_own_setting(tmp_path):
    settings = settings_in(tmp_path)
    folder = tmp_path / "secrets-github"
    folder.mkdir()
    (folder / "other.txt").write_text("x", encoding="utf-8")
    with pytest.raises(OSError, match="github.token_file"):
        github_token.save_token(settings, fine_grained())


@pytest.mark.parametrize("value, ok", [(fine_grained(), True), (classic(), True), ("gho_" + "a" * 30, True),
                                       ("github_pat_short", False), ("ghp_" + "a" * 30 + " x", False), ("hello", False)])
def test_token_shape(value, ok):
    assert github_token.valid_token(value) is ok


def test_a_token_with_every_permission_is_ok_and_the_probes_create_nothing(tmp_path):
    calls = []
    found = github_token.check_permissions(settings_in(tmp_path), fine_grained(), transport=github(FULL, calls))
    assert (found.create, found.contents, found.issues, found.probe_repo) == (True, True, True, "me/notes")
    status, detail, hint = github_token.describe(found)
    assert status == "ok" and "권한 확인됨" in detail and "All repositories" in hint
    writes = [call for call in calls if call[0] in ("POST", "PUT")]
    assert writes and all(body == "{}" for _, _, body in writes)  # no name, content, message or title: nothing made


def test_the_pi_token_of_10_16_is_told_what_is_missing_by_name(tmp_path):
    """Measured 10-16: may create repositories (422) but Contents and Issues answered 403."""
    routes = {**FULL,
              ("PUT", "/repos/me/notes/contents/.labhq-permission-probe"): (403, {"message": "Resource not accessible"}, {}),
              ("POST", "/repos/me/notes/issues"): (403, {"message": "Resource not accessible"}, {})}
    token = fine_grained()
    found = github_token.check_permissions(settings_in(tmp_path), token, transport=github(routes))
    status, detail, hint = github_token.describe(found)
    assert status == "warn" and found.missing() == ["Contents 쓰기", "Issues 쓰기"]
    assert "Contents 쓰기, Issues 쓰기" in detail and "me/notes" in detail
    assert github_token.TOKEN_SETTINGS_URL in hint and "All repositories" in hint
    assert token not in detail + hint


def test_a_classic_token_is_judged_by_its_scopes(tmp_path):
    scoped = lambda scopes: {("GET", "/user"): (200, {"login": "me"}, {"X-OAuth-Scopes": scopes})}
    calls = []
    full = github_token.check_permissions(settings_in(tmp_path), classic(), transport=github(scoped("repo, workflow"), calls))
    assert (full.kind, full.create, full.contents, full.issues) == ("classic", True, True, True)
    assert [c[0] for c in calls] == ["GET"]  # the scopes answer it; no probe is sent
    public = github_token.check_permissions(settings_in(tmp_path), classic(), transport=github(scoped("public_repo")))
    status, detail, hint = github_token.describe(public)
    assert status == "warn" and len(public.missing()) == 3 and "public_repo" in hint


def test_a_rejected_token_warns_and_an_unreachable_github_does_not(tmp_path):
    rejected = github_token.check_permissions(settings_in(tmp_path), fine_grained(),
                                              transport=github({("GET", "/user"): (401, {"message": "Bad"}, {})}))
    assert github_token.describe(rejected)[0] == "warn" and rejected.rejected

    def offline(request):
        raise httpx.ConnectError("no network", request=request)

    down = github_token.check_permissions(settings_in(tmp_path), fine_grained(), transport=httpx.MockTransport(offline))
    status, detail, _ = github_token.describe(down)
    assert status == "ok" and "확인 못 함" in detail and "처음 게시 때" in detail


def test_issues_turned_off_on_one_repository_tries_the_next(tmp_path):
    routes = {**FULL, ("GET", "/user/repos"): (200, [{"full_name": "me/notes"}, {"full_name": "me/lab"}], {}),
              ("POST", "/repos/me/notes/issues"): (410, {"message": "Issues are disabled"}, {}),
              ("POST", "/repos/me/lab/issues"): (403, {"message": "Resource not accessible"}, {})}
    found = github_token.check_permissions(settings_in(tmp_path), fine_grained(), transport=github(routes))
    assert found.issues is False and found.missing() == ["Issues 쓰기"]


def test_with_no_repository_to_probe_contents_and_issues_wait_for_the_first_publish(tmp_path):
    routes = {**FULL, ("GET", "/user/repos"): (200, [], {})}
    found = github_token.check_permissions(settings_in(tmp_path), fine_grained(), transport=github(routes))
    status, detail, hint = github_token.describe(found)
    assert status == "ok" and found.create is True and "Contents 쓰기" in detail and "처음 게시 때" in hint


def test_doctor_dry_run_never_reaches_github(tmp_path, monkeypatch):
    settings = settings_in(tmp_path)
    github_token.save_token(settings, fine_grained())
    monkeypatch.setattr(github_token, "check_permissions",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("dry-run probed GitHub")))
    row = doctor._github_token_row(settings, dry_run=True)
    assert row["status"] == "ok" and "dry-run" in row["detail"]


class Prompt:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def __call__(self, text):
        self.asked.append(text)
        return self.answer


def test_init_lists_the_permissions_and_skips_on_enter(tmp_path, monkeypatch, capsys):
    prompt = Prompt("")
    monkeypatch.setattr(init_wizard, "_interactive", lambda: True)
    monkeypatch.setattr(init_wizard.getpass, "getpass", prompt)
    settings = settings_in(tmp_path)
    init_wizard._github_token(settings)
    out = capsys.readouterr().out
    assert len(prompt.asked) == 1 and "없으면 Enter" in prompt.asked[0]
    for need in ("Repository creation", "Contents 쓰기", "Issues 쓰기", "All repositories", "repo"):
        assert need in out
    assert "로컬에만 저장" in out and "건너뜀" in out and not github_token.token_path(settings).exists()


def test_init_stores_a_pasted_token_without_printing_it_or_its_place(tmp_path, monkeypatch, capsys):
    token = fine_grained()
    monkeypatch.setattr(init_wizard, "_interactive", lambda: True)
    monkeypatch.setattr(init_wizard.getpass, "getpass", Prompt(token))
    settings = settings_in(tmp_path)
    init_wizard._github_token(settings)
    out = capsys.readouterr().out
    assert github_token.read_token(settings) == token and "토큰 저장" in out
    assert token not in out and "github_pat_" not in out and str(tmp_path) not in out
    keep = Prompt("")
    monkeypatch.setattr(init_wizard.getpass, "getpass", keep)
    init_wizard._github_token(settings)  # a second init offers a replacement; Enter keeps the stored token
    assert "그대로 두려면 Enter" in keep.asked[0] and "그대로 씁니다" in capsys.readouterr().out
    assert github_token.read_token(settings) == token


def test_init_replaces_an_expired_stored_token(tmp_path, monkeypatch, capsys):
    """doctor tells a rejected token to go through `labhq init` again, so init must take a new one (PR #700 review)."""
    settings = settings_in(tmp_path)
    github_token.save_token(settings, fine_grained("Q7"))
    monkeypatch.setattr(init_wizard, "_interactive", lambda: True)
    monkeypatch.setattr(init_wizard.getpass, "getpass", Prompt(fine_grained("R8")))
    init_wizard._github_token(settings)
    out = capsys.readouterr().out
    assert github_token.read_token(settings) == fine_grained("R8") and "토큰 저장" in out
    assert fine_grained("R8") not in out and fine_grained("Q7") not in out


def test_with_the_variable_set_init_does_not_ask(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", classic())
    monkeypatch.setattr(init_wizard, "_interactive", lambda: True)
    monkeypatch.setattr(init_wizard.getpass, "getpass", lambda *_: (_ for _ in ()).throw(AssertionError("asked")))
    init_wizard._github_token(settings_in(tmp_path))
    assert "환경 변수 GITHUB_TOKEN" in capsys.readouterr().out


def test_init_refuses_something_that_is_not_a_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(init_wizard, "_interactive", lambda: True)
    monkeypatch.setattr(init_wizard.getpass, "getpass", Prompt("my password"))
    settings = settings_in(tmp_path)
    init_wizard._github_token(settings)
    assert "저장하지 않았습니다" in capsys.readouterr().out and not github_token.token_path(settings).exists()


def test_init_never_asks_without_a_person_at_the_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(init_wizard, "_interactive", lambda: False)
    monkeypatch.setattr(init_wizard.getpass, "getpass", lambda *_: (_ for _ in ()).throw(AssertionError("asked")))
    init_wizard._github_token(settings_in(tmp_path))
