from labhq.gateway.server import Hub
from labhq.models import AgentSpec
from labhq.settings import Settings


class Socket:
    async def send_text(self, body):
        pass


def test_snapshot_agents_carry_the_capability_card(tmp_path):
    # #57 ⑦: the staff sheet shows resume, read-only enforcement, effort, permission and attached MCP from the
    # runner's roster and the engine adapters, without the web guessing.
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(s)
    claude = AgentSpec(id="cso", name="CSO", role="lead", engine="claude_code", effort="high",
                       permission_mode="acceptEdits", builtin_mcp=["approval"])
    codex = AgentSpec(id="engineer", name="Engineer", role="code", engine="codex", sandbox="read-only")
    plain = AgentSpec(id="tool", name="Tool", role="pipeline", engine="cli", cli={"command": ["tool"]})
    hub.register_runner("runner", Socket(), [agent.summary() for agent in (claude, codex, plain)])

    cards = {agent["id"]: agent["capabilities"] for agent in hub.snapshot()["data"]["agents"]}
    assert cards["cso"] == {"resume": True, "read_only": True, "effort": "high", "permission": "acceptEdits",
                            "max_turns": claude.max_turns, "mcp": ["labhq_approval", "labhq_ask"]}
    assert cards["engineer"]["permission"] == "read-only" and cards["engineer"]["read_only"] is True
    assert cards["tool"]["resume"] is False and cards["tool"]["read_only"] is False
    assert cards["tool"]["permission"] == plain.permission_mode


def test_capability_mcp_lists_what_the_runner_wires(tmp_path):
    # PR #410 review: labhq_hpc only when the runner has a scheduler; labhq_ask joins every engine but Antigravity.
    s = Settings()
    s.gateway.state_dir = str(tmp_path / "state")
    hub = Hub(s)
    analyst = AgentSpec(id="analyst", name="Analyst", role="analysis", engine="claude_code",
                        builtin_mcp=["approval", "hpc"])
    hub.register_runner("plain", Socket(), [analyst.summary()], capabilities={"scheduler": "none"})
    assert hub.agents["analyst"]["capabilities"]["mcp"] == ["labhq_approval", "labhq_ask"]
    hub.register_runner("cluster", Socket(), [analyst.summary()], capabilities={"scheduler": "slurm"})
    assert hub.agents["analyst"]["capabilities"]["mcp"] == ["labhq_approval", "labhq_hpc", "labhq_ask"]
