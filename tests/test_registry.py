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


def _contract(tmp: Path, expires_in: float) -> AgentSpec:
    now = time.time()
    return AgentSpec(id="c_tissue", name="TISSUE 파견", role="[파견] spatial uncertainty",
                     employment=Employment.contract,
                     contract=ContractInfo(repo="https://github.com/sunericd/TISSUE", hired_at=now,
                                           expires_at=now + expires_in, status="active",
                                           talent_dir=str(tmp / "talent" / "tissue")))


def test_contract_lifecycle(tmp_path):
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


def test_expired_contract_goes_to_talent_pool(tmp_path):
    (tmp_path / "agents" / "core").mkdir(parents=True)
    reg = Registry(tmp_path / "agents", tmp_path / "talent")
    reg.save_contract(_contract(tmp_path, -1))
    reg.load()
    assert "c_tissue" not in reg.agents
    assert reg.talent_pool()[0].contract.status == "archived"
    assert not (tmp_path / "agents" / "contract" / "c_tissue.yaml").exists()
