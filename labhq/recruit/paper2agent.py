"""파견직 채용: turn a paper (+ its code) into a contract agent with the Paper2Agent skill.

1. 인사팀 (recruiter agent, Claude Code) runs the paper2agent skill in talent/<slug>/build.
2. It returns a structured "offer letter" (JSON schema below): tools, MCP entry point, skill dir.
3. Probation: labhq starts the delivered MCP server and lists its tools.
4. A contract AgentSpec (with expiry) is written to agents/contract/ and mirrored to the talent pool, with the
   source commit, licenses, external services and labhq's own skill/MCP hashes (#424, recruit/provenance.py).

Paper2Agent output (see its SKILL.md): dist/<project>-agent/{skill/<paper>-paper, mcp/<repo>-mcp}
or dist/<repo>-mcp.zip with a USAGE.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import AgentSpec, ContractInfo, Employment, Engine, Event, ExternalService, McpServerSpec, Task
from . import provenance
from ..tools._mcpcompat import list_tools
from ..util import extract_json, short, slugify
from ..test_guard import guard_test_path

if TYPE_CHECKING:
    from ..runner.daemon import Runner

log = logging.getLogger(__name__)

_STR_OR_NULL = {"type": ["string", "null"]}
OFFER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "agent_name": {"type": "string"},
        "role_summary": {"type": "string"},
        "kind": {"type": "string", "enum": ["consultant", "technician", "full"]},
        "mcp_zip": _STR_OR_NULL,
        "mcp_dir": _STR_OR_NULL,
        "mcp_command": _STR_OR_NULL,
        "mcp_args": {"type": "array", "items": {"type": "string"}},
        "mcp_env_required": {"type": "array", "items": {"type": "string"}},
        "skill_dir": _STR_OR_NULL,
        "tools": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
            "required": ["name", "description"]}},
        "verification_status": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "source_commit": _STR_OR_NULL,
        "code_license": _STR_OR_NULL,
        "weights_license": _STR_OR_NULL,
        "external_services": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"host": {"type": "string"}, "what_is_sent": {"type": "string"}},
            "required": ["host", "what_is_sent"]}},
    },
    "required": ["agent_name", "role_summary", "kind", "mcp_zip", "mcp_dir", "mcp_command", "mcp_args",
                 "mcp_env_required", "skill_dir", "tools", "verification_status", "limitations",
                 "source_commit", "code_license", "weights_license", "external_services"],
}

# Paper2Agent's draft states (Paper2Skill strict verification exits 2) and its combined "partial" result: the
# offer_status words that keep a hire on probation even when its tools list (#424).
DRAFT_STATUSES = frozenset({"mechanical_failure", "unreviewed", "unresolved_discrepancies", "partial", "blocked",
                            "failed", "failure"})
SOURCE_MARKER = ".labhq-source.json"  # which Paper2Agent commit setup-paper2agent installed

# Known upstream defects patched into the installed copy (#662). Paper2Agent's reading_package.py builds bundle keys
# with str(path.relative_to(root)), which is backslash-separated on Windows and no longer matches the manifest and
# evidence keys. as_posix() is identical on POSIX. Each `old` text must occur exactly once; a file that already holds
# every `new` text (upstream fixed it, or an earlier install patched it) is left alone; anything else is a mismatch.
UPSTREAM_PATCHES = (
    {"id": "windows-relative-to-posix", "file": "paper2skill/scripts/reading_package.py",
     "upstream_commit": "8c2d059165ef8cdcb70dbea76655b9c2b55b38e6",
     "replace": (
         ("str(evidence.relative_to(work))", "evidence.relative_to(work).as_posix()"),
         ("str(p.relative_to(stage))", "p.relative_to(stage).as_posix()"),
         ("str(path.relative_to(root))", "path.relative_to(root).as_posix()"),
         ("str(p.relative_to(output))", "p.relative_to(output).as_posix()"),
     )},
)


def patch_state(skill: Path, patch: dict) -> str:
    """absent (no such file) / unpatched / patched / mismatch for one patch against a skill folder."""
    target = skill / patch["file"]
    if not target.is_file():
        return "absent"
    text = target.read_text(encoding="utf-8")
    olds = [text.count(old) for old, _ in patch["replace"]]
    news = [text.count(new) for _, new in patch["replace"]]
    if all(n == 1 for n in olds):
        return "unpatched"
    if not any(olds) and all(n == 1 for n in news):
        return "patched"
    return "mismatch"


def apply_upstream_patches(skill: Path) -> list[dict]:
    """Patch a staged skill copy and return the provenance records. A mismatch raises, so a changed upstream is
    reported instead of installed with the defect or half a patch."""
    records = []
    for patch in UPSTREAM_PATCHES:
        state = patch_state(skill, patch)
        if state == "mismatch":
            raise RuntimeError(f"Paper2Agent patch {patch['id']} does not match {patch['file']}; upstream changed. "
                               "Review the patch before installing this commit")
        if state == "unpatched":
            target = skill / patch["file"]
            text = target.read_text(encoding="utf-8")
            for old, new in patch["replace"]:
                text = text.replace(old, new)
            target.write_text(text, encoding="utf-8", newline="")
        records.append({"id": patch["id"], "file": patch["file"], "upstream_commit": patch["upstream_commit"],
                        "result": {"unpatched": "applied", "patched": "already_patched", "absent": "file_absent"}[state]})
    return records


def skill_patched(skill: Path) -> bool:
    """False when an installed copy is unpatched or only half patched; reinstalling patches it or reports the mismatch."""
    return all(patch_state(skill, patch) in ("patched", "absent") for patch in UPSTREAM_PATCHES)


CONTRACT_PROMPT = """You are {name}, a contract specialist (파견직) hired into the lab from a published method.
Source paper: {paper}
Code: {repo}
What you bring: {role}

Your paper-derived tools (MCP server `{server}`):
{tools}

Rules:
- Solve the assigned sub-task by applying this paper's method through its tools; do not reimplement the algorithm.
- If the request is outside the method's validated scope (organism, assay, data type), say so and hand it back
  to the CSO instead of improvising.
- Log every tool call's parameters and versions so the result is reproducible; cite output paths.
- Your tools send data to: {external}. Never pass restricted, cohort or patient data to them.
- Known limitations from onboarding:
{limits}
"""


def skill_path(engine: str, settings=None) -> Path:
    """Staff-owned install; Claude loads this dedicated plugin only for the recruiter."""
    from ..adapters.base import expand_env

    if engine not in ("claude_code", "codex"):
        raise ValueError("Paper2Agent recruiter requires claude_code or codex")
    key = "CLAUDE_CONFIG_DIR" if engine == "claude_code" else "CODEX_HOME"
    env = expand_env(getattr(settings.engines, engine).env) if settings else {}
    raw = env.get(key) or str(Path.home() / ".labhq" / ("claude-staff" if engine == "claude_code" else "codex-staff"))
    home = Path(raw).expanduser()
    if not home.is_absolute():
        raise ValueError(f"Set engines.{engine}.env.{key} to an absolute staff directory")
    if engine == "claude_code":
        home /= "labhq-paper2agent"
    path = home / "skills" / "paper2agent"
    guard_test_path(path)
    personal = [Path.home() / name for name in (".claude", ".codex", ".agents")]
    if any(path.resolve() == p.resolve() or p.resolve() in path.resolve().parents for p in personal):
        raise ValueError(f"Set engines.{engine}.env.{key} to a separate staff directory")
    return path


def skill_installed(engine: str = "claude_code", settings=None) -> bool:
    path = skill_path(engine, settings) if settings else skill_path(engine)
    guard_test_path(path / "SKILL.md")
    return (path / "SKILL.md").is_file()


def enabled(settings) -> bool:
    # Missing stays enabled for callers predating this setting; Settings validates strict booleans.
    return getattr(settings.recruit, "enabled", True) is not False


def require_enabled(settings) -> None:
    if not enabled(settings):
        raise ValueError("recruitment and Paper2Agent are disabled (recruit.enabled=false)")


def disabled_task(settings, task) -> bool:
    """Refuse stale/forged conversion tasks and attachments before recovery or workspace effects."""
    return not enabled(settings) and (
        task.agent_id == settings.recruit.agent_id
        or task.meta.get("kind") in {"recruit", "recruit_check", "plan_recruit", "paper_skill"}
        or bool(task.meta.get("paper_skills")))


def recruiter_engine(settings, recruiter) -> str:
    require_enabled(settings)
    if settings.runner.force_engine == "mock":
        return "mock"
    if recruiter is None:
        raise ValueError("recruiter is missing from the roster")
    return settings.runner.force_engine or recruiter.engine.value


def setup_paper2agent(settings, commit: str | None = None) -> list[Path]:
    """Shared CLI/doctor fix path, without changing staff or PI configuration."""
    require_enabled(settings)
    from ..doctor import _roster

    recruiter = next((a for a in _roster(settings) if a.id == settings.recruit.agent_id), None)
    engine = recruiter_engine(settings, recruiter)
    if engine == "mock":
        return []
    dst = skill_path(engine, settings)
    if engine == "codex":
        from ..adapters.base import expand_env
        if not expand_env(settings.engines.codex.env).get("CODEX_HOME"):
            raise ValueError("Set engines.codex.env.CODEX_HOME to the staff login directory")
    out = install_skill(settings.recruit.skill_source, Path.home() / ".labhq" / "cache",
                        commit=commit or settings.recruit.skill_commit, destinations=[dst])
    if engine == "claude_code":
        manifest = dst.parent.parent / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('{"name":"labhq-paper2agent"}\n', encoding="utf-8")
    # Only old copies with labhq's valid provenance marker belong to us. Never follow directory links.
    from ..adapters.owned import is_link
    for old in (Path.home() / ".claude/skills/paper2agent", Path.home() / ".agents/skills/paper2agent"):
        if not old.is_dir() or any(is_link(p) for p in (old, *old.parents)):
            continue
        try:
            marker = json.loads((old / SOURCE_MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(marker, dict) and isinstance(marker.get("source"), str)
                and marker["source"] and provenance.source_commit(marker.get("commit"))):
            shutil.rmtree(old)
    return out


def head_commit(repo: Path) -> str | None:
    """The checkout's HEAD commit, read from .git without running git (detached, a loose ref or packed-refs)."""
    git = repo / ".git"
    guard_test_path(git)
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return provenance.source_commit(head)
        ref = head[5:].strip()
        if (git / ref).is_file():
            return provenance.source_commit((git / ref).read_text(encoding="utf-8"))
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return provenance.source_commit(parts[0])
    except OSError:
        pass
    return None


def origin_url(repo: Path) -> str | None:
    """remote.origin.url from .git/config, read without running git; None when it cannot be read."""
    guard_test_path(repo / ".git" / "config")
    try:
        text = (repo / ".git" / "config").read_text(encoding="utf-8")
    except OSError:
        return None
    in_origin = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_origin = bool(re.fullmatch(r'\[remote\s+"origin"\]', line))
        elif in_origin and "=" in line:
            key, value = line.split("=", 1)
            if key.strip().lower() == "url":
                return value.strip()
    return None


def same_source(a: str, b: str) -> bool:
    def norm(url: str) -> str:
        return url.strip().rstrip("/").removesuffix(".git").rstrip("/").casefold()
    return norm(a) == norm(b)


def _remove_tree(path: Path) -> None:
    for folder, _dirs, files in os.walk(path):  # git keeps object files read-only, which Windows will not delete
        for name in files:
            try:
                os.chmod(os.path.join(folder, name), stat.S_IREAD | stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def _replace_clone(source: str, repo: Path) -> None:
    """Clone `source` beside the cached checkout and swap it in; a failed clone leaves the old one as it was."""
    work = Path(tempfile.mkdtemp(prefix=".Paper2Agent-", dir=repo.parent))
    try:
        subprocess.run(["git", "clone", "--depth", "1", source, str(work / "new")], check=True)
        repo.rename(work / "old")
        try:
            (work / "new").rename(repo)
        except OSError:
            (work / "old").rename(repo)
            raise
    finally:
        _remove_tree(work)


def _detached(repo: Path) -> bool:
    try:
        return not (repo / ".git" / "HEAD").read_text(encoding="utf-8").startswith("ref: ")
    except OSError:
        return False


def _checkout(repo: Path, ref: str) -> None:
    subprocess.run(["git", "-C", str(repo), "fetch", "--depth", "1", "origin", ref], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "--detach", "FETCH_HEAD"], check=True)


def installed_commit(engine: str = "claude_code", settings=None) -> str | None:
    """The Paper2Agent commit setup-paper2agent recorded in the installed copy, or None."""
    try:
        path = (skill_path(engine, settings) if settings else skill_path(engine)) / SOURCE_MARKER
        guard_test_path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return provenance.source_commit(data.get("commit")) if isinstance(data, dict) else None


def install_skill(source: str, cache: Path, commit: str | None = None,
                  *, destinations: list[Path] | None = None) -> list[Path]:
    """Prepare both Paper2Agent copies before replacing either installed skill.

    With `commit` (recruit.skill_commit or `setup-paper2agent --commit`) exactly that commit is installed; without
    it the latest HEAD is. Either way the installed commit is written to SOURCE_MARKER in both copies (#424)."""
    if commit is not None and provenance.source_commit(commit) != commit:
        raise ValueError("Paper2Agent commit must be a full 40-character lowercase hex commit")
    repo = cache / "Paper2Agent"
    guard_test_path(cache)
    guard_test_path(repo / ".git")
    out = destinations if destinations is not None else [skill_path(engine) for engine in ("claude_code", "codex")]
    for dst in out:
        guard_test_path(dst)
    origin = origin_url(repo) if (repo / ".git").exists() else None
    if not (repo / ".git").exists():
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", source, str(repo)], check=True)
        if commit:
            _checkout(repo, commit)
    elif origin is None or not same_source(origin, source):
        # recruit.skill_source changed, or the origin cannot be read: never pull from an origin not verified as source
        _replace_clone(source, repo)
        origin = source
        if commit:
            _checkout(repo, commit)
    elif commit or _detached(repo):  # an earlier pin leaves the checkout detached, where pull cannot run
        _checkout(repo, commit or "HEAD")
    else:
        subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"], check=True)
    installed = head_commit(repo)
    if commit and installed != commit:
        raise RuntimeError(f"Paper2Agent checkout is at commit {installed}, not the pinned {commit}")
    skill = repo / "skills" / "paper2agent"
    if not skill.is_dir() or not (skill / "SKILL.md").is_file():
        raise FileNotFoundError(f"Paper2Agent source skill is missing SKILL.md: {skill}")
    staged: list[tuple[Path, Path]] = []
    published: set[Path] = set()
    backups: set[Path] = set()
    complete = False
    try:
        for dst in out:
            dst.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=".paper2agent-", dir=dst.parent))
            staged.append((dst, stage))
            shutil.copytree(skill, stage / "new")
            if not (stage / "new" / "SKILL.md").is_file():
                raise FileNotFoundError(f"Staged Paper2Agent skill is missing SKILL.md: {stage / 'new'}")
            patches = apply_upstream_patches(stage / "new")  # a mismatch aborts before either copy is replaced
            marker = json.dumps({"source": origin or source, "commit": installed, "patches": patches}) + "\n"
            (stage / "new" / SOURCE_MARKER).write_text(marker, encoding="utf-8")
        for dst, stage in staged:
            if dst.exists() or dst.is_symlink():
                dst.rename(stage / "backup")
                backups.add(dst)
            (stage / "new").rename(dst)
            published.add(dst)
        complete = True
    except BaseException as install_error:
        recovery: list[str] = []
        for dst, stage in reversed(staged):
            try:
                if dst in published:
                    if dst.is_symlink() or not dst.is_dir():
                        dst.unlink()
                    else:
                        shutil.rmtree(dst)
                if dst in backups:
                    (stage / "backup").rename(dst)
                    backups.remove(dst)
            except OSError:
                recovery.append(str(stage / "backup") if dst in backups else str(dst))
        if recovery:
            raise RuntimeError("Paper2Agent update failed; rollback needs manual recovery. "
                               "Preserved paths: " + ", ".join(recovery)) from install_error
        raise
    finally:
        for dst, stage in staged:
            # A failed rollback must never clean up the only remaining copy of an old installation.
            if complete or dst not in backups:
                try:
                    shutil.rmtree(stage)
                except OSError as cleanup_error:
                    log.warning("Paper2Agent staging cleanup failed; inspect %s: %s", stage, cleanup_error)
    return out


def build_prompt(paper: str | None, repo: str | None, focus: str | None, out_dir: Path) -> str:
    lines = [
        "Use the paper2agent skill to agentify this paper and its associated files, alongside its code "
        "repository if available. Follow the skill instructions for the workflow, verification, and final delivery.",
        "",
        f"Paper and associated files: {paper or 'none (code-only conversion)'}",
        f"Code repository (if available): {repo or 'none (paper-only conversion)'}",
        f"Output directory: {out_dir}",
    ]
    if focus:
        lines.append(f"Focus on: {focus}")
    lines += [
        "",
        "Do not register the server with any client. After delivery, extract the delivered MCP ZIP to "
        f"{out_dir}/labhq_mcp, install it exactly as its USAGE.md says, and confirm it starts over stdio.",
        "Then answer with the offer-letter JSON: absolute paths for mcp_zip, mcp_dir and skill_dir; the exact "
        "tested interpreter as mcp_command and the server entry point as mcp_args (absolute paths); names "
        "(never values) of required environment variables; the delivered tools; and verification_status and "
        "limitations exactly as the skill's final report states them. Use null for parts that were not built.",
        "Also report what the hire is built from and where it sends data: source_commit, the full 40-character "
        "commit of the code repository you converted (git rev-parse HEAD in its checkout; null without a repo); "
        "code_license and weights_license as the repository and any model weights state them (null when none or "
        "not stated); external_services, every host the delivered tools contact at run time with what each one "
        "receives (empty when the tools run fully locally).",
        "Never write credentials into files or into the offer.",
    ]
    return "\n".join(lines)


def probation(kind: str, tools: list[str], skill_dir: str | None, offer_status: Any) -> tuple[bool, str | None]:
    """Probation passes when the delivered parts work and Paper2Agent's own status is not a draft state (#424)."""
    if not (bool(skill_dir) if kind == "consultant" else bool(tools)):
        return False, "no skill delivered" if kind == "consultant" else "MCP server listed no tools"
    words = set(re.findall(r"[a-z_]+", str(offer_status or "").lower()))
    draft = sorted(words & DRAFT_STATUSES)
    return (False, f"offer_status {', '.join(draft)}") if draft else (True, None)


def conversion_budget(settings, parent_task_id, budget, remaining):
    """A reserved plan hire spends its existing hold, never the recruiter's larger default.

    Manual hires and reservation-off plans retain their configured limit. Claude's CLI renders cents, so round
    down before sending: rounding a sub-cent reservation up would authorize more than its hold.
    """
    if not parent_task_id or not settings.policy.budget.reserve_before_dispatch:
        return settings.recruit.max_budget_usd, None
    values = (budget, remaining, settings.recruit.max_budget_usd)
    if any(value is None for value in values):
        raise ValueError("reserved recruitment requires a parent budget and remaining allowance")
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("reserved recruitment requires finite positive allowances")
    cap = float(Decimal(str(min(values))).quantize(Decimal("0.01"), rounding=ROUND_FLOOR))
    if cap <= 0:
        raise ValueError("reserved recruitment allowance is below one CLI cent")
    return cap, cap


class Recruitment:
    def __init__(self, runner: "Runner", msg: dict):
        self.r, self.s = runner, runner.s
        self.paper, self.repo, self.focus = msg.get("paper"), msg.get("repo"), msg.get("focus")
        self.ttl_days = float(msg.get("ttl_days") or self.s.recruit.default_ttl_days)
        self.request_id = msg.get("request_id")
        self.plan_task_id = msg.get("plan_task_id")
        self.parent_budget = msg.get("parent_budget_usd")
        self.parent_remaining = msg.get("parent_budget_remaining_usd")
        self.slug = slugify(msg.get("name") or self.repo or self.paper or "contract")

    async def _emit(self, typ: str, data: dict) -> None:
        await self.r.emit(Event(type=typ, request_id=self.request_id, agent_id=self.s.recruit.agent_id,
                                data={"slug": self.slug, **data,
                                      **({"plan_task_id": self.plan_task_id} if self.plan_task_id else {})}))

    def conversion_task(self, build) -> Task:
        budget, remaining = conversion_budget(self.s, self.plan_task_id, self.parent_budget, self.parent_remaining)
        return Task(
            agent_id=self.s.recruit.agent_id, request_id=self.request_id, output_schema=OFFER_SCHEMA,
            prompt=build_prompt(self.paper, self.repo, self.focus, build), budget_usd=budget,
            request_budget_remaining_usd=remaining,
            meta={"kind": "recruit", "slug": self.slug, "title": f"파견직 채용 변환: {self.slug}",
                  "agent_overrides": {"permission_mode": self.s.recruit.permission_mode}},
        )

    async def run(self) -> AgentSpec | None:
        require_enabled(self.s)
        # Validate inherited allowances before creating a build directory or preparing a model turn.
        conversion_budget(self.s, self.plan_task_id, self.parent_budget, self.parent_remaining)
        talent = self.r.registry.talent_dir / self.slug
        build = talent / "build"
        await asyncio.to_thread(lambda: build.mkdir(parents=True, exist_ok=True))  # off the event loop (#606)
        recruiter = self.r.registry.agents.get(self.s.recruit.agent_id)
        engine = recruiter_engine(self.s, recruiter)
        real = engine != "mock"
        if real and not skill_installed(engine, self.s):
            await self._emit("recruit.failed", {"error": "paper2agent skill not installed — run `labhq setup-paper2agent`"})
            return None

        await self._emit("recruit.status", {"stage": "converting", "paper": self.paper, "repo": self.repo})
        task = self.conversion_task(build)
        # Tracked like a dispatched task: a card answer that arrives after this turn ended must not mark the
        # recruiter working again (PR #526 review, #521).
        if self.plan_task_id and self.s.policy.budget.reserve_before_dispatch:
            self.r._bind_recruitment_conversion(self.plan_task_id, task)
        turn = asyncio.ensure_future(self.r.run_task(task, workdir_override=build))
        self.r.tasks[task.id] = turn
        result = await turn
        offer = result.structured if isinstance(result.structured, dict) else extract_json(result.text)
        if not result.ok or not isinstance(offer, dict):
            await self._emit("recruit.failed", {"error": result.error or "no offer letter", "text": short(result.text, 500)})
            return None

        spec = self._to_spec(offer, talent)
        await self._emit("recruit.status", {"stage": "probation", "agent_id": spec.id})
        tools: list[str] = []
        probe_error = None
        if spec.mcp:
            try:
                tools = await list_tools(spec.mcp[0])
            except Exception as e:
                probe_error = f"{type(e).__name__}: {e}"
        assert spec.contract
        c = spec.contract
        c.skill_sha256 = provenance.folder_sha256(c.skill_dir)
        mcp_target = offer.get("mcp_dir") or offer.get("mcp_zip")  # a ZIP-only delivery is hashed as the file
        c.mcp_sha256 = provenance.folder_sha256(mcp_target)
        c.mcp_path = str(mcp_target) if mcp_target else None
        c.mcp_entry_path = provenance.mcp_entry_tree(offer)  # what actually runs, when outside mcp_dir
        c.mcp_entry_sha256 = provenance.folder_sha256(c.mcp_entry_path)
        status = offer.get("verification_status")
        c.verification = {**c.verification, "offer_status": status, "probe_tools": tools,
                          "probe_error": probe_error, "limitations": offer.get("limitations", [])}
        if real:
            c.verification["paper2agent_commit"] = installed_commit(engine, self.s)
        passed, reason = probation(c.kind, tools, c.skill_dir, status)
        unhashed = provenance.missing_hashes(spec)
        if passed and unhashed:  # a delivered part labhq cannot hash could change unseen: the PI checks it first
            passed, reason = False, f"provenance hash failed: {', '.join(unhashed)}"
        if reason:
            c.verification["probation_reason"] = reason
        c.status = "active" if passed else "probation"
        path = await self.r.registry.asave_contract(spec)
        await self._emit("recruit.done", {"agent": spec.summary(), "path": str(path), "passed_probation": passed})
        return spec

    def _provenance(self, offer: dict) -> tuple[dict, list[str]]:
        """ContractInfo fields from the offer; a malformed value is dropped and noted, never fatal."""
        notes = []
        commit = provenance.source_commit(offer.get("source_commit"))
        if offer.get("source_commit") and not commit:
            notes.append(f"source_commit is not a full 40-hex commit: {short(offer.get('source_commit'), 60)}")
        elif self.repo and not commit:
            notes.append("source_commit missing for a code repository")
        services = []
        for raw in offer.get("external_services") or []:
            # Kept, never dropped: a lost declaration would open restricted steps to this hire. The raw entry is not
            # echoed into a note, since a URL host may carry a credential.
            entry = raw if isinstance(raw, dict) else {"host": raw}
            host, note = provenance.service_host(entry.get("host"))
            sent = str(entry.get("what_is_sent") or "").strip()
            services.append(ExternalService(host=host, what_is_sent=sent or "not stated"))
            notes += [note] if note else []
            if not sent:
                notes.append(f"external_services {host}: what_is_sent not stated")
        if offer.get("mcp_env_required") and not services:
            notes.append("mcp_env_required names keys but external_services is empty; check whether the tools call "
                         "an outside API")
        licenses = {key: str(offer.get(key) or "").strip() or None for key in ("code_license", "weights_license")}
        return {"source_commit": commit, "external_services": services, **licenses}, notes

    def _to_spec(self, offer: dict, talent: Path) -> AgentSpec:
        skill_dir = None
        src = offer.get("skill_dir")
        if src and Path(src).exists():
            dst = talent / "skill" / Path(src).name
            shutil.copytree(src, dst, dirs_exist_ok=True)
            skill_dir = str(dst)
        server = f"paper_{self.slug.replace('-', '_')}"[:40]
        mcp = []
        if offer.get("mcp_command"):
            mcp.append(McpServerSpec(
                name=server, command=offer["mcp_command"], args=list(offer.get("mcp_args") or []),
                cwd=offer.get("mcp_dir"),
                env={k: "${" + k + "}" for k in offer.get("mcp_env_required") or []},  # values from runner env
            ))
        kind = offer.get("kind") or ("full" if mcp and skill_dir else "technician" if mcp else "consultant")
        tools_txt = "\n".join(f"- {t.get('name')}: {t.get('description', '')}" for t in offer.get("tools") or []) or "- (skill only)"
        limits = "\n".join(f"- {x}" for x in offer.get("limitations") or []) or "- (none reported)"
        fields, notes = self._provenance(offer)
        external = ", ".join(f"{s.host} ({s.what_is_sent})" for s in fields["external_services"]) or \
            "no external service (declared at hiring)"
        now = time.time()
        return AgentSpec(
            id=f"c_{self.slug}"[:48], name=offer.get("agent_name") or self.slug,
            role=f"[파견] {offer.get('role_summary', '')}"[:200], character="paper-hat-chick",
            engine=Engine(self.s.recruit.contract_engine), model=self.s.recruit.contract_model,
            model_tier=self.s.recruit.contract_model_tier,
            system_prompt=CONTRACT_PROMPT.format(name=offer.get("agent_name"), paper=self.paper, repo=self.repo,
                                                 role=offer.get("role_summary"), server=server, tools=tools_txt,
                                                 limits=limits, external=external),
            builtin_mcp=["approval", "hpc"], mcp=mcp, employment=Employment.contract,
            contract=ContractInfo(paper=self.paper, repo=self.repo, kind=kind, hired_at=now,
                                  expires_at=now + self.ttl_days * 86400, skill_dir=skill_dir, talent_dir=str(talent),
                                  verification={"provenance_notes": notes} if notes else {}, **fields),
            tags=["contract", "paper2agent"],
        )
