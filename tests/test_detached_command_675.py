"""#675: a command that detaches a process from its step is refused without a card, and the answer names the job tool."""
import pytest

from labhq.detached_command import DENIAL, detached
from labhq.policy import evaluate_tool
from labhq.settings import Settings


@pytest.mark.parametrize("tool, command", [
    ("Bash", "nohup python gsea.py > gsea.log 2>&1 &"),
    ("Bash", "python gsea.py &"),
    ("Bash", "nohup python gsea.py"),
    ("Bash", "setsid ./run.sh"),
    ("Bash", "sleep 3; disown"),
    ("Bash", "python a.py & python b.py"),
    ("PowerShell", "Start-Process python -ArgumentList gsea.py"),
    ("PowerShell", "Start-Job { python gsea.py }"),
    ("Bash", "sh -c 'sleep 60 &'"),
    ("Bash", "bash -lc \"nohup python gsea.py > log 2>&1 &\""),
    ("PowerShell", "powershell -Command \"Start-Process python gsea.py\""),
])
def test_commands_that_detach_a_process_are_recognised(tool, command):
    assert detached(tool, command)


@pytest.mark.parametrize("tool, command", [
    ("Bash", "python gsea.py > out.log 2>&1"),
    ("Bash", "python a.py && python b.py"),
    ("Bash", "make -j4 2>&1 | tail -20"),
    ("Bash", "cat nohup.out"),
    ("Bash", "echo 'a & b'"),
    ("Bash", "python - <<'EOF'\nx = a & b\nEOF"),
    ("Bash", "cmd1 |& cmd2"),
    ("Bash", "sh -c 'echo done'"),
    ("Bash", "bash -c \"python gsea.py && echo ok\""),
    ("PowerShell", "Start-Process python -Wait -ArgumentList gsea.py"),
    ("PowerShell", "python gsea.py 2>&1 | Out-File out.txt"),
    ("Read", "nohup python gsea.py &"),
])
def test_ordinary_commands_are_not_mistaken_for_detaching(tool, command):
    assert not detached(tool, command)


@pytest.mark.parametrize("tool, command", [("Bash", "nohup python gsea.py > log 2>&1 &"),
                                           ("PowerShell", "Start-Process python gsea.py")])
def test_the_approval_gate_refuses_without_a_card_and_names_the_job_tool(tmp_path, tool, command):
    decision = evaluate_tool(tool, {"command": command}, Settings().policy, allowed_roots=[str(tmp_path)],
                             workdir=str(tmp_path))
    assert decision.action == "deny"  # a denial is answered at once: no PI card
    assert decision.reason == DENIAL and "labhq_job" in decision.reason and "job_submit" in decision.reason


def test_an_ordinary_command_still_reaches_the_gate_unchanged(tmp_path):
    decision = evaluate_tool("Bash", {"command": "python gsea.py > out.log 2>&1"}, Settings().policy,
                             allowed_roots=[str(tmp_path)], workdir=str(tmp_path))
    assert decision.action != "deny" or decision.reason != DENIAL
