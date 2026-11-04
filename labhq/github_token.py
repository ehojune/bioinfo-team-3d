"""The GitHub token for result repositories and development logs (#687).

The environment variable named by `github.token_env` comes first. Without it, the owner-only file `github.token_file`
that `labhq init` writes when the PI pastes a token. Only the gateway process loads that file, into its own environment
(`export_to_env`); staff, runner and MCP processes never receive it, and the value is never written to the config,
printed or logged. No token at all is a valid setup: results then stay local.

Permission probes (measured 2026-10-16, #687). Each request lacks a required field, so GitHub refuses it after the
permission check and before anything is made: a probe never creates a repository, file or issue.
- POST /user/repos with {}: 422 "name must not be blank" when the token may create repositories, 403 when not.
- PUT /repos/<repo>/contents/<path> with {}: 422 ("content", "message" missing) with Contents write, 403 without.
- POST /repos/<repo>/issues with {}: 422 ("title" missing) with Issues write, 403 without; 410 when issues are off.
- A classic or OAuth token lists its scopes in X-OAuth-Scopes; `repo` covers all three.
A fine-grained token limited to selected repositories cannot reach the repositories it creates (GET answered 404 for
one it had just made), and the API does not say which access it has, so that advice is shown for every fine-grained
token.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import httpx

from .settings import Settings
from .tools import annot_keys

_TOKEN = re.compile(r"(ghp_|github_pat_|gho_|ghu_)[A-Za-z0-9_]{20,250}")
FINE_GRAINED_PREFIX = "github_pat_"
PROBE_PATH = ".labhq-permission-probe"
TOKEN_SETTINGS_URL = "https://github.com/settings/personal-access-tokens"
NEEDED = ("Repository creation(또는 Administration 쓰기)", "Contents 쓰기", "Issues 쓰기")
NEEDED_TEXT = ("fine-grained 토큰: Repository creation(또는 Administration 쓰기)·Contents 쓰기·Issues 쓰기, "
               "Repository access는 All repositories · classic 토큰: repo")


def token_path(settings: Settings) -> Path:
    return settings.path(settings.github.token_file)


def read_token(settings: Settings) -> str:
    """The token from the environment, else from the owner-only file; "" when there is none."""
    value = os.environ.get(settings.github.token_env, "").strip()
    if value:
        return value
    try:
        return token_path(settings).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError, ValueError):
        return ""


def token_source(settings: Settings) -> str | None:
    """Where the token comes from, without its value: "env", "file" or None."""
    if os.environ.get(settings.github.token_env, "").strip():
        return "env"
    return "file" if read_token(settings) else None


def valid_token(token: str) -> bool:
    return bool(_TOKEN.fullmatch(token))


def save_token(settings: Settings, token: str, keep_out: Iterable[Path] = ()) -> None:
    """Write the token for this account alone (POSIX 0600, Windows without inherited access)."""
    annot_keys.write_key(token_path(settings), token, keep_out, setting="github.token_file")


def export_to_env(settings: Settings) -> bool:
    """Load the stored token into this process's environment when the variable is unset. The gateway calls it once at
    start, so every GitHub client there reads one place. True when the file supplied the token."""
    if os.environ.get(settings.github.token_env, "").strip():
        return False
    token = read_token(settings)
    if not token:
        return False
    os.environ[settings.github.token_env] = token
    return True


@dataclass
class Permissions:
    kind: str = "unknown"  # classic | fine_grained | unknown
    create: bool | None = None
    contents: bool | None = None
    issues: bool | None = None
    probe_repo: str | None = None  # owner/name of the repository the Contents and Issues probes used
    error: str | None = None  # the check could not run (offline, rejected token)
    rejected: bool = False  # 401: expired, revoked or mistyped
    notes: list[str] = field(default_factory=list)

    def missing(self) -> list[str]:
        return [name for name, have in zip(NEEDED, (self.create, self.contents, self.issues)) if have is False]

    def unknown(self) -> list[str]:
        return [name for name, have in zip(NEEDED, (self.create, self.contents, self.issues)) if have is None]


def _verdict(response: httpx.Response) -> bool | None:
    if response.status_code == 422:
        return True
    if response.status_code in (403, 404):
        return False
    return None


def check_permissions(settings: Settings, token: str, transport: httpx.BaseTransport | None = None,
                      timeout: float = 10.0) -> Permissions:
    """What the token may do for result repositories, from probes that create nothing (module docstring)."""
    found = Permissions(kind="fine_grained" if token.startswith(FINE_GRAINED_PREFIX) else "unknown")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "labhq-doctor"}
    try:
        with httpx.Client(base_url=settings.github.api_url, headers=headers, timeout=timeout,
                          transport=transport) as client:
            user = client.get("/user")
            if user.status_code == 401:
                found.rejected, found.error = True, "GitHub가 토큰을 거부했습니다(만료·취소·잘못 붙여 넣음)"
                return found
            if user.status_code != 200:
                found.error = f"GitHub /user가 HTTP {user.status_code}로 답했습니다"
                return found
            scopes = user.headers.get("X-OAuth-Scopes")
            if scopes is not None and found.kind != "fine_grained":
                found.kind = "classic"
                granted = {scope.strip() for scope in scopes.split(",") if scope.strip()}
                full = "repo" in granted
                found.create = found.contents = found.issues = full
                if not full and "public_repo" in granted:
                    found.notes.append("public_repo 범위는 공개 저장소에만 쓸 수 있습니다. 비공개 결과 저장소에는 repo가 필요합니다")
                return found
            found.create = _verdict(client.post("/user/repos", json={}))
            listing = client.get("/user/repos", params={"affiliation": "owner", "per_page": 100, "sort": "updated"})
            repos = [r for r in (listing.json() if listing.status_code == 200 else [])
                     if isinstance(r, dict) and not r.get("archived") and r.get("full_name")]
            if not repos:
                found.notes.append("토큰이 볼 수 있는 내 저장소가 없어 Contents·Issues 쓰기는 처음 게시 때 확인합니다")
                return found
            found.probe_repo = repos[0]["full_name"]
            found.contents = _verdict(client.put(f"/repos/{found.probe_repo}/contents/{PROBE_PATH}", json={}))
            for repo in repos[:5]:  # a repository with issues turned off answers 410: try the next one
                answer = client.post(f"/repos/{repo['full_name']}/issues", json={})
                if answer.status_code != 410:
                    found.issues = _verdict(answer)
                    break
    except (httpx.HTTPError, ValueError) as exc:
        found.error = f"GitHub에 연결하지 못했습니다({type(exc).__name__})"
    return found


def describe(found: Permissions) -> tuple[str, str, str]:
    """(status, detail, hint) for doctor and init: one line each, never the token."""
    access = ("labhq가 새로 만든 저장소에 올리려면 토큰의 Repository access를 All repositories로 두세요"
              "(API로 확인할 수 없음)") if found.kind == "fine_grained" else ""
    fix = f"{TOKEN_SETTINGS_URL}에서 토큰 권한을 고치면 토큰 값은 그대로 씁니다."
    if found.rejected:
        return "warn", found.error or "토큰이 거부됐습니다", "새 토큰을 만들어 `labhq init`으로 다시 넣으세요. " + NEEDED_TEXT
    if found.error:
        return "ok", f"권한 확인 못 함: {found.error}. 처음 게시 때 확인합니다", NEEDED_TEXT
    missing = found.missing()
    if missing:
        detail = "결과 저장소에 빠진 권한: " + ", ".join(missing)
        if found.probe_repo and (found.contents is False or found.issues is False):
            detail += f" (확인한 저장소 {found.probe_repo})"
        return "warn", detail, " ".join(x for x in (fix, access, *found.notes) if x)
    unknown = found.unknown()
    detail = "결과 저장소 권한 확인됨" if not unknown else "결과 저장소 권한: 확인 못 한 것 " + ", ".join(unknown)
    return "ok", detail, " ".join(x for x in (access, *found.notes) if x)
