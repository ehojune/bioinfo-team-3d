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


def folders(command, workdir):
    seen = []

    def probe(folder):
        seen.append(folder.as_posix())
        return Space(100 * GIB, 0, 50 * GIB)

    shell_check(command, str(workdir), probe=probe, **CHECK)
    return seen


def test_the_target_folder_decides_the_drive(tmp_path):
    other = (tmp_path / "elsewhere").as_posix()
    assert other in folders(f"tar -xf a.tar -C {other}", tmp_path)
    assert folders("unzip a.zip -d sub", tmp_path)[0].endswith("/sub")
    assert folders("curl -o dl/a.zip https://example.org/a.zip", tmp_path)[0].endswith("/dl")


@pytest.mark.parametrize("command", [
    "tar xzf a.tgz -C {out}", "tar -xzf a.tgz -C {out}", "tar -C {out} -xf a.tgz",
    "tar --extract --file a.tgz -C {out}", "tar --extract --file=a.tgz --directory={out}", "tar --get -f a.tgz -C {out}"])
def test_every_tar_extract_spelling_is_read(tmp_path, command):
    out = (tmp_path / "out").as_posix()
    (tmp_path / "a.tgz").write_bytes(b"x")
    assert out in folders(command.format(out=out), tmp_path)


def test_tar_listing_and_creating_are_not_extraction(tmp_path):
    assert folders("tar tf a.tar", tmp_path) == []
    assert folders("tar czf out.tgz data/", tmp_path) == []


def test_git_clone_probes_its_destination(tmp_path):
    other = (tmp_path / "bigdisk" / "repo").as_posix()
    assert other in folders(f"git clone https://example.org/r.git {other}", tmp_path)
    assert other in folders(f"git clone --depth 1 -b main https://example.org/r.git {other}", tmp_path)
    assert tmp_path.as_posix() in folders("git clone https://example.org/r.git", tmp_path)


def test_bulk_tools_probe_their_named_output(tmp_path):
    out = (tmp_path / "sra").as_posix()
    assert out in folders(f"fasterq-dump SRR1 -O {out}", tmp_path)
    assert out in folders(f"aws s3 sync s3://bucket/x {out}", tmp_path)
    assert tmp_path.as_posix() in folders("aws s3 cp local.txt s3://bucket/x", tmp_path)
    assert out in folders(f"rsync -a host:/data {out}", tmp_path)


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


def _allowed_tools(tmp_path, tools, *, gate=True):
    from labhq.adapters import get_adapter
    from labhq.adapters.base import RunContext
    from labhq.models import AgentSpec, Engine, McpServerSpec, Task
    from labhq.settings import Settings
    agent = AgentSpec(id="analyst", name="A", role="test", engine=Engine.claude_code, tools=list(tools),
                      builtin_mcp=["approval"])
    (tmp_path / "ws" / ".labhq").mkdir(parents=True, exist_ok=True)
    servers = [McpServerSpec(name="labhq_approval", command="python", args=["-m", "x"])] if gate else []
    ctx = RunContext(task=Task(agent_id=agent.id, prompt="x"), agent=agent, workdir=tmp_path / "ws",
                     settings=Settings(), mcp_servers=servers, env={}, emit=None, prompt="x", extra_dirs=[],
                     claude_settings={}, use_permission_tool=gate)
    cmd = get_adapter(agent.engine, Settings()).build_command(ctx)
    i = cmd.index("--allowedTools")
    end = cmd.index("--disallowedTools") if "--disallowedTools" in cmd else len(cmd)
    return cmd[i + 1:end]


RULES = ["Read", "Bash(git clone *)", "Bash(unzip *)", "Bash(curl:*)", "Bash(tar xzf *)", "Bash(uv *)", "Bash(ls *)"]


def test_preapproved_download_and_unpack_rules_are_left_for_the_gate(tmp_path):
    allowed = _allowed_tools(tmp_path, RULES)
    assert not {"Bash(git clone *)", "Bash(unzip *)", "Bash(curl:*)", "Bash(tar xzf *)"} & set(allowed)
    assert {"Read", "Bash(uv *)", "Bash(ls *)"} <= set(allowed)  # other rules are untouched


def test_without_the_gate_the_rules_stay_because_nothing_else_would_run_the_command(tmp_path):
    assert set(RULES) <= set(_allowed_tools(tmp_path, RULES, gate=False))


@pytest.mark.parametrize("rule,expected", [
    ("Bash(git clone *)", True), ("Bash(git status)", False), ("Bash(git *)", False), ("Bash(wget *)", True),
    ("Bash(rsync -a *)", True), ("Bash(python *)", False), ("Bash", False), ("Read", False), ("Bash()", False)])
def test_which_rules_preapprove_a_bulk_write(rule, expected):
    assert disk_space.preapproves_bulk_write(rule) is expected


def test_curl_output_dir_decides_the_drive(tmp_path):
    other = (tmp_path / "big").as_posix()
    assert other in folders(f"curl --output-dir {other} -O https://example.org/a.zip", tmp_path)
    assert other in folders(f"curl --output-dir {other} -o a.zip https://example.org/a.zip", tmp_path)


def test_attached_tar_option_values_are_read(tmp_path):
    out = (tmp_path / "out").as_posix()
    (tmp_path / "a.tgz").write_bytes(b"x")
    arch = (tmp_path / "a.tgz").as_posix()
    assert out in folders(f"tar -C{out} -xf{arch}", tmp_path)


def test_git_clone_skips_the_value_of_options_it_does_not_know(tmp_path):
    other = (tmp_path / "dest").as_posix()
    assert other in folders(f"git clone --reference-if-able /cache https://example.org/r.git {other}", tmp_path)
    assert other in folders(f"git clone --depth=1 --single-branch https://example.org/r.git {other}", tmp_path)


def test_every_output_of_one_curl_call_is_probed(tmp_path):
    big = (tmp_path / "big").as_posix()
    seen = folders(f"curl -o work/a https://example.org/1 -o {big}/b https://example.org/2", tmp_path)
    assert any(f.startswith(big) for f in seen) and any("/work" in f for f in seen)


def test_a_leading_cd_moves_where_the_next_command_runs(tmp_path):
    big = (tmp_path / "big").as_posix()
    assert tmp_path.as_posix() + "/big" in folders(f"cd {big} && curl -O https://example.org/a.zip", tmp_path)[0]
    assert folders(f"cd {big} && curl -O https://example.org/a.zip", tmp_path)[0].startswith(big)


def test_git_clone_switches_and_double_dash_do_not_hide_the_destination(tmp_path):
    other = (tmp_path / "dest").as_posix()
    assert other in folders(f"git clone --checkout https://example.org/r.git {other}", tmp_path)
    assert other in folders(f"git clone -- https://example.org/r.git {other}", tmp_path)


def test_unzip_skips_option_values_and_ignores_listing_modes(tmp_path):
    with zipfile.ZipFile(tmp_path / "raw.zip", "w") as z:
        z.writestr("big.bin", b"\0" * 1024)
    tight = lambda f: Space(10 * GIB, 0, int(2 * GIB + 10))  # 1 KiB listed leaves only 10 bytes over the floor
    asked = shell_check("unzip -P secret raw.zip -d out", str(tmp_path), probe=tight, **CHECK)
    assert asked is not None and asked.action == "ask"
    for listing in ("unzip -l raw.zip", "unzip -t raw.zip", "unzip -p raw.zip"):
        assert shell_check(listing, str(tmp_path), probe=drive(0.5), **CHECK) is None


def test_a_quoted_separator_does_not_split_a_command(tmp_path):
    verdict = check("curl -o out.zip 'https://example.org/a;v=1'", tmp_path, 1.0)
    assert verdict is not None and verdict.action == "ask"
    assert check("echo 'curl -O x; ls", tmp_path, 1.0) is None  # unclosed quote: not read


def test_a_hash_inside_a_word_is_not_a_comment(tmp_path):
    big = (tmp_path / "big").as_posix()
    assert any(f.startswith(big) for f in folders(f"curl https://example.org/file#frag -o {big}/a", tmp_path))


def test_a_comment_starts_only_at_a_word_start(tmp_path):
    big = (tmp_path / "big").as_posix()
    # an apostrophe in a real comment must not swallow the command that follows or poison the quotes
    verdict = check("# it's the nightly fetch\ncurl -O https://example.org/a.zip", tmp_path, 1.0)
    assert verdict is not None and verdict.action == "ask"
    assert any(f.startswith(big) for f in folders(f"curl https://example.org/file#frag -o {big}/a", tmp_path))
    assert folders("curl -O https://example.org/a.zip # don't forget", tmp_path)  # a trailing comment is dropped


def test_a_real_comment_hides_the_command_text_after_it(tmp_path):
    assert check("echo hi # curl -O https://example.org/a.zip", tmp_path, 0.1) is None


def test_redirections_do_not_split_a_command_and_their_target_is_probed(tmp_path):
    big = (tmp_path / "big").as_posix()
    assert any(f.startswith(big) for f in folders(f"curl -s https://example.org/a > {big}/a.json", tmp_path))
    assert any(f.startswith(big) for f in folders(f"curl -s https://example.org/a >> {big}/log 2>&1", tmp_path))
    assert any(f.startswith(big) for f in folders(f"curl -s https://example.org/a &> {big}/all", tmp_path))
    assert check("curl -O https://example.org/a.zip 2>/dev/null", tmp_path, 1.0).action == "ask"
    assert check("tar xzf a.tgz < /dev/null", tmp_path, 50) is None


def test_unzip_patterns_after_x_do_not_hide_the_destination(tmp_path):
    big = (tmp_path / "big").as_posix()
    assert any(f.startswith(big) for f in folders(f"unzip raw.zip -x '*.tmp' 'junk/*' -d {big}", tmp_path))
    assert any(f.startswith(big) for f in folders(f"unzip -x '*.tmp' -d {big} raw.zip", tmp_path))


def test_curl_short_option_clusters_name_their_output(tmp_path):
    big = (tmp_path / "big").as_posix()
    assert any(f.startswith(big) for f in folders(f"curl -Lo {big}/a.zip https://example.org/a.zip", tmp_path))
    assert any(f.startswith(big) for f in folders(f"curl -fsSLo {big}/a.zip https://example.org/a.zip", tmp_path))


def test_descriptor_duplication_names_no_file(tmp_path):
    assert folders("curl -s https://example.org/a 2>&1", tmp_path) == [tmp_path.as_posix()]
    assert folders("curl -s https://example.org/a >&2", tmp_path) == [tmp_path.as_posix()]


def test_a_backslash_newline_continues_the_line(tmp_path):
    big = (tmp_path / "big").as_posix()
    cont = chr(92) + chr(10)  # a backslash at the end of a line
    assert any(f.startswith(big) for f in folders(f"tar -xf a.tar -C {cont}{big}", tmp_path))
    command = f"curl -sSL {cont}  -o {big}/a.zip {cont}  https://example.org/a.zip"
    assert any(f.startswith(big) for f in folders(command, tmp_path))


def test_descriptor_move_names_no_file(tmp_path):
    assert folders("curl -s https://example.org/a 1>&3-", tmp_path) == [tmp_path.as_posix()]


# ----- the PowerShell tool (#1167) -----

def ps_check(command, workdir, free_gib, **kw):
    return disk_space.powershell_check(command, str(workdir), probe=drive(free_gib), **{**CHECK, **kw})


@pytest.mark.parametrize("command,words", [
    (r"Invoke-WebRequest https://x.org/a.zip -OutFile C:\data\a.zip", ["Invoke-WebRequest", "https://x.org/a.zip", "-OutFile", r"C:\data\a.zip"]),
    ("iwr 'https://x.org/a b.zip' -o \"D:\\my data\\a.zip\"", ["iwr", "https://x.org/a b.zip", "-o", "D:\\my data\\a.zip"]),
    ("Expand-Archive a.zip -DestinationPath out; Get-ChildItem", None),
    ("iwr u -OutFile a.zip # -OutFile ignored.zip", ["iwr", "u", "-OutFile", "a.zip"]),
    ("iwr 'it''s.zip' -OutFile `\"x`\".zip", ["iwr", "it's.zip", "-OutFile", '"x".zip']),
])
def test_powershell_words_are_read_as_powershell_reads_them(command, words):
    commands = disk_space._ps_commands(command)
    if words is None:
        assert [c[0] for c in commands] == ["Expand-Archive", "Get-ChildItem"]
    else:
        assert commands == [words]


def test_an_unclosed_quote_is_not_read(tmp_path):
    assert disk_space._ps_commands("iwr 'x -OutFile a.zip") == []
    assert ps_check("iwr 'x -OutFile a.zip", tmp_path, 0.5) is None


def test_invoke_webrequest_to_a_full_drive_asks(tmp_path):
    verdict = ps_check("Invoke-WebRequest -Uri https://x.org/a.zip -OutFile out\\a.zip", tmp_path, 1.0)
    assert verdict.action == "ask" and "1.0 GiB" in verdict.reason


@pytest.mark.parametrize("command", [
    "iwr https://x.org/a.zip -OutFile a.zip", "irm https://x.org/a.json -OutFile a.json",
    "curl https://x.org/a.zip -OutFile a.zip", "wget https://x.org/a.zip -OutFile:a.zip",
    "Invoke-WebRequest https://x.org/a.zip -OutF a.zip",       # an unambiguous prefix of the parameter name
    "invoke-webrequest https://x.org/a.zip -outfile a.zip",
])
def test_powershell_download_spellings_all_ask_on_a_full_drive(tmp_path, command):
    assert ps_check(command, tmp_path, 1.0).action == "ask"


@pytest.mark.parametrize("command", [
    "Invoke-WebRequest https://x.org/page",                  # the answer goes to the pipeline, not to a file
    "iwr https://x.org/a.zip | Select-Object StatusCode",
    "iwr https://x.org/a.zip -OutFile $env:TEMP\\a.zip",     # a variable: not written out, left alone
    "Get-ChildItem C:\\data", "Write-Output 'hello'", "Copy-Item a b",
])
def test_other_powershell_commands_and_unreadable_paths_never_ask(tmp_path, command):
    assert ps_check(command, tmp_path, 0.5) is None


def test_a_powershell_download_of_unknown_size_only_warns_on_a_low_drive(tmp_path):
    assert ps_check("iwr https://x.org/a.zip -OutFile a.zip", tmp_path, 6).action == "warn"
    assert ps_check("iwr https://x.org/a.zip -OutFile a.zip", tmp_path, 50) is None


def test_expand_archive_that_will_not_fit_asks_with_the_zips_real_size(tmp_path):
    with zipfile.ZipFile(tmp_path / "raw.zip", "w") as z:
        z.writestr("big.bin", b"\0" * (3 * 1024 * 1024))
    tight = lambda f: Space(10 * GIB, 0, int(2 * GIB + 1024))
    for command in ("Expand-Archive raw.zip -DestinationPath out", "Expand-Archive -Path raw.zip -DestinationPath out -Force",
                    "Expand-Archive -LiteralPath raw.zip -DestinationPath:out", "Expand-Archive raw.zip out"):
        verdict = disk_space.powershell_check(command, str(tmp_path), probe=tight, **CHECK)
        assert verdict.action == "ask" and "필요" in verdict.reason, command
    assert ps_check("Expand-Archive raw.zip -DestinationPath out", tmp_path, 50) is None


def test_expand_archive_without_a_destination_unpacks_into_the_current_folder(tmp_path):
    """#1184 review: an omitted destination is not a reason to skip the check; the folder it unpacks into is the current one."""
    assert ps_check("Expand-Archive raw.zip", tmp_path, 0.5).action == "ask"
    assert ps_check("Expand-Archive -Path raw.zip -Force", tmp_path, 0.5).action == "ask"
    assert ps_check("Expand-Archive raw.zip", tmp_path, 50) is None
    with zipfile.ZipFile(tmp_path / "big.zip", "w") as z:
        z.writestr("big.bin", b"\0" * (3 * 1024 * 1024))
    tight = lambda f: Space(10 * GIB, 0, int(2 * GIB + 1024))
    verdict = disk_space.powershell_check("Expand-Archive big.zip", str(tmp_path), probe=tight, **CHECK)
    assert verdict.action == "ask" and "필요" in verdict.reason


@pytest.mark.parametrize("command", [
    "iwr https://x.org/a.zip -Headers $headers -OutFile a.zip",             # a variable elsewhere does not hide the path
    "iwr https://x.org/a.zip -Credential $cred -UseBasicParsing -OutFile out\a.zip",
    "Expand-Archive -Path raw.zip -DestinationPath out -ErrorAction $action",
])
def test_a_variable_that_is_not_the_output_path_does_not_hide_the_command(tmp_path, command):
    assert ps_check(command, tmp_path, 1.0).action == "ask"


@pytest.mark.parametrize("command", [
    "iwr https://x.org/a.zip -OutFile $dest", "iwr https://x.org/a.zip -OutFile $env:TEMP\a.zip",
    "Expand-Archive raw.zip -DestinationPath $out",
])
def test_a_variable_in_the_path_that_is_written_still_leaves_the_command_alone(tmp_path, command):
    assert ps_check(command, tmp_path, 0.5) is None


@pytest.mark.parametrize("command", [
    "Start-BitsTransfer -Source big.zip -Destination https://x.org/upload -TransferType Upload",
    "Start-BitsTransfer big.zip https://x.org/upload -TransferType Upload",
    "Start-BitsTransfer -Source big.zip -Destination https://x.org/upload",       # a remote destination is no local folder
    "Start-BitsTransfer -Source big.zip -Destination out\big.zip -TransferType Upload",
])
def test_a_bits_upload_is_not_a_local_download(tmp_path, command):
    assert ps_check(command, tmp_path, 0.5) is None


def test_start_bitstransfer_reads_its_destination(tmp_path):
    for command in ("Start-BitsTransfer -Source https://x.org/a.zip -Destination out\\a.zip",
                    "Start-BitsTransfer https://x.org/a.zip out\\a.zip"):
        assert ps_check(command, tmp_path, 1.0).action == "ask", command


def test_a_powershell_chain_is_read_command_by_command_and_the_worst_wins(tmp_path):
    verdict = ps_check("Get-Date; iwr https://x.org/a.zip -OutFile a.zip; Write-Output done", tmp_path, 1.0)
    assert verdict.action == "ask"


def test_a_leading_set_location_moves_where_the_next_command_runs(tmp_path):
    seen = []

    def probe(folder):
        seen.append(folder)
        return Space(100 * GIB, 0, 50 * GIB)

    disk_space.powershell_check("Set-Location big; iwr https://x.org/a.zip -OutFile a.zip", str(tmp_path), probe=probe, **CHECK)
    assert seen and seen[0] == (tmp_path / "big").resolve()


def test_an_unreadable_drive_never_asks_a_powershell_command(tmp_path):
    def broken(folder):
        raise OSError("drive gone")

    assert disk_space.powershell_check("iwr u -OutFile a.zip", str(tmp_path), probe=broken, **CHECK) is None


@pytest.mark.asyncio
async def test_the_gate_turns_an_allowed_powershell_download_on_a_full_drive_into_a_card(gate, monkeypatch):
    mcp, sent = gate
    monkeypatch.setattr(disk_space, "usage", drive(0.5))
    result = json.loads(await mcp.approval_prompt(
        "PowerShell", {"command": "Invoke-WebRequest https://example.org/a.zip -OutFile outputs/raw.zip"}))
    assert result["behavior"] == "deny"  # the fake PI declined the card
    assert len(sent) == 1 and sent[0][0].endswith("/approval") and "여유" in sent[0][1]["summary"]


@pytest.mark.asyncio
async def test_the_gate_leaves_a_powershell_download_alone_when_there_is_room(gate, monkeypatch):
    mcp, sent = gate
    monkeypatch.setattr(disk_space, "usage", drive(500, 1000))
    result = json.loads(await mcp.approval_prompt(
        "PowerShell", {"command": "Invoke-WebRequest https://example.org/a.zip -OutFile outputs/raw.zip"}))
    assert result["behavior"] == "allow" and sent == []


@pytest.mark.parametrize("rule,expected", [
    ("PowerShell(Invoke-WebRequest *)", True), ("PowerShell(iwr:*)", True), ("PowerShell(Expand-Archive *)", True),
    ("PowerShell(Start-BitsTransfer *)", True), ("PowerShell(Get-ChildItem *)", False), ("PowerShell", False),
    ("PowerShell()", False)])
def test_which_powershell_rules_preapprove_a_bulk_write(rule, expected):
    assert disk_space.preapproves_bulk_write(rule) is expected
