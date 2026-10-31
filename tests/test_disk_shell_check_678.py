"""A shell download or unpack asks the PI when its target drive is short on space (#678)."""
import json
import zipfile
from collections import namedtuple
from types import SimpleNamespace

import pytest

from labhq import disk_space
from labhq.disk_space import GIB, shell_check

Space = namedtuple("Space", "total used free")
CHECK = dict(fail_gb=2, warn_gb=10, warn_percent=5)


def drive(free_gib, total_gib=100):
    return lambda folder: Space(int(total_gib * GIB), int((total_gib - free_gib) * GIB), int(free_gib * GIB))


def check(command, workdir, free_gib, **kw):
    return shell_check(command, str(workdir), probe=drive(free_gib), **{**CHECK, **kw})


def test_a_download_below_the_floor_asks(tmp_path):
    verdict = check("curl -L -o raw.zip https://example.org/a.zip", tmp_path, 1.0)
    assert verdict.action == "ask" and "1.0 GiB" in verdict.reason


def test_a_download_of_unknown_size_on_a_comfortable_drive_is_left_alone(tmp_path):
    assert check("wget https://example.org/a.tar.gz", tmp_path, 50) is None


def test_an_unknown_size_on_a_low_drive_only_warns(tmp_path):
    verdict = check("curl -O https://example.org/a.zip", tmp_path, 6)
    assert verdict.action == "warn"  # below the 10 GiB warning level, above the 2 GiB floor


def test_a_zip_that_will_not_fit_asks_with_its_real_size(tmp_path):
    with zipfile.ZipFile(tmp_path / "raw.zip", "w") as z:
        z.writestr("big.bin", b"\0" * (3 * 1024 * 1024))
    sliver = shell_check("unzip raw.zip", str(tmp_path), probe=lambda f: Space(10 * GIB, 0, int(2 * GIB + 1024)),
                         **CHECK)  # 3 MiB listed, 1 KiB over the 2 GiB floor: it does not fit
    assert sliver.action == "ask" and "필요" in sliver.reason
    assert check("unzip raw.zip", tmp_path, 50) is None  # plenty of room


def test_a_tar_is_guessed_from_its_packed_size(tmp_path, monkeypatch):
    (tmp_path / "a.tgz").write_bytes(b"x")
    monkeypatch.setattr(disk_space, "ARCHIVE_RATIO", float(3 * GIB))  # 1 byte packed -> 3 GiB unpacked
    assert check("tar xzf a.tgz -C out", tmp_path, 4).action == "ask"  # 4 - 3 < 2
    assert check("tar xzf a.tgz -C out", tmp_path, 8) is None


def test_the_target_folder_decides_the_drive(tmp_path):
    seen = []

    def probe(folder):
        seen.append(folder.as_posix())
        return Space(100 * GIB, 0, 50 * GIB)

    shell_check("tar -xf a.tar -C D:/data/out", str(tmp_path), probe=probe, **CHECK)
    shell_check("unzip a.zip -d sub", str(tmp_path), probe=probe, **CHECK)
    assert seen[0] == "D:/data/out" and seen[1].endswith("/sub")


@pytest.mark.parametrize("command", ["ls -la", "cat notes.txt", "python run.py", "tar tf a.tar",
                                     "grep curl notes.txt", "echo 'unterminated"])
def test_other_commands_never_ask(tmp_path, command):
    assert check(command, tmp_path, 0.1) is None


def test_a_chain_is_read_command_by_command_and_the_worst_wins(tmp_path):
    verdict = check("echo hi && curl -O https://example.org/a.zip; ls", tmp_path, 1.0)
    assert verdict.action == "ask"


def test_an_unreadable_drive_never_asks(tmp_path):
    def broken(folder):
        raise OSError("no such drive")

    assert shell_check("curl -O https://example.org/a", str(tmp_path), probe=broken, **CHECK) is None


@pytest.fixture
def gate(tmp_path, monkeypatch):
    from labhq.settings import Settings
    from labhq.tools import approval_mcp as mcp
    monkeypatch.setattr(mcp, "S", Settings())
    monkeypatch.setattr(mcp, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(mcp, "WORKDIR_INPUT", str(tmp_path))
    monkeypatch.setattr(mcp, "EXTRA_ROOTS", [])
    monkeypatch.setattr(mcp, "READ_ONLY", False)
    monkeypatch.setattr(mcp, "TOKEN", "fixture-only")
    monkeypatch.setattr(mcp, "PRIVATE", SimpleNamespace(paths=[], enabled=False, open_reads=[]))
    sent = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

        async def post(self, url, **kwargs):
            sent.append((url, kwargs["json"]))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"approved": False, "note": "no"})

    monkeypatch.setattr(mcp.httpx, "AsyncClient", Client)
    return mcp, sent


@pytest.mark.asyncio
async def test_the_gate_turns_an_allowed_download_on_a_full_drive_into_a_card(gate, monkeypatch):
    mcp, sent = gate
    monkeypatch.setattr(disk_space, "usage", drive(0.5))
    result = json.loads(await mcp.approval_prompt(
        "Bash", {"command": "curl -L -o outputs/raw.zip https://example.org/a.zip"}))
    assert result["behavior"] == "deny"  # the fake PI declined the card
    assert len(sent) == 1 and sent[0][0].endswith("/approval")
    assert "여유" in sent[0][1]["summary"]


@pytest.mark.asyncio
async def test_the_gate_leaves_a_download_alone_when_there_is_room(gate, monkeypatch):
    mcp, sent = gate
    monkeypatch.setattr(disk_space, "usage", drive(500, 1000))
    result = json.loads(await mcp.approval_prompt(
        "Bash", {"command": "curl -L -o outputs/raw.zip https://example.org/a.zip"}))
    assert result["behavior"] == "allow" and sent == []


@pytest.mark.asyncio
async def test_a_low_drive_with_unknown_size_logs_a_warning_and_allows(gate, monkeypatch):
    mcp, sent = gate
    monkeypatch.setattr(disk_space, "usage", drive(6))
    result = json.loads(await mcp.approval_prompt("Bash", {"command": "wget https://example.org/a.tgz"}))
    assert result["behavior"] == "allow"
    assert len(sent) == 1 and sent[0][0].endswith("/event") and sent[0][1]["data"]["level"] == "warn"
