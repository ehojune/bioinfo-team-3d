"""Shell write detection reads only shell syntax: '>' inside strings, comments and here-documents is text.

Blanking non-syntax text may only remove false approval cards. Every case where the shell would really
redirect must still be found, and when the scanner is unsure it falls back to the raw-text scan.
"""

import pytest

from labhq.policy import _blank_non_syntax, _shell_write_targets, evaluate_tool
from labhq.settings import DataZone, PolicySettings

ROOTS = ["/work", "C:/work"]


def _decide(tool, command):
    policy = PolicySettings(data_zones=[DataZone(path="/data/cohort", level="restricted")])
    return evaluate_tool(tool, {"command": command}, policy, ROOTS)


# The two commands from the 2026-10-03 trial run, both inside the staff member's own folder.
TRIAL_POWERSHELL = (r"$t=$t.Replace('Run: <s1 venv>\Scripts\python.exe outputs\scripts\s7_network.py',"
                    r"'Run: python outputs\scripts\s7_network.py')")
TRIAL_BASH = (
    "python - <<'EOF'\n"
    "from pathlib import Path\n"
    "p = Path('outputs/README.md')\n"
    "t = p.read_text(encoding='utf-8')\n"
    "def rep(a, b):\n"
    "    global t\n"
    "    t = t.replace(a, b)\n"
    "rep('''Run:\n"
    " <s1 venv>\\Scripts\\python.exe outputs\\scripts\\s7_network.py''', '''Run:\n"
    " python outputs/scripts/s7_network.py''')\n"
    "p.write_text(t, encoding='utf-8')\n"
    "EOF"
)

TRIAL_CURL_SED = (
    "curl -s https://ftp.ncbi.nlm.nih.gov/geo/$u | "
    "grep -o 'href=\"[^\"]*\"[^<]*<[^\\n]*' | sed 's/<[^>]*>//g' | "
    "grep -v '^href=\"/' | head"
)
TRIAL_CAT_SCRIPT = (
    "cd /work; mkdir -p outputs/scripts; "
    "cat > outputs/scripts/s03_pairing.py <<'E'\n"
    "import re\n"
    "import subprocess\n"
    "pattern = re.compile(r'(?:>|/)')\n"
    "subprocess.run(['python', '--version'], check=True)\n"
    "E"
)


@pytest.mark.parametrize("tool,command", [
    ("PowerShell", TRIAL_POWERSHELL),
    ("PowerShell", TRIAL_POWERSHELL.replace("\\", "\\\\")),
    ("Bash", TRIAL_BASH),
    # A here-document body is text even where no quote protects it.
    ("Bash", "cat <<'EOF' > notes.md\nRun: <s1 venv>\\Scripts\\python.exe\nEOF"),
    ("Bash", "cat <<-\"END\" > notes.md\n\tit's <a>\\b\n\tEND\necho done"),
    ("Bash", "cat <<\\EOF > notes.md\n$(not run) <a>\\b\nEOF"),
    ("Bash", 'echo "Run: <s1 venv>\\Scripts\\python.exe" > notes.md'),
    ("Bash", "echo 'a > /elsewhere/out'"),
    ("Bash", "ls  # see <s1 venv>\\Scripts\\python.exe"),
    ("Bash", "echo a\\>/elsewhere/out"),
    ("PowerShell", "Get-Date # <s1 venv>\\Scripts\\python.exe"),
    ("PowerShell", "$s = @'\nRun: <s1 venv>\\Scripts\\python.exe 'quoted'\n'@\nSet-Content -Path notes.md -Value $s"),
    ("PowerShell", '$s = @"\nRun: <s1 venv>\\Scripts\\python.exe "x"\n"@'),
    ("PowerShell", "Write-Output 'it''s <a>\\b'"),
    ("PowerShell", 'Write-Output "C:\\dir\\" ; Write-Output "<a>\\b"'),
    # Command-name writes are split on the same blanked text, so a quoted ';' or '|' is not a new command.
    ("Bash", 'git commit -m "fix; cp a /elsewhere/b"'),
    ("PowerShell", "Write-Output 'a | Set-Content C:/elsewhere/x'"),
    ("Bash", "cat <<'EOF' > notes.md\ncp a /elsewhere/b\nEOF"),
    # The same edits written the way staff usually write them.
    ("PowerShell", "$p = 'outputs\\README.md'; $t = Get-Content -Raw $p; " + TRIAL_POWERSHELL
     + "; Set-Content -NoNewline -Path $p -Value $t"),
    ("PowerShell", "(Get-Content -Raw README.md).Replace('<s1 venv>\\Scripts\\python.exe', 'python') | Out-File x.md"),
    ("Bash", "cd outputs && " + TRIAL_BASH),
    ("Bash", 'git commit -m "move a -> /elsewhere/b"'),
    ("Bash", TRIAL_CURL_SED),
    ("Bash", TRIAL_CAT_SCRIPT),
])
def test_text_that_only_looks_like_a_redirect_is_not_a_write(tool, command):
    assert _decide(tool, command).action == "allow"


@pytest.mark.parametrize("tool,command,target", [
    ("Bash", 'echo hi > "/elsewhere/out file.txt"', "/elsewhere/out file.txt"),
    ("Bash", "echo 'a > b' > /elsewhere/out", "/elsewhere/out"),
    ("Bash", "python x.py 2>> /elsewhere/err.log", "/elsewhere/err.log"),
    ("Bash", "echo x > /elsewhere/out # it's fine", "/elsewhere/out"),
    ("PowerShell", "Get-Process *> C:/elsewhere/all.txt", "C:/elsewhere/all.txt"),
    ("PowerShell", "Get-Date > 'C:/elsewhere/a b.txt'", "C:/elsewhere/a b.txt"),
    # After a here-document ends, its following lines are shell again; the header line always was.
    ("Bash", "cat <<'EOF'\na > b\nEOF\necho x > /elsewhere/out", "/elsewhere/out"),
    ("Bash", "cat <<'EOF' > /elsewhere/notes.md\nbody\nEOF", "/elsewhere/notes.md"),
    # An apostrophe inside a comment must not pair with a later quote and hide the line between.
    ("Bash", "# don't\necho x > C:/out\necho 'y'", "C:/out"),
    ("PowerShell", "# don't\necho x > C:/out\necho 'y'", "C:/out"),
    ("Bash", "cat <<'EOF' > notes.md\nx\nEOF\ncp notes.md /elsewhere/notes.md", "/elsewhere/notes.md"),
])
def test_real_redirects_are_still_found(tool, command, target):
    assert target in _shell_write_targets(command, powershell=tool == "PowerShell")
    decision = _decide(tool, command)
    assert decision.action == "ask" and target in decision.reason


@pytest.mark.parametrize("tool,command", [
    # An unmatched quote: nothing after it is blanked.
    ("Bash", "echo it's > /elsewhere/out"),
    ("PowerShell", "Write-Output don't > C:/elsewhere/out"),
    # Escapes outside quotes are literal characters, not string openers.
    ("Bash", "echo it\\'s > /elsewhere/out; echo 'q'"),
    ("Bash", "echo x\\ # > /elsewhere/out"),
    ("Bash", "echo x\r# > /elsewhere/out"),
    ("PowerShell", "Write-Output `'a > C:/elsewhere/out; Write-Output 'b'"),
    ("Bash", "echo $'it\\'s' > /elsewhere/out; echo 'q'"),
    # Substitutions inside double quotes or unquoted here-documents run commands and may nest quotes.
    ("Bash", 'echo "$(echo "it\'s")" > /elsewhere/out; echo \'q\''),
    ("Bash", 'x="$(date > /elsewhere/out)"'),
    ("Bash", 'echo "${x:-"it\'s"}" > /elsewhere/out; echo \'q\''),
    ("Bash", "cat <<EOF\n$(date > /elsewhere/out)\nEOF"),
    ("PowerShell", 'Write-Output "$(Get-Date > C:/elsewhere/out)"'),
    # The raw scan tries every '>', so a quoted target no longer swallows the redirect inside it.
    ("Bash", 'echo x > "$(echo a > /elsewhere/out)"'),
    ("PowerShell", '<# c #>"$(Get-Date > C:/elsewhere/out)"'),
    # An unterminated here-document is not blanked.
    ("Bash", "cat <<EOF\necho x > /elsewhere/out"),
    # '#' where the shell may not see a comment.
    ("Bash", "(( a = 2 #3 )) ; echo x > /elsewhere/out"),
    ("Bash", "echo ${x #y} ; echo z > /elsewhere/out"),
    ("Bash", "echo `echo a # c` > /elsewhere/out"),
    ("PowerShell", "$x#'\nGet-Date > C:/elsewhere/out\n# it's"),
    ("PowerShell", "<# don't #> Get-Date > C:/elsewhere/out; 'q'"),
    ("PowerShell", "${x #'} > C:/elsewhere/out; 'y'"),
    # PowerShell also closes strings with typographic quotes.
    ("PowerShell", "Write-Output 'x\u2019 > C:/elsewhere/out \u2019'"),
    # Quoted text run as code by another shell or a write-capable one-liner keeps its redirects.
    ("Bash", 'bash -c "echo x > /elsewhere/out"'),
    ("Bash", "sh -c 'echo x > /elsewhere/out'"),
    ("Bash", "eval 'echo x > /elsewhere/out'"),
    ("Bash", "awk '{ print > \"/elsewhere/out\" }' in.txt"),
    ("Bash", "cat <<'EOF' | bash\necho x > /elsewhere/out\nEOF"),
    ("PowerShell", 'cmd /c "dir > C:\\elsewhere\\out.txt"'),
    ("PowerShell", "powershell -Command 'Get-Date > C:/elsewhere/out'"),
    ("PowerShell", "Invoke-Expression 'Get-Date > C:/elsewhere/out'"),
    ("PowerShell", '& "C:\\Program Files\\Git\\bin\\bash.exe" -c \'echo x > /elsewhere/out\''),
    # A here-document delimiter bash reads differently: $'..' and $".." drop the '$'.
    ("Bash", "cat <<$'EOF'\nbody\nEOF\necho x > /elsewhere/out\n$EOF"),
    ("Bash", 'cat <<$"EOF"\nbody\nEOF\necho x > /elsewhere/out\n$EOF'),
    # '<<' inside $[..], ${..}, a subscript or an extglob pattern is not a here-document.
    ("Bash", "echo $[1<<2]\necho x > /elsewhere/out\n2]"),
    ("Bash", "x=abcdef; echo ${x:1<<2}\necho x > /elsewhere/out\n2}"),
    ("Bash", "a[1<<2]=x\necho x > /elsewhere/out\n2]=x"),
    ("Bash", "ls @(x<<y)\necho x > /elsewhere/out\ny"),
    # Quotes inside arithmetic do not quote: bash runs the $(..) in each of these.
    ("Bash", "echo $(( '$(echo x > /elsewhere/out)' ))"),
    ("Bash", "echo $[ '$(echo x > /elsewhere/out)' ]"),
    ("Bash", "x=abc; echo ${x:'$(echo x > /elsewhere/out)'}"),
    ("Bash", "[[ 'a[$(echo x > /elsewhere/out)]' -eq 0 ]]"),
    ("Bash", "a=(1); [ -v 'a[$(echo x > /elsewhere/out)]' ]"),
    ("Bash", "test -v 'a[$(echo x > /elsewhere/out)]'"),
    # Git Bash ends a here-document at 'EOF\r', Linux bash does not.
    ("Bash", "cat <<'EOF'\nEOF\r\nit's\nEOF\necho x > /elsewhere/out\necho \\' 'y'"),
    # Commands that run quoted text as code, beyond any list of shell names.
    ("Bash", "source <(echo 'echo x > /elsewhere/out')"),
    ("Bash", ". /dev/stdin <<< 'echo x > /elsewhere/out'"),
    ("Bash", "echo 'echo x > /elsewhere/out' | at now"),
    ("Bash", "echo '* * * * * echo x > /elsewhere/out' | crontab -"),
    ("Bash", "git -c alias.w='!echo x > /elsewhere/out' w"),
    ("Bash", "sed '1e echo x > /elsewhere/out' in.txt"),
    ("Bash", "GIT_EDITOR='sh -c \"echo x > /elsewhere/out\"' git commit --amend"),
    ("Bash", "printf -v GIT_EDITOR '%s' 'sh -c \"echo x > /elsewhere/out\"'; git commit --amend"),
    ("Bash", "./tool.sh 'echo x > /elsewhere/out'"),
    ("PowerShell", "$ExecutionContext.InvokeCommand.InvokeScript('Get-Date > C:/elsewhere/out')"),
    ("PowerShell", "$ExecutionContext.InvokeCommand.NewScriptBlock('Get-Date > C:/elsewhere/out').Invoke()"),
    ("PowerShell", "$ExecutionContext.InvokeCommand | % InvokeScript 'Get-Date > C:/elsewhere/out'"),
    ("PowerShell", "`iex 'Get-Date > C:/elsewhere/out'"),
    ("PowerShell", 'schtasks /create /tn t /tr "cmd /c dir > C:\\elsewhere\\out" /sc once /st 00:00'),
    ("PowerShell", "$env:GIT_EDITOR = 'cmd /c dir > C:\\elsewhere\\out'; git commit --amend"),
    ("PowerShell", "Set-Content 'env:GIT_EDITOR' 'cmd /c dir > C:\\elsewhere\\out'; git commit --amend"),
    ("PowerShell", "$function:Write-Output = 'Get-Date > C:/elsewhere/out'; Write-Output x"),
    ("PowerShell", "Set-Content function:foo 'Get-Date > C:/elsewhere/out'; foo"),
    ("PowerShell", "$ExecutionContext.InvokeCommand. InvokeScript('Get-Date > C:/elsewhere/out')"),
    # Inline interpreter code that hands a string to a shell.
    ("Bash", "python -c \"import os; os.system('echo x > /elsewhere/out')\""),
    ("Bash", "Rscript -e 'system(\"echo x > /elsewhere/out\")'"),
    ("Bash", "node -e \"require('child_process').execSync('echo x > /elsewhere/out')\""),
    ("Bash", "python - <<'EOF'\nimport os\nos.system('echo x > /elsewhere/out')\nEOF"),
    ("Bash", "python run.py 'echo x > /elsewhere/out'"),
    ("PowerShell", "python -c \"import os; os.system('echo x > C:/elsewhere/out')\""),
    ("PowerShell", ".venv\\Scripts\\python.exe -c \"import os; os.system('echo x > C:/elsewhere/out')\""),
    # A PowerShell here-string starts only at a token start; 'x@' is one generic token.
    ("PowerShell", "Write-Output x@'\nfoo'\nGet-Date > C:/elsewhere/out\n'@ #'"),
    # The call operator, dot-sourcing and a [scriptblock] cast run a string, even inside parentheses (PR #340).
    ("PowerShell", "& ([scriptblock]'Get-Date > C:/elsewhere/out')"),
    ("PowerShell", "& ([ScriptBlock]::Create('Get-Date > C:/elsewhere/out'))"),
    ("PowerShell", ". ([scriptblock]'Get-Date > C:/elsewhere/out')"),
    ("PowerShell", "$b = [System.Management.Automation.ScriptBlock]'Get-Date > C:/elsewhere/out'; & $b"),
    ("PowerShell", "Write-Output ok; (& ([scriptblock]\"Get-Date > C:/elsewhere/out\"))"),
])
def test_redirects_the_blanking_must_not_hide(tool, command):
    assert _decide(tool, command).action == "ask"


@pytest.mark.parametrize("command,target", [
    ("bash -c 'echo x > /etc/y'", "/etc/y"),
    ('eval "echo x > /tmp/y"', "/tmp/y"),
    ("xargs sh -c 'cat > /x'", "/x"),
    ("ssh h 'echo > /x'", "/x"),
    ("sed 'w /etc/x' f", "/etc/x"),
    ("sed -n 's/a/b/w /tmp/o' f", "/tmp/o"),
    ("sed -e 'w /tmp/a' -e 'W /tmp/b' f", "/tmp/a"),
    ("sed -e 'w /tmp/a' -e 'W /tmp/b' f", "/tmp/b"),
    ("sed -i s/a/b/ /etc/hosts", "/etc/hosts"),
    ("curl -o /tmp/x URL", "/tmp/x"),
    ("curl --output=/tmp/x URL", "/tmp/x"),
    ("curl --output-dir /tmp -O URL", "/tmp"),
    ("wget -O /tmp/x URL", "/tmp/x"),
    ("wget -P /tmp URL", "/tmp"),
])
def test_commands_that_execute_text_or_name_outputs_keep_their_write_targets(command, target):
    assert target in set(_shell_write_targets(command))
    assert _decide("Bash", command).action == "ask"


def test_a_sed_execute_command_keeps_the_raw_fallback():
    command = "sed 'e echo x > /tmp/y' f"
    assert "/tmp/y" in set(_shell_write_targets(command))
    assert _decide("Bash", command).action == "ask"


@pytest.mark.parametrize("command", [
    "sed 'e rm -rf /' f",
    "sed 's/a/b/e' f",
    'sed "$script" f',
    "sed -f rules.sed 'f > /tmp/x'",
    "curl -K config 'URL > /tmp/x'",
    "curl --config=config 'URL > /tmp/x'",
])
def test_commands_with_executable_or_unresolved_configuration_keep_the_raw_fallback(command):
    assert _blank_non_syntax(command, powershell=False) is None


@pytest.mark.parametrize("tool, command, expected", [
    # 7th mock trial: the stream redirect's target was read as the copy destination.
    ("Bash", "cp .tmp/preprocess.py outputs/ 2>/dev/null; ls outputs", ["outputs/"]),
    ("Bash", "awk -F'\t' '{print $1}' outputs/t.tsv; cp .tmp/x.py outputs/ 2>/dev/null", ["outputs/"]),
    ("Bash", "cp a.txt b.txt 2>&1", ["b.txt"]),
    ("Bash", "mv a.txt b.txt < in.txt", ["b.txt"]),
    ("PowerShell", "Copy-Item a.txt -Destination b.txt 2>$null", ["b.txt"]),
])
def test_a_redirect_is_not_read_as_a_command_word(tool, command, expected):
    targets = list(_shell_write_targets(command, powershell=tool == "PowerShell"))
    assert "/dev/null" not in targets and "$null" not in targets and "&1" not in targets and "1" not in targets
    assert set(targets) == set(expected)


def test_a_real_redirect_next_to_a_copy_is_still_a_target():
    assert set(_shell_write_targets("cp a outputs/ 2>/elsewhere/log")) == {"outputs/", "/elsewhere/log"}


@pytest.mark.parametrize("command, action", [
    ("cp /elsewhere/src >(cat)", "allow"),
    ("cp a >(cat > /elsewhere/x)", "ask"),
    ("diff <(sort a) <(sort b)", "allow"),
])
def test_process_substitution_is_an_argument_but_its_inner_redirect_is_checked(command, action):
    assert _decide("Bash", command).action == action


@pytest.mark.parametrize("command, target", [
    ("cp a /work2>/dev/null", "/work2"),          # the 2 belongs to the path; only `>/dev/null` is a redirect
    ("cp a out1>/dev/null", "out1"),
    ("cp a /elsewhere/x9 2>/dev/null", "/elsewhere/x9"),
])
def test_a_digit_glued_to_a_path_is_not_a_stream_number(command, target):
    assert target in set(_shell_write_targets(command))


def test_a_path_ending_in_a_digit_outside_the_roots_still_asks():
    assert _decide("Bash", "cp a /work2>/dev/null").action == "ask"


# 8th mock trial: a hash next to a quoted manifest row, all inside the staff member's own folder.
TRIAL8_POWERSHELL = (
    "$h = (Get-FileHash outputs/raw/cel_header_scan_dates.tsv -Algorithm SHA256).Hash.ToLower(); "
    "$len=(Get-Item outputs/raw/cel_header_scan_dates.tsv).Length; "
    'Add-Content -Encoding utf8 outputs/data_manifest.tsv "outputs/raw/cel_header_scan_dates.tsv`t'
    "https://ftp.ncbi.nlm.nih.gov/geo/samples/GSM254nnn/<GSM>/suppl/<GSM>.CEL.gz (107 URLs, per-row in file)`t$h`t"
    '$len`t2026-10-03`t1`tDerived table: scan date from CEL DatHeader (header bytes only streamed)"; '
    "Get-Content outputs/data_manifest.tsv")


@pytest.mark.parametrize("command", [
    TRIAL8_POWERSHELL,
    'Add-Content -Path outputs/m.tsv -Value "/suppl/<GSM>.CEL.gz"',  # a text parameter is not a destination
    "Set-Content outputs/x.txt -Encoding utf8 -Value C:/data/row",
    "Set-Content -Path outputs/x.txt C:/data/row",  # with -Path named, the positional word is the value
    "Out-File -FilePath:outputs/x.txt -Encoding utf8 C:/data/row",
])
def test_quoted_rows_written_inside_the_folder_do_not_ask(command):
    assert _decide("PowerShell", command).action == "allow"


@pytest.mark.parametrize("tool, command", [
    ("PowerShell", "Set-Content -Encoding utf8 C:/elsewhere/x.txt a"),  # the path after a flag was never read
    ("PowerShell", "Add-Content -NoNewline -Encoding utf8 C:/elsewhere/x.txt a"),
    ("PowerShell", "Out-File -Encoding utf8 C:/elsewhere/x.txt -InputObject a"),
    ("PowerShell", "Set-Content -Pa C:/elsewhere/x.txt -Value a"),  # a prefix of -Path
    ("PowerShell", "Set-Content -Path:C:/elsewhere/x.txt -Value a"),
    ("PowerShell", "Set-Content -Fo C:/elsewhere/x.txt a"),  # a prefix of the -Force switch
    ("PowerShell", "Set-Content -Path C:/elsewhere/x.txt outputs/row"),
    ("PowerShell", "sc C:/elsewhere/x.txt a"),
    ("PowerShell", "New-Item -ItemType File C:/elsewhere/x.txt"),
    ("PowerShell", "Get-Process | Export-Csv -NoTypeInformation C:/elsewhere/p.csv"),
    ("PowerShell", "Get-Date | Tee-Object C:/elsewhere/t.txt"),
    ("Bash", "echo x | tee /elsewhere/x"),
    ("Bash", "echo x | tee -a outputs/log /elsewhere/x"),
])
def test_a_write_named_after_a_flag_or_through_tee_still_asks(tool, command):
    assert _decide(tool, command).action == "ask"


# 12th mock trial: Claude's Bash on Windows is Git Bash, and /c/... is the C: drive.
@pytest.mark.parametrize("command, action", [
    ("head -c 400 x.tsv > /c/work/.tmp/header_dump.txt", "allow"),   # the staff member's own folder
    ("cp a.txt /C/work/outputs/a.txt", "allow"),
    ("echo x > /c/elsewhere/out.txt", "ask"),                        # still outside the roots
    ("echo x > /d/work/out.txt", "ask"),                             # another drive
])
def test_git_bash_drive_paths_are_read_as_windows_paths(command, action):
    assert _decide("Bash", command).action == action


def test_a_posix_runner_keeps_slash_c_as_a_posix_folder():
    policy = PolicySettings()
    for roots, action in ((["/work"], "ask"), (["/c/work"], "allow")):
        decision = evaluate_tool("Bash", {"command": "echo x > /c/work/out.txt"}, policy, roots, windows=False)
        assert decision.action == action


def test_a_git_bash_path_into_a_restricted_zone_is_still_refused():
    """PR #364 review: converting only the write target let `cp /c/<zone>/raw /c/<root>/out` through."""
    policy = PolicySettings(data_zones=[DataZone(path="C:/work/restricted", level="restricted")])
    decision = evaluate_tool("Bash", {"command": "cp /c/work/restricted/raw.txt /c/work/out.txt"}, policy,
                             ["C:/work"])
    assert decision.action != "allow" and "restricted" in decision.reason


def test_a_git_bash_path_into_a_private_folder_is_still_refused():
    decision = evaluate_tool("Bash", {"command": "cat /c/Users/pi/.ssh/id_rsa > /c/work/key.txt"}, PolicySettings(),
                             ["C:/work"], workdir="C:/work", private_paths=["C:/Users/pi/.ssh"],
                             private_enabled=True, home="C:/Users/pi")
    assert decision.action != "allow"
