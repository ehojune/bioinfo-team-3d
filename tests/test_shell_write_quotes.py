"""Shell write detection reads only shell syntax: '>' inside strings, comments and here-documents is text.

Blanking non-syntax text may only remove false approval cards. Every case where the shell would really
redirect must still be found, and when the scanner is unsure it falls back to the raw-text scan.
"""

import pytest

from labhq.policy import _shell_write_targets, evaluate_tool
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
])
def test_redirects_the_blanking_must_not_hide(tool, command):
    assert _decide(tool, command).action == "ask"
