import pytest

from labhq.models import ApprovalRequest
from labhq.policy import evaluate_tool
from labhq.settings import PolicySettings


@pytest.mark.parametrize('kind', ['tool_permission', 'hpc_submit', 'budget', 'recruit'])
def test_approval_has_korean_explanation_without_changing_source(kind):
    card = ApprovalRequest(kind=kind, summary='**Approval required**', detail={'input': 'raw'})
    data = card.model_dump()
    assert data['summary'] == '**Approval required**'
    for key in ('pi_summary', 'pi_reason', 'pi_outcome'):
        assert any('가' <= c <= '힣' for c in data[key])
        assert '**' not in data[key] and '`' not in data[key]


def test_tool_explanation_names_literal_action_and_path():
    card = ApprovalRequest(kind='tool_permission', summary='PowerShell: approval', detail={
        'tool_name': 'PowerShell', 'tool_input': {'command': 'New-Item -ItemType Directory -Force .tmp'}})
    assert '.tmp' in card.pi_summary and '폴더' in card.pi_summary
    assert '만들' in card.pi_summary
    assert '밖은 건드리지' not in card.pi_summary  # no invented safety assurance


def test_pi_rules_reach_staff_and_cso():
    from labhq.adapters.base import ROLE_FOOTER
    from labhq.orchestrator.cso import PI_CARD_QUESTION_RULE
    for rule in (ROLE_FOOTER, PI_CARD_QUESTION_RULE):
        assert '쉬운 한국어 평문' in rule
        assert '무엇을 하려는지 → 왜 → 승인하면/끝나면 무슨 결과' in rule


def test_powershell_here_string_is_data_not_recursive_delete(tmp_path):
    command = "New-Item -ItemType Directory -Force .tmp | Out-Null\n@'\nRemove-Item results -Recurse\n'@ | Set-Content .tmp/example.txt"
    result = evaluate_tool('PowerShell', {'command': command}, PolicySettings(),
                           workdir=str(tmp_path), allowed_roots=[str(tmp_path)])
    assert result.action == 'allow', result.reason


@pytest.mark.parametrize('tail', ['; Remove-Item results -Recurse', ' | Invoke-Expression'])
def test_real_or_executable_here_string_delete_still_asks(tmp_path, tail):
    command = "@'\nRemove-Item results -Recurse\n'@" + tail
    assert evaluate_tool('PowerShell', {'command': command}, PolicySettings(),
                         workdir=str(tmp_path), allowed_roots=[str(tmp_path)]).action == 'ask'


def test_new_item_force_alone_is_not_delete(tmp_path):
    result = evaluate_tool('PowerShell', {'command': 'New-Item -ItemType Directory -Force .tmp'},
                           PolicySettings(), workdir=str(tmp_path), allowed_roots=[str(tmp_path)])
    assert result.action == 'allow'


@pytest.mark.asyncio
async def test_gateway_persists_and_publishes_the_same_explanation(tmp_path):
    from labhq.gateway.server import Hub
    from labhq.settings import Settings
    cfg = Settings()
    cfg.gateway.state_dir = str(tmp_path / 'state')
    hub = Hub(cfg)
    await hub.on_runner_message('runner', {'type': 'approval.requested', 'data': {
        'id': 'a', 'kind': 'budget', 'summary': 'Budget exceeded',
        'detail': {'spent_usd': 12, 'limit_usd': 10, 'requested_budget_usd': 20}}})
    stored = hub.store.all('approval')['a']['approval']
    event = next(e for e in hub.events if e['type'] == 'approval.requested')['data']
    assert stored['pi_summary'] == event['pi_summary']
    assert '$12.00' in stored['pi_reason'] and '$20.00' in stored['pi_outcome']
    assert stored['summary'] == 'Budget exceeded'
