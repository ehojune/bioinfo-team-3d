import time
from pathlib import Path

from labhq.models import AgentSpec, ContractInfo, Employment
from labhq.registry import Registry

REPO = Path(__file__).resolve().parents[1]


def test_core_agents_load():
    reg = Registry(REPO / "agents", REPO / ".talent-test")
    reg.load()
    ids = set(reg.agents)
    assert {"cso", "chief_of_staff", "sci_reviewer", "recruiter", "analyst", "qc_reviewer"} <= ids
    assert reg.get("cso").can_orchestrate
    lit = reg.get("lit_scout")
    assert lit.model == "gpt-6-luna"
    assert {(m.name, m.type, m.url) for m in lit.mcp} == {
        ("pubmed", "http", "https://pubmed.mcp.claude.com/mcp"),
        ("biorxiv", "http", "https://hcls.mcp.claude.com/biorxiv/mcp"),
    }
    assert reg.get("engineer").model == "gpt-6.1-sol" and not reg.get("engineer").tools
    assert reg.get("sci_reviewer").model == "gpt-6-astra"
    assert reg.get("sci_reviewer").tools == ["WebSearch"]
    assert "original source" in lit.system_prompt
    assert "original source" in reg.get("sci_reviewer").system_prompt


def _contract(tmp: Path, expires_in: float) -> AgentSpec:
    now = time.time()
    return AgentSpec(id="c_tissue", name="TISSUE 파견", role="[파견] spatial uncertainty",
                     employment=Employment.contract,
                     contract=ContractInfo(repo="https://github.com/sunericd/TISSUE", hired_at=now,
                                           expires_at=now + expires_in, status="active",
                                           talent_dir=str(tmp / "talent" / "tissue")))


def test_contract_lifecycle(tmp_path, monkeypatch):
    import labhq.registry as registry_module

    writes = []
    original = registry_module.atomic_write_text

    def record(path, text):
        writes.append(Path(path))
        original(path, text)

    monkeypatch.setattr(registry_module, "atomic_write_text", record)
    (tmp_path / "agents" / "core").mkdir(parents=True)
    reg = Registry(tmp_path / "agents", tmp_path / "talent")
    reg.save_contract(_contract(tmp_path, 3600))
    reg.load()
    assert "c_tissue" in reg.agents
    reg.release("c_tissue")
    reg.load()
    assert "c_tissue" not in reg.agents
    assert [s.id for s in reg.talent_pool()] == ["c_tissue"]
    spec = reg.rehire("tissue", days=7)
    reg.load()
    assert "c_tissue" in reg.agents and spec.contract.expires_at > time.time() + 6 * 86400
    assert tmp_path / "agents" / "contract" / "c_tissue.yaml" in writes
    assert tmp_path / "talent" / "tissue" / "contract.yaml" in writes


def test_expired_contract_goes_to_talent_pool(tmp_path):
    (tmp_path / "agents" / "core").mkdir(parents=True)
    reg = Registry(tmp_path / "agents", tmp_path / "talent")
    reg.save_contract(_contract(tmp_path, -1))
    reg.load()
    assert "c_tissue" not in reg.agents
    assert reg.talent_pool()[0].contract.status == "archived"
    assert not (tmp_path / "agents" / "contract" / "c_tissue.yaml").exists()
