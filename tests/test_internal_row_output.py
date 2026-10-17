"""#706: approval cards distinguish raw rows from safe metadata and aggregates."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from labhq.models import AgentSpec, ApprovalRequest, Task
from labhq.runner.daemon import Runner
from labhq.settings import DataZone, Settings
from labhq.tool_explanation import internal_row_output
from labhq.policy import claude_settings, evaluate_tool


def _policy(tmp_path: Path):
    settings = Settings()
    internal = tmp_path / "internal"
    public = tmp_path / "public"
    internal.mkdir()
    public.mkdir()
    settings.policy.data_zones = [
        DataZone(path=str(internal), level="internal"),
        DataZone(path=str(public), level="public"),
    ]
    return settings, internal, public


@pytest.mark.parametrize("template", [
    'head -5 "{path}"',
    'cat "{path}"',
    'tail -3 "{path}"',
    "sed -n '2,5p' \"{path}\"",
    "awk 'NR > 1 {{print $0}}' \"{path}\"",
    'python -c "import pandas as pd; print(pd.read_csv(r\'{path}\').head())"',
    'Rscript -e "print(head(read.csv(\'{path}\')))"',
])
def test_internal_row_commands_are_marked(tmp_path, template):
    settings, internal, _ = _policy(tmp_path)
    command = template.format(path=internal / "people.tsv")
    assert internal_row_output("Bash", {"command": command}, settings.policy, str(tmp_path))


@pytest.mark.parametrize("command", [
    'head -1 "{path}"',
    'wc -l "{path}"',
    'python -c "import pandas as pd; print(pd.read_csv(r\'{path}\').columns)"',
    'python -c "import pandas as pd; print(pd.read_csv(r\'{path}\', nrows=0).columns)"',
    'python -c "import pandas as pd; print(pd.read_csv(r\'{path}\').groupby(\'sex\').size())"',
    'Rscript -e "print(names(read.csv(\'{path}\', nrows=0)))"',
    "awk '{{sum += $2}} END {{print sum}}' \"{path}\"",
])
def test_internal_headers_counts_and_aggregates_are_not_marked(tmp_path, command):
    settings, internal, _ = _policy(tmp_path)
    assert not internal_row_output(
        "Bash", {"command": command.format(path=internal / "people.tsv")}, settings.policy, str(tmp_path)
    )


def test_public_rows_are_not_marked(tmp_path):
    settings, _, public = _policy(tmp_path)
    assert not internal_row_output(
        "Bash", {"command": f'head -5 "{public / "people.tsv"}"'}, settings.policy, str(tmp_path)
    )


def test_relative_path_is_resolved_from_internal_workdir(tmp_path):
    settings, internal, _ = _policy(tmp_path)
    assert internal_row_output("Bash", {"command": "head -5 people.tsv"}, settings.policy, str(internal))


def test_policy_asks_for_internal_rows_but_not_header(tmp_path):
    settings, internal, _ = _policy(tmp_path)
    rows = evaluate_tool(
        "Bash", {"command": "head -5 people.tsv"}, settings.policy, workdir=str(internal)
    )
    header = evaluate_tool(
        "Bash", {"command": "head -1 people.tsv"}, settings.policy, workdir=str(internal)
    )
    assert rows.action == "ask" and "internal data row output" in rows.reason
    assert header.action != "ask"


def test_claude_internal_file_reads_reach_the_gate(tmp_path):
    settings, internal, _ = _policy(tmp_path)
    asks = claude_settings(settings.policy)["permissions"]["ask"]
    assert any(rule.startswith("Read(") and internal.name in rule for rule in asks)


@pytest.mark.parametrize("command,marked", [
    ('Get-Content -TotalCount 5 "{path}"', True),
    ('Get-Content -TotalCount 1 "{path}"', False),
    ('Get-Content "{path}" | Select-Object -First 5', True),
])
def test_powershell_row_output(tmp_path, command, marked):
    settings, internal, _ = _policy(tmp_path)
    assert internal_row_output(
        "PowerShell", {"command": command.format(path=internal / "people.tsv")},
        settings.policy, str(tmp_path),
    ) is marked


@pytest.mark.asyncio
async def test_runner_card_marks_rows_and_recommends_rejection(tmp_path):
    settings, internal, _ = _policy(tmp_path)
    task = Task(id="step", agent_id="analyst", prompt="analyse", meta={"title": "내부 자료 분석"})
    agent = AgentSpec(id="analyst", name="분석가", role="analysis")
    workdir = tmp_path / "work"
    workdir.mkdir()
    events = []

    async def emit_all(items):
        events.extend(items)

    runner = SimpleNamespace(
        s=settings,
        task_req={"step": "req"},
        approval_tasks={},
        emit_all=emit_all,
        workspaces={"step": SimpleNamespace(task=task, agent=agent, dir=workdir)},
    )
    req = ApprovalRequest(
        task_id="step",
        agent_id="analyst",
        kind="tool_permission",
        summary="approval",
        detail={"tool_name": "Bash", "tool_input": {"command": f'head -5 "{internal / "people.tsv"}"'}},
    )

    await Runner._on_approval(runner, req)

    card = events[0].data
    assert card["detail"]["internal_row_output"] is True
    assert "내부 자료 행 출력" in card["pi_summary"]
    assert "거절" in card["pi_reason"]
