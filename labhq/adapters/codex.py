"""OpenAI Codex CLI adapter (`codex exec --json`).

Role instructions go to AGENTS.md in the workspace (Codex reads it as project guidance).
Prompts use compact XML blocks (task / output contract / action safety / verification / grounding),
which Codex follows more reliably than long prose.

Observed in codex-cli 0.155.0-alpha.16 (openai/codex#24135): exec defaults to approval
policy never, so MCP calls fail unless the server's default_tools_approval_mode is approve.
labhq's own MCP tools enforce phone approval inside the broker.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..util import openai_strict_schema, short, strip_optional_nulls
from .base import (AgentAdapter, role_footer, child_config_dirs, RunContext, RunState, expand_env, note_command_ok,
                   note_failed_output, record_model_id, wrap_cwd)
from .owned import read_owned, write_owned

# Staff tool names that mean "web". Codex has no per-tool rules for them; its native search turns on instead.
WEB_TOOLS = {"WebSearch", "WebFetch"}
# Features a read-only task turns off: each runs code or acts outside the `-s read-only` sandbox (hooks run shell
# commands, plugins bring hooks and MCP servers, apps are remote connector tools, computer/browser use drive the
# desktop). Names checked against `codex features list` on codex-cli 0.159.2; an unknown name is a CLI error, so a
# Codex that lacks one fails the read-only run instead of running it with that feature on.
READ_ONLY_DISABLED_FEATURES = ("hooks", "plugins", "apps", "computer_use", "browser_use")
# Off for every staff run (#421): multi_agent starts sub-agents outside labhq's step accounting, and memories carry one
# request's notes into the next through the shared staff CODEX_HOME. Checked against `codex features list` on the
# codex-cli this PC runs (2026-10-05); an unknown name fails the run, as above.
STAFF_DISABLED_FEATURES = ("multi_agent", "memories")
ELEVATED_SETUP_ERROR = (
    "Codex elevated sandbox setup is required for the staff CODEX_HOME. An unattended run cannot safely "
    "launch its administrator setup helper. Initialize that CODEX_HOME interactively before unattended work; "
    "LabHQ did not fall back to a weaker sandbox."
)
CODEX_HOME_STATE = ("auth.json", "installation_id", "sessions", ".sandbox", ".sandbox-bin")
CACHE_DIRS = {"PIP_CACHE_DIR": "pip", "MPLCONFIGDIR": "matplotlib", "XDG_CACHE_HOME": "xdg"}


SPAWN_FAILED = "Failed to create unified exec process:"  # Codex's own wording when it cannot start a command
SETUP_REQUIRED = "sandbox setup required"


def _command_needs_setup(output: str) -> bool:
    """A failed command whose output is Codex's own spawn error for the elevated sandbox: the setup helper was
    cancelled (no UAC unattended), or an app update left the setup incompatible (#328). The setup phrase counts only
    in output that starts with Codex's spawn-error prefix, so a test or grep that prints it stays an ordinary command failure."""
    if ("orchestrator_helper_launch_canceled" in output
            and "ShellExecuteExW failed to launch setup helper: 1223" in output):
        return True
    text = output.strip()
    return text.startswith(SPAWN_FAILED) and SETUP_REQUIRED in text[len(SPAWN_FAILED):].lower()


def _turn_needs_setup(message: str) -> bool:
    """Codex's turn or stream error says the elevated sandbox setup is missing or incompatible ("sandbox setup
    required: sandbox users missing or incompatible with marker version", #328)."""
    return _command_needs_setup(message) or SETUP_REQUIRED in message.lower()


def _stop_for_setup(st: RunState, error: str) -> None:
    """The first setup error ends the run (#382): every later shell command would ask for setup again, and on the
    UAC fallback each one leaves a prompt open while the command hangs."""
    if getattr(st, "error_kind", None) != "sandbox_setup_required":
        st.stop_reason = ("Codex가 elevated sandbox 준비를 요구해 labhq가 Codex를 끝냈습니다. 다음 셸 명령이 "
                          "승인 창(UAC)을 또 띄우지 않게 합니다 (#382)")
    st.error = error
    st.error_kind = "sandbox_setup_required"


def _toml(v: object) -> str:
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_toml_key(k)} = {_toml(x)}" for k, x in v.items()) + "}"
    return json.dumps(v)  # JSON strings/arrays/bools/numbers are valid TOML here


def _toml_key(key: str) -> str:
    """A bare key when TOML allows it (MCP env names stay as they were), else a quoted one (paths)."""
    return key if key and all(c.isascii() and (c.isalnum() or c in "_-") for c in key) else json.dumps(key)


# Built-in Codex permission profiles a staff profile extends; `danger-full-access` has none and keeps `-s`.
PROFILE_PARENTS = {"workspace-write": ":workspace", "read-only": ":read-only"}
STAFF_PROFILE = "labhq_staff"


def _is_windows() -> bool:
    return os.name == "nt"


# The two machine-wide accounts every elevated setup creates and gives new random passwords (#382). Each CODEX_HOME
# keeps those passwords only in its own .sandbox-secrets, so a setup run from one home leaves the other unable to log
# on; that home's next command then asks for setup again, which shows a UAC prompt or hangs an unattended run.
SANDBOX_ACCOUNTS = ("CodexSandboxOffline", "CodexSandboxOnline")
SECRETS_FILE = (".sandbox-secrets", "sandbox_users.json")
STALE_SLACK_S = 5.0  # Codex writes the secrets file right after it sets the passwords


NERR_USER_NOT_FOUND = 2221


def sandbox_accounts() -> dict[str, float | None] | None:
    """When each sandbox account last got a new password (epoch seconds), read without admin rights through
    NetUserGetInfo level 1. An account that does not exist maps to None (a Codex reinstall deletes them, and the next
    command recreates them through setup); the whole answer is None when the accounts cannot be read."""
    if not _is_windows():
        return None
    try:
        import ctypes
        import time
        from ctypes import wintypes

        class UserInfo1(ctypes.Structure):
            _fields_ = [("name", wintypes.LPWSTR), ("password", wintypes.LPWSTR), ("password_age", wintypes.DWORD),
                        ("priv", wintypes.DWORD), ("home_dir", wintypes.LPWSTR), ("comment", wintypes.LPWSTR),
                        ("flags", wintypes.DWORD), ("script_path", wintypes.LPWSTR)]

        netapi = ctypes.WinDLL("netapi32")
        netapi.NetUserGetInfo.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                          ctypes.POINTER(ctypes.c_void_p)]
        now, accounts = time.time(), {}
        for name in SANDBOX_ACCOUNTS:
            buf = ctypes.c_void_p()
            code = netapi.NetUserGetInfo(None, name, 1, ctypes.byref(buf))
            if code == NERR_USER_NOT_FOUND:
                accounts[name] = None
                continue
            if code != 0:
                return None
            try:
                age = ctypes.cast(buf, ctypes.POINTER(UserInfo1)).contents.password_age
            finally:
                netapi.NetApiBufferFree(buf)
            accounts[name] = now - age
        return accounts
    except (OSError, AttributeError, ValueError):
        return None


def elevated_home_problem(home: Path, accounts: dict[str, float | None] | None) -> str | None:
    """Why this staff CODEX_HOME's elevated setup would make Codex ask for setup again, judged from file metadata only
    (never the secrets' contents); None when it looks usable."""
    marker = home / ".sandbox" / "setup_marker.json"
    if not marker.is_file():
        return "Expected $CODEX_HOME/.sandbox/setup_marker.json"
    try:
        json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "$CODEX_HOME/.sandbox/setup_marker.json is empty or unreadable (an interrupted setup)"
    secrets = home.joinpath(*SECRETS_FILE)
    if secrets.parent.is_dir() and not secrets.is_file():
        return "$CODEX_HOME/.sandbox-secrets has no sandbox_users.json"
    try:
        written = secrets.stat().st_mtime
    except OSError:
        return None
    if not accounts:
        return None
    missing = [name for name, changed in accounts.items() if changed is None]
    if missing:
        return (f"The sandbox account {missing[0]} no longer exists (a Codex reinstall removes them), so the next "
                "command would recreate it through setup")
    if written + STALE_SLACK_S < max(accounts.values()):
        return ("The sandbox accounts got new passwords after this CODEX_HOME's setup: another Codex home on this PC "
                "ran elevated setup. Only one CODEX_HOME per PC may use windows.sandbox=\"elevated\" (#382)")
    return None


class CodexAdapter(AgentAdapter):
    engine = "codex"
    enforces_read_only = True  # -s read-only, no MCP, hooks/plugins/apps off, no user config

    def prepare(self, ctx: RunContext) -> None:
        write_owned(ctx.workdir, "AGENTS.md", ctx.agent.system_prompt.strip() + "\n" + role_footer(ctx))
        if ctx.task.output_schema:
            ctx.write_meta("output_schema.json", json.dumps(openai_strict_schema(ctx.task.output_schema)))

    def staff_env(self, ctx: RunContext) -> dict[str, str]:
        env = super().staff_env(ctx)
        if ctx.read_only:
            return env
        cache_root = ctx.workdir / ".labhq" / "cache"
        present = {name.casefold() for name in env}
        for name, directory in CACHE_DIRS.items():
            if name.casefold() not in present:
                env[name] = str(cache_root / directory)
        return env

    def compose_prompt(self, ctx: RunContext) -> str:
        t = ctx.task
        blocks = [f"<task>\n{ctx.prompt}\n</task>"]
        if t.output_schema:
            blocks.append("<structured_output_contract>\nReturn only JSON that matches the provided output "
                          "schema. No prose outside the JSON.\n</structured_output_contract>")
        blocks.append("<action_safety>\nStay inside the workspace and the listed project dirs. No unrelated "
                      "refactors. Heavy compute and anything touching restricted data goes through the "
                      "labhq_hpc tools.\n</action_safety>")
        blocks.append("<verification_loop>\nBefore finishing, check that every deliverable exists in ./outputs "
                      "and that tests or sanity checks you ran actually passed; report what you verified.\n"
                      "</verification_loop>")
        blocks.append("<grounding_rules>\nTie every claim to a file, command output or cited source. Label "
                      "hypotheses as hypotheses.\n</grounding_rules>")
        return "\n\n".join(blocks)

    def preflight_error(self, ctx: RunContext, env: dict[str, str]) -> str | None:
        b = self.settings.engines.codex
        # Codex reads AGENTS.override.md in its cwd instead of AGENTS.md, so a copy an earlier run left in a reused
        # workspace would replace the role prepare() writes, for a follow-up and for every later step (#147).
        override = ctx.workdir / "AGENTS.override.md"
        if override.exists() or override.is_symlink():
            return ("Codex staff session refused: the workspace holds AGENTS.override.md, which Codex reads instead "
                    "of labhq's role instructions (AGENTS.md) and no flag turns off. An earlier run left it; move it "
                    "out of the workspace to continue.")
        isolated = b.isolate_user_config or ctx.read_only
        homes = child_config_dirs(env, ctx.workdir, "CODEX_HOME", ".codex")
        if isolated and not b.allow_global_agents_md:
            found = [n for home in homes for n in ("AGENTS.md", "AGENTS.override.md") if (home / n).is_file()]
            if found:
                return (f"Codex staff session refused: $CODEX_HOME/{found[0]} (global instructions) would load and "
                        "no flag turns it off. Run the runner under a dedicated account, point "
                        "engines.codex.env.CODEX_HOME at a separate staff login, or set "
                        "engines.codex.allow_global_agents_md: true.")
        if isolated and _is_windows() and b.windows_sandbox == "elevated":
            used = [home for home in homes if any((home / name).exists() for name in CODEX_HOME_STATE)]
            accounts = sandbox_accounts() if used else None
            for home in used:
                problem = elevated_home_problem(home, accounts)
                if problem:
                    return f"{ELEVATED_SETUP_ERROR} {problem}; LabHQ did not start Codex or request elevation."
        return None

    def deny_read_paths(self, ctx: RunContext) -> list[str]:
        """The PI personal paths this run's sandbox itself refuses to read (#382). Only the elevated Windows sandbox
        enforces deny-read (unelevated refuses to start with it), and only that backend is measured here, so other
        platforms keep the instruction in the role footer. labhq passes windows.sandbox only to isolated runs."""
        b = self.settings.engines.codex
        if not (ctx.private_paths and _is_windows() and b.windows_sandbox == "elevated"
                and (b.isolate_user_config or ctx.read_only)):
            return []
        return list(dict.fromkeys(ctx.private_paths))

    def sandbox_flags(self, ctx: RunContext) -> list[str]:
        """`-s <mode>`, or with private paths to refuse a permission profile that extends the same built-in mode.
        Codex profiles and `-s` do not compose: a run takes one or the other."""
        sandbox = ctx.agent.sandbox
        parent = PROFILE_PARENTS.get(sandbox)
        deny = self.deny_read_paths(ctx) if parent else []
        if not deny:
            return ["-s", sandbox]
        profile = {"extends": parent, "filesystem": {path: "deny" for path in deny}}
        return ["-c", f"default_permissions={_toml(STAFF_PROFILE)}",
                "-c", f"permissions.{STAFF_PROFILE}={_toml(profile)}"]

    def build_command(self, ctx: RunContext) -> list[str]:
        a, t, b = ctx.agent, ctx.task, self.settings.engines.codex
        flags = ["--json", "--skip-git-repo-check", "-C", str(ctx.workdir), *self.sandbox_flags(ctx),
                 "-o", str(ctx.meta_dir / "last_message.txt")]
        if b.isolate_user_config or ctx.read_only:
            # config.toml carries the PI's plugins, notify hook and MCP servers. The global AGENTS.md in
            # CODEX_HOME still loads; set engines.codex.env.CODEX_HOME to a separate staff login to drop it.
            flags += ["--ignore-user-config", "--ignore-rules"]
            if _is_windows() and b.windows_sandbox:
                flags += ["-c", f"windows.sandbox={_toml(b.windows_sandbox)}"]
        for feature in (*STAFF_DISABLED_FEATURES, *(READ_ONLY_DISABLED_FEATURES if ctx.read_only else ())):
            flags += ["--disable", feature]
        if a.model:
            flags += ["-m", a.model]
        if a.effort:
            flags += ["-c", f'model_reasoning_effort="{a.effort}"']
        web_search = "live" if any(t.split("(")[0] in WEB_TOOLS for t in a.tools) else "disabled"
        flags += ["-c", f'web_search="{web_search}"']
        for d in ctx.extra_dirs:
            flags += ["--add-dir", d]
        if t.output_schema:
            flags += ["--output-schema", str(ctx.meta_dir / "output_schema.json")]
        for s in ctx.mcp_servers:
            key = f"mcp_servers.{s.name}"
            if s.name in ("labhq_hpc", "labhq_approval", "labhq_ask") or s.auto_approve:
                # labhq tools enforce phone approval inside the broker; auto_approve servers are read-only.
                flags += ["-c", f'{key}.default_tools_approval_mode="approve"']
            if s.timeout_s:  # labhq_ask waits longer than a phone approval
                flags += ["-c", f"{key}.tool_timeout_sec={s.timeout_s}"]
            elif s.name.startswith("labhq_"):
                flags += ["-c", f"{key}.tool_timeout_sec={self.settings.policy.approvals.timeout_s + 120}"]
            if s.type == "stdio":
                command, args = wrap_cwd(s)
                flags += ["-c", f"{key}.command={_toml(command)}", "-c", f"{key}.args={_toml(args)}"]
                # A value Codex already holds in its own env (the broker token among them) goes by name, so no
                # secret sits on the command line other processes of the account can read (#330).
                env, child = expand_env(s.env), self.staff_env(ctx)
                held = [k for k, v in env.items() if child.get(k) == v]
                inline = {k: v for k, v in env.items() if k not in held}
                if inline:
                    flags += ["-c", f"{key}.env={_toml(inline)}"]
                if held:
                    flags += ["-c", f"{key}.env_vars={_toml(held)}"]
            else:
                flags += ["-c", f"{key}.url={_toml(s.url)}"]
        if not ctx.read_only:  # PI extra_args could widen the sandbox; a read-only run takes none
            flags += b.extra_args
        prompt = self.compose_prompt(ctx)
        if t.resume_session_id:
            return [b.bin, "exec", *flags, "resume", t.resume_session_id, prompt]
        return [b.bin, "exec", *flags, prompt]

    async def handle_line(self, line: str, st: RunState, ctx: RunContext) -> None:
        ev = json.loads(line)
        record_model_id(st, ctx, ev.get("model_id") or ev.get("model"))
        typ = ev.get("type")
        if typ == "turn.failed":
            st.result_seen = True  # the turn is over either way: the exit guard applies (#330)
        if typ == "thread.started":
            st.session_id = ev.get("thread_id")
            await ctx.emit("agent.status", {"state": "working", "engine": "codex"})
        elif typ in ("item.started", "item.completed"):
            item = ev.get("item") or {}
            it = item.get("type") or item.get("item_type")
            if it in ("agent_message", "assistant_message") and typ == "item.completed":
                st.text_parts.append(item.get("text", ""))
                st.last_message = item.get("text", "")
                await ctx.emit("agent.log", {"text": short(item.get("text"), 2000)})
            elif it == "reasoning" and typ == "item.completed":
                await ctx.emit("agent.log", {"level": "thinking", "text": short(item.get("text"), 400)})
            elif it == "command_execution":
                exit_code = item.get("exit_code")
                if typ == "item.started":
                    await ctx.emit("agent.tool", {"name": "shell", "input": short(item.get("command"), 300)})
                elif exit_code == 0 and not isinstance(exit_code, bool):
                    st.commands_ran = True  # the sandbox started a command (#328)
                    note_command_ok(st)
                elif exit_code not in (None, 0):
                    message = str(item.get("aggregated_output") or "command failed")
                    note_failed_output(st, item.get("aggregated_output"))
                    if _command_needs_setup(message):
                        _stop_for_setup(st, ELEVATED_SETUP_ERROR)
                    await ctx.emit("agent.tool_error", {"text": short(message, 400)})
            elif it == "mcp_tool_call" and typ == "item.started":
                call = {"name": f"mcp:{item.get('server')}.{item.get('tool')}"}
                if item.get("arguments") is not None:  # tells calls apart, e.g. for the runaway detector
                    call["input"] = short(json.dumps(item["arguments"], ensure_ascii=False, sort_keys=True,
                                                     default=str), 300)
                await ctx.emit("agent.tool", call)
            elif it == "mcp_tool_call" and typ == "item.completed":
                result = item.get("result") or {}
                tool_error = isinstance(result, dict) and (result.get("isError") or result.get("is_error"))
                if item.get("status") != "failed" and not tool_error:
                    return
                err = item.get("error")
                detail = "\n".join(str(block.get("text", "")) for block in result.get("content", [])
                                   if isinstance(block, dict) and block.get("type") == "text") if tool_error else ""
                message = str((err.get("message") if isinstance(err, dict) else err) or detail or "MCP tool failed")
                # The agent may recover from an ordinary tool failure; an approval-policy refusal means
                # labhq wired the server wrong (openai/codex#24135), so that one fails the task loudly.
                if "approval policy" in message:
                    st.error = message
                await ctx.emit("agent.tool_error", {"text": short(message, 400)})
            elif it == "file_change" and typ == "item.completed":
                await ctx.emit("agent.tool", {"name": "edit", "input": short(item.get("changes"), 300)})
            elif it == "web_search" and typ == "item.completed":
                action = item.get("action") or {}
                query = action.get("query") if isinstance(action, dict) else None
                await ctx.emit("agent.tool", {"name": "web_search",
                                               "input": short(query or item.get("query"), 200)})
        elif typ == "turn.completed":
            st.result_seen = True
            from .base import cumulative_usage, record_accounting, token_counts
            total = token_counts(ev.get("usage"), ("input_tokens", "cached_input_tokens",
                "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens"))
            usage = cumulative_usage(total, st, ctx)
            record_accounting(st, ctx)
            await ctx.emit("agent.usage", {"tokens": usage, "usage_known": st.usage_known,
                                           "cost_usd": None, "cost_known": False})
        elif typ in ("turn.failed", "error"):
            err = ev.get("error")
            message = (err.get("message") if isinstance(err, dict) else None) or ev.get("message") or "codex error"
            if _turn_needs_setup(str(message)):
                _stop_for_setup(st, f"{ELEVATED_SETUP_ERROR} Codex: {short(str(message), 200)}")
            elif getattr(st, "error_kind", None) != "sandbox_setup_required":
                st.error = message

    def finalize(self, st: RunState, ctx: RunContext, returncode: int | None):
        # Never through a link the agent put there (#165): the stream's own text stands instead.
        last = read_owned(ctx.workdir, ".labhq/last_message.txt")
        if last and last.strip():
            st.final_text = last
        elif st.ended_by_guard and st.last_message and st.last_message.strip():
            # Codex writes -o only after its shutdown returns; when that shutdown hangs and the exit guard ends the
            # process, the file never appears. Its final answer is the turn's last agent message, not every message
            # joined (#330 review).
            st.final_text = st.last_message
        result = super().finalize(st, ctx, returncode)
        if result.structured is not None and ctx.task.output_schema:
            result = result.model_copy(update={
                "structured": strip_optional_nulls(result.structured, ctx.task.output_schema),
            })
        return result
