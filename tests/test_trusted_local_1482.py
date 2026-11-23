"""#1482 trusted_local: the runner answers tool cards itself, allow by default and deny only named reasons."""
from types import SimpleNamespace

import pytest

from labhq.approval_grants import classify, trusted_verdict
from labhq.settings import DataZone, PolicySettings, Settings
from tests.test_private_paths import _link_dir


@pytest.fixture
def layout(tmp_path):
    runs = tmp_path / "runs"
    work = runs / "day" / "task"
    for folder in (work / "outputs" / "old", work / "inputs" / "s1", runs / "day" / "other",
                   tmp_path / "internal", tmp_path / "closed", tmp_path / "home" / ".ssh"):
        folder.mkdir(parents=True)
    _link_dir(work / "link", runs / "day" / "other")
    policy = PolicySettings(data_zones=[DataZone(path=(tmp_path / "internal").as_posix(), level="internal"),
                                        DataZone(path=(tmp_path / "closed").as_posix(), level="restricted")])
    scope = dict(workdir=work.as_posix(), private_paths=(runs.as_posix(), (work / "inputs").as_posix(),
                                                          (tmp_path / "home").as_posix()),
                 private_enabled=True, private_open_reads=(work.as_posix(), (work / "inputs" / "s1").as_posix()),
                 environ={})
    return tmp_path, policy, scope


def _verdict(layout, command, tool="Bash", policy_reason="", workspace_root=""):
    tmp_path, policy, scope = layout
    command = command.replace("{tmp}", tmp_path.as_posix())
    trace = []
    category = classify(tool, {"command": command}, policy, trace=trace, **scope)
    return trusted_verdict(category, trace, tool, {"command": command}, policy, policy_reason=policy_reason,
                           workspace_root=workspace_root.replace("{tmp}", tmp_path.as_posix()), **scope)


@pytest.mark.parametrize("tool, command", [
    ("Bash", "mkdir -p outputs/sub && echo x > outputs/sub/a.txt"),
    ("PowerShell", "New-Item outputs/sub"),
    ("Bash", "python outputs/run.py"),  # an analysis script
    ("Bash", 'python -c "import urllib.request; print(1)" > outputs/log.txt'),  # a network word
    ("Bash", "rm -rf outputs/old"),  # a folder delete inside the task folder
    ("Bash", "curl -L -o outputs/ref.fa.gz https://example.test/ref.fa.gz"),  # a public download
    ("Bash", 'for f in outputs/*.txt; do wc -l "$f"; done > outputs/n.txt'),
])
def test_ordinary_analysis_cards_are_allowed(layout, tool, command):
    assert _verdict(layout, command, tool) == ("allow", "")


@pytest.mark.parametrize("tool, command, reason", [
    ("Bash", "cat {tmp}/home/.ssh/id_rsa > outputs/k", None),  # private path or credential
    ("Bash", "cat ../other/result.tsv > outputs/r.tsv", "개인 경로"),  # another task's folder
    ("Bash", "echo x > inputs/a.txt", "개인 경로"),  # a private item inside the task folder
    ("Bash", "cat {tmp}/closed/a.csv > outputs/a.csv", "제한 구역"),
    ("Bash", "echo x > {tmp}/elsewhere.txt", "허용 폴더 밖 쓰기"),
    ("PowerShell", "Set-Content link/a.txt x", None),  # a link out of the task folder
    ("Bash", "rm -rf {tmp}/elsewhere", "허용 폴더 밖 삭제"),
    ("Bash", "rm -rf link/file", "허용 폴더 밖 삭제"),  # a link in the task folder leads out
    ("Bash", "sudo apt-get install -y samtools", "관리자 권한·위험 명령"),
    ("Bash", "sbatch outputs/job.sh", "스케줄러 직접 호출: HPC 도구(hpc_submit)로 제출하세요"),
])
def test_named_risks_are_denied_with_a_reason(layout, tool, command, reason):
    verdict, why = _verdict(layout, command, tool)
    assert verdict == "deny" and why
    if reason:
        assert why == reason


def test_a_short_disk_download_is_denied_with_the_disk_reason(layout):
    # The runner's own policy allows it: the approval gate asked only because of the disk check.
    verdict, why = _verdict(layout, "pwd", policy_reason="다운로드 대상 드라이브 여유가 2.0 GiB")
    assert verdict == "deny" and why.startswith("디스크 여유 부족") and "2.0 GiB" in why


def test_internal_zone_reads_keep_their_once_per_request_card(layout):
    assert _verdict(layout, "cat {tmp}/internal/a.csv")[0] == "card"
    assert _verdict(layout, 'python -c "import urllib" {tmp}/internal/a.csv')[0] == "card"


def _runner(tmp_path, trusted):
    from labhq.models import Engine
    from labhq.runner.daemon import Runner

    runner = Runner.__new__(Runner)
    runner.s = Settings()
    runner.s.policy.approvals.trusted_local = trusted
    runner.process = "process-1"
    agent = SimpleNamespace(engine=Engine.claude_code, name="test", permission_mode="default")
    runner.workspaces = {"t1": SimpleNamespace(dir=tmp_path, task=SimpleNamespace(meta={}, agent_id="agent"),
                                               agent=agent)}
    runner.task_req, runner.active_roots = {"t1": "r1"}, {"t1": (False, [tmp_path])}
    runner.tool_contexts = {"t1": SimpleNamespace(
        agent=agent, extra_dirs=[], private_paths=[], private_enabled=False, private_open_reads=[], read_only=False,
        shared_environment_protected=False, environment_step=False, env={}, _approval_grant_env={})}
    runner.approval_tasks = {}
    resolved, sent, logged = [], [], []
    runner.broker = SimpleNamespace(resolve=lambda aid, ok, note="": resolved.append((aid, ok, note)) or True)

    async def emit_all(events):
        sent.extend(events)

    async def emit(event):
        logged.append(event)
    runner.emit_all, runner.emit = emit_all, emit
    return runner, resolved, sent, logged


@pytest.mark.parametrize("trusted", [False, True])
async def test_runner_answers_cards_only_when_trusted_local_is_on(tmp_path, trusted):
    from labhq.models import ApprovalRequest

    runner, resolved, sent, logged = _runner(tmp_path, trusted)
    for command in ("rm -rf old", "sudo rm -rf /"):
        req = ApprovalRequest(kind="tool_permission", summary="s", task_id="t1", request_id="r1", agent_id="agent",
                              detail={"tool_name": "Bash", "tool_input": {"command": command}, "policy_reason": "x"})
        await runner._on_approval(req)
    if not trusted:
        assert resolved == [] and [e.type for e in sent].count("approval.requested") == 2
        return
    assert [ok for _, ok, _ in resolved] == [True, False] and sent == []
    assert "trusted_local" in resolved[1][2] and [e.type for e in logged] == ["agent.log"] * 2


@pytest.mark.parametrize("command, verdict", [
    # The runs root and inputs/ are registered private, but they hold labhq's own task folders.
    ("ls inputs; cat inputs/s1/a.tsv > outputs/a.tsv", "allow"),
    ("cd inputs && find s1 -maxdepth 2 && wc -l s1/a.tsv", "allow"),
    ("cat ../other/result.tsv > outputs/r.tsv", "allow"),
    # The PI's own files and writes out of the folders stay denied.
    ("cat {tmp}/home/.ssh/id_rsa > outputs/k", "deny"),
    ("echo x > {tmp}/elsewhere.txt", "deny"),
    ("cd ../.. && echo x > .tmp/a.txt", "deny"),
])
def test_workspace_root_task_folders_are_not_personal_paths(layout, command, verdict):
    assert _verdict(layout, command, workspace_root="{tmp}/runs")[0] == verdict


@pytest.mark.parametrize("command", [
    'python -c "for d in [1]: print(d)" > outputs/a.txt',  # a `d:` word in code
    "python -I -c \"t='token'; print(t)\" > outputs/a.txt",  # a credential-like word, not a path
])
def test_loose_words_in_inline_code_are_not_path_evidence(layout, command):
    assert _verdict(layout, command, workspace_root="{tmp}/runs") == ("allow", "")
