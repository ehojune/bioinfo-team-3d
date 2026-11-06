"""Synthetic runner capability drift regression (#1037)."""

from types import SimpleNamespace

from labhq.models import AgentSpec, Engine
from labhq.runner.daemon import Runner
from labhq.settings import Settings, WikiSettings


def test_roster_off_cannot_later_attach_wiki_without_new_bound_dispatch(tmp_path, monkeypatch):
    runner = object.__new__(Runner)
    runner.s = Settings()
    runner.s.wiki = WikiSettings(enabled=False, root=str(tmp_path), external_model_allowed=True)
    agent = AgentSpec(id="worker", name="worker", role="test", engine=Engine.claude_code, builtin_mcp=["wiki"])
    runner.registry = SimpleNamespace(agents={agent.id: agent})
    runner.ws_root = tmp_path
    monkeypatch.setattr("labhq.runner.daemon.adapter_preflight_error", lambda *args: None)
    assert runner.roster()[0]["wiki_tools"] is False

    # A stale roster must not authorize a different tool set on the eventual dispatch.
    runner.s.wiki.enabled = True
    servers = runner._mcp_servers(agent, {}, allow_ask=False)
    assert "labhq_wiki" not in [server.name for server in servers]
