"""Environment owner exceptions must identify a destination inside that step's workspace."""

import os
import subprocess

import pytest

from labhq.environment_install import shared_environment_install_denial


def denial(tmp_path, command, *, environment_step=True, tool="Bash", protected=True):
    return shared_environment_install_denial(
        tool, {"command": command}, protected=protected,
        environment_step=environment_step, workdir=str(tmp_path))


@pytest.mark.parametrize("command", [
    "python -m venv .venv && ./.venv/bin/python -m pip install --only-binary=:all: scanpy",
    r"python -m venv .venv; .\.venv\Scripts\python.exe -m pip install scanpy",
    "./.venv/bin/pip install scanpy",
    "command ./.venv/bin/python -m pip install scanpy",
    "env ./.venv/bin/python -m pip install scanpy",
    "env -u PIP_TARGET -- ./.venv/bin/python -m pip install scanpy",
    "env PIP_INDEX_URL=https://example.org/simple pip install --target ./packages scanpy",
    "exec ./.venv/bin/python -m pip install scanpy",
    "if true; then ./.venv/bin/python -m pip install scanpy; else uv pip --python .venv install scanpy; fi",
    "if true; then pip install --target ./packages scanpy; fi",
    "env conda create -p ./conda-env python=3.12",
    "python -m pip install --target ./packages scanpy",
    "pip install --target=./packages scanpy",
    "PIP_INDEX_URL=https://example.org/simple pip install --target ./packages scanpy",
    "pip install -t./packages scanpy",
    "pip install -t=./packages scanpy",
    "pip install -t=/shared scanpy",  # pip's optparse keeps '=' as a literal local path component
    "pip install --target ./first --target ./second scanpy",
    "conda install -p ./conda-env scanpy",
    "conda create --prefix=./conda-env python=3.12",
    "mamba install -p./conda-env scanpy",
    "conda install -p=./conda-env scanpy",
    "micromamba install --prefix ./conda-env scanpy",
    "uv pip --python .venv install scanpy",
    "uv pip install --python ./.venv/bin/python scanpy",
    "uv pip install --target ./packages scanpy",
    "uv --directory ./nested pip install --python .venv scanpy",
    "cd ./nested && python -m pip install --target ./packages scanpy",
    "pushd ./nested; popd; ./.venv/bin/python -m pip install scanpy",
    "Rscript -e \"install.packages('limma', lib='./r-library')\"",
    "R CMD INSTALL -l ./r-library package.tar.gz",
    "R CMD INSTALL --library=./r-library package.tar.gz",
    "R CMD INSTALL package.tar.gz -l './r library' --no-docs",
    "env R CMD INSTALL -l ./first --library=./second package.tar.gz",
    "$env:R_LIBS_USER='./r-library'; Rscript -e \"BiocManager::install('limma')\"",
])
def test_environment_owner_allows_explicit_workspace_destinations(tmp_path, command):
    assert denial(tmp_path, command) is None


@pytest.mark.parametrize("command", [
    "python -m pip install scanpy",
    "pip install scanpy",
    "echo $(pip install scanpy)",
    'printf "%s" "$(pip install scanpy)"',
    "ls <(pip install scanpy)",
    "echo `pip install scanpy`",
    '''echo "it's $(pip install scanpy) now's"''',
    r'echo "literal \" text $(pip install scanpy)"',
    "command pip install scanpy",
    "env pip install scanpy",
    "env -u PIP_TARGET pip install scanpy",
    "if true; then pip install scanpy; fi",
    "if false; then true; else pip install scanpy; fi",
    "while true; do pip install scanpy; done",
    "unknown-launcher pip install --target ./packages scanpy",
    "nice ./.venv/bin/python -m pip install scanpy",
    "env --unknown ./.venv/bin/python -m pip install scanpy",
    "env -C ../shared pip install --target ./packages scanpy",
    'pip install --target "./packages scanpy',
    'env "pip install --target ./packages scanpy',
    'p"i"p install scanpy',
    "./unknown-manager install scanpy",
    "if true; then cd ..; else cd ./step; fi; pip install --target ./packages scanpy",
    "for name in a b; do cd ..; pip install --target ./packages scanpy; done",
    "uv pip install scanpy",
    "/runner/env/bin/pip install scanpy",
    r"C:\runner\env\Scripts\python.exe -m pip install scanpy",
    "conda install scanpy",
    "conda install -n base scanpy",
    "conda install -p ./local -nbase scanpy",
    "conda create -n shared python=3.12",
    "mamba install -p ../shared scanpy",
    "micromamba install --prefix /shared scanpy",
    "pip install --target ../shared scanpy",
    "conda install -p=/shared scanpy",
    "conda install -p=../shared scanpy",
    "uv pip install -t=/shared scanpy",
    "pip install --target ./local/../../shared scanpy",
    "pip install --target ./local --target /shared scanpy",
    "pip install --target ./local --prefix /shared scanpy",
    "./.venv/bin/python -m pip install --root /runner scanpy",
    "./.venv/bin/python -m pip install --user scanpy",
    "PIP_TARGET=/runner ./.venv/bin/python -m pip install scanpy",
    "$env:PIP_TARGET='../shared'; ./.venv/Scripts/python.exe -m pip install scanpy",
    "export PIP_PREFIX=../shared; true; ./.venv/bin/python -m pip install scanpy",
    "uv pip install --python python scanpy",
    "uv pip install --python 3.12 scanpy",
    "uv pip install --python ../shared scanpy",
    "uv pip install --python .venv --system scanpy",
    "uv pip install --python .venv --python /runner/bin/python scanpy",
    "uv --directory ../shared pip install --python .venv scanpy",
    "uv pip install --target scanpy --python",
    "pip install --target",
    "pip install --target=$HOME/packages scanpy",
    "cd .. && python -m pip install --target ./packages scanpy",
    "Set-Location -LiteralPath ..; ./.venv/Scripts/python.exe -m pip install scanpy",
    "cd $OTHER && pip install --target ./packages scanpy",
    "(cd ..; pip install --target ./packages scanpy)",
    "true & pip install scanpy",
    "true & pip install --target ../shared scanpy",
    "Rscript -e \"install.packages('limma')\"",
    "R CMD INSTALL package.tar.gz",
    "R CMD INSTALL --library=/shared package.tar.gz",
    "R CMD INSTALL -l ../shared package.tar.gz",
    "R CMD INSTALL -l ./local --library=/shared package.tar.gz",
    "R CMD INSTALL --library=/shared -l ./local package.tar.gz",
    "R CMD INSTALL -l",
    "R CMD INSTALL -l --no-docs package.tar.gz",
    "R CMD INSTALL --library= package.tar.gz",
    "R CMD INSTALL --library ./local package.tar.gz",
    "R CMD INSTALL -l./local package.tar.gz",
    "R CMD INSTALL -l=./local package.tar.gz",
    "R CMD INSTALL --LIBRARY=./local package.tar.gz",
    "R CMD INSTALL --library=$HOME/packages package.tar.gz",
    "cd ..; R CMD INSTALL -l ./r-library package.tar.gz",
    "Rscript -e \"install.packages('limma', lib='../shared')\"",
    "Rscript -e \"install.packages('limma', lib=file.path('/shared'))\"",
    "Rscript -e \"install.packages('limma', lib=.rlib)\"",
    "Rscript -e \"install.packages('a', lib='./local'); install.packages('b')\"",
])
def test_environment_owner_rejects_shared_ambiguous_or_escaping_destinations(tmp_path, command):
    assert "own workspace" in denial(tmp_path, command)


def test_environment_owner_uses_shell_cwd_and_explicit_absolute_paths(tmp_path):
    local = tmp_path / "packages"
    assert denial(tmp_path, f'cd ..; pip install --target "{local}" scanpy') is None
    assert denial(tmp_path, f'"{tmp_path}/.venv/Scripts/python.exe" -m pip install scanpy') is None
    assert denial(tmp_path, r"& '.\.venv\Scripts\python.exe' -m pip install scanpy", tool="PowerShell") is None


@pytest.mark.parametrize("command", [
    r"& 'C:\runner\env\Scripts\python.exe' -m pip install scanpy",
    "if ($true) { pip install scanpy }",
    "& $installer install scanpy",
])
def test_powershell_unknown_and_compound_installs_fail_closed(tmp_path, command):
    assert denial(tmp_path, command, tool="PowerShell")


@pytest.mark.parametrize("path", [
    r"C:\runner\env\Scripts\python.exe",
    "C:/runner/env/Scripts/python.exe",
    r"C:runner\env\Scripts\python.exe",
    r"\\server\share\env\Scripts\python.exe",
    "//server/share/env/Scripts/python.exe",
])
def test_foreign_windows_executable_spellings_are_never_task_relative(tmp_path, path):
    assert denial(tmp_path, f'"{path}" -m pip install scanpy')


@pytest.mark.parametrize("path", [r"C:\shared", "C:/shared", r"C:shared", r"\\server\share\env"])
def test_foreign_windows_targets_are_rejected_for_all_supported_managers(tmp_path, path):
    for command in (f'pip install --target "{path}" scanpy', f'conda install -p "{path}" scanpy',
                    f'uv pip --python "{path}" install scanpy', f'R CMD INSTALL --library="{path}" package.tar.gz'):
        assert denial(tmp_path, command)


@pytest.mark.parametrize("command", [
    "python script.py --out outputs/x.tsv", "Rscript analysis.R", "ls outputs", "Get-Content notes.md",
    'echo "pip install scanpy"', "command echo pip install scanpy", "env python script.py",
    "command -v python", "if true; then echo install; fi", "cd install; ls",
])
def test_plain_noninstall_commands_remain_allowed(tmp_path, command):
    assert denial(tmp_path, command) is None


def test_consumers_still_require_their_own_fixed_library(tmp_path):
    assert denial(tmp_path, "pip install --target ./.pylib scanpy", environment_step=False) is None
    assert denial(tmp_path, "pip install --target ./other scanpy", environment_step=False)
    assert denial(tmp_path, "cd ..; pip install --target ./.pylib scanpy", environment_step=False)
    assert denial(tmp_path, "cd nested; Rscript -e \"install.packages('a', lib='./.rlib')\"",
                  environment_step=False)


@pytest.mark.parametrize("command", [
    "R CMD INSTALL -l ./.rlib package.tar.gz",
    "R CMD INSTALL --library=./.rlib package.tar.gz",
    "command R CMD INSTALL -l ./.rlib --library=./.rlib package.tar.gz",
])
def test_r_cmd_consumers_can_install_only_into_their_fixed_library(tmp_path, command):
    assert denial(tmp_path, command, environment_step=False) is None


@pytest.mark.parametrize("command", [
    "R CMD INSTALL package.tar.gz",
    "R CMD INSTALL -l ./other package.tar.gz",
    "R CMD INSTALL -l ./.rlib --library=./other package.tar.gz",
    "cd nested; R CMD INSTALL -l ./.rlib package.tar.gz",
    "R CMD INSTALL --library=../other/.rlib package.tar.gz",
])
def test_r_cmd_consumer_shared_and_redirected_libraries_are_denied(tmp_path, command):
    assert denial(tmp_path, command, environment_step=False)


def test_r_cmd_explicit_absolute_local_libraries_and_powershell_call_operator(tmp_path):
    assert denial(tmp_path, f'cd ..; R CMD INSTALL --library="{tmp_path}/r library" package.tar.gz') is None
    assert denial(tmp_path, r"& R.exe CMD INSTALL -l .\r-library package.tar.gz", tool="PowerShell") is None


def _directory_link(link, target):
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True, text=True)
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("folder,command,owner", [
    (".venv", "./.venv/bin/python -m pip install scanpy", True),
    ("packages", "pip install --target ./packages scanpy", True),
    (".pylib", "pip install --target ./.pylib scanpy", False),
    (".rlib", "Rscript -e \"install.packages('a', lib='./.rlib')\"", False),
    ("r-library", "R CMD INSTALL -l ./r-library package.tar.gz", True),
    (".rlib", "R CMD INSTALL --library=./.rlib package.tar.gz", False),
])
def test_directory_links_cannot_redirect_installations_outside_the_step(tmp_path, folder, command, owner):
    workspace = tmp_path / "step"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    _directory_link(workspace / folder, outside)
    try:
        assert denial(workspace, command, environment_step=owner)
    finally:
        # Remove just the link; pytest's temp cleanup must never traverse an outside junction.
        if os.name == "nt":
            (workspace / folder).rmdir()
        else:
            (workspace / folder).unlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv interpreters normally use a final executable symlink")
def test_posix_venv_python_binary_link_does_not_escape_the_local_environment(tmp_path):
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").symlink_to("/usr/bin/python3")
    assert denial(tmp_path, "./.venv/bin/python -m pip install scanpy")
    (bin_dir.parent / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    assert denial(tmp_path, "./.venv/bin/python -m pip install scanpy") is None


def test_unprotected_requests_and_non_shell_tools_keep_their_existing_behavior(tmp_path):
    assert denial(tmp_path, "pip install scanpy", protected=False) is None
    assert denial(tmp_path, "pip install scanpy", tool="Read") is None


@pytest.mark.parametrize("command", ["echo '$(pip install scanpy)'", 'echo "pip install scanpy"',
                                     "printf '%s' '`pip install scanpy`'"])
def test_literal_installation_instructions_are_data(tmp_path, command):
    assert denial(tmp_path, command) is None


@pytest.mark.parametrize("environment_step", [True, False])
@pytest.mark.parametrize("command", [
    "pip ${ACTION:-install} scanpy",
    'pip "$ACTION" scanpy',
    'pip "$env:ACTION" scanpy',
    'pip "$(get-action)" scanpy',
    'pip `get-action` scanpy',
    'python -m "$MODULE" install scanpy',
    'python -m pip "$ACTION" scanpy',
    'uv pip "$ACTION" scanpy',
    'conda "$ACTION" -p ./conda-env scanpy',
    'R "$MODE" INSTALL -l ./r-library package.tar.gz',
    '"$PIP" install --target ./.pylib scanpy',
    '"$PY" -m pip install --target ./.pylib scanpy',
])
def test_nonliteral_installer_selectors_fail_closed(tmp_path, command, environment_step):
    assert denial(tmp_path, command, environment_step=environment_step)


@pytest.mark.parametrize("command,environment_step", [
    ("(cd nested; true); pip install --target .. scanpy", True),
    ("{ cd nested; true; }; pip install --target .. scanpy", True),
    ("(cd nested; true); conda install -p .. scanpy", True),
    ("(cd nested; true); uv pip install --python ../python scanpy", True),
    ("(cd nested; true); pip install --target ../.pylib scanpy", False),
    ("{ cd nested; true; }; pip install --target ../.pylib scanpy", False),
    ("(cd nested; true); R CMD INSTALL -l ../.rlib package.tar.gz", False),
])
def test_group_directory_changes_do_not_escape_their_scope(tmp_path, command, environment_step):
    assert denial(tmp_path, command, environment_step=environment_step)


@pytest.mark.parametrize("environment_step,target", [
    (True, "./packages"),
    (False, "./.pylib"),
])
def test_brace_group_directory_changes_persist_in_the_current_shell(tmp_path, environment_step, target):
    command = f"{{ cd ..; true; }}; pip install --target {target} scanpy"
    assert denial(tmp_path, command, environment_step=environment_step)


@pytest.mark.parametrize("environment_step,target", [
    (True, "./packages"),
    (False, "./.pylib"),
])
def test_subshell_directory_changes_are_restored(tmp_path, environment_step, target):
    command = f"(cd ..; true); pip install --target {target} scanpy"
    assert denial(tmp_path, command, environment_step=environment_step) is None


@pytest.mark.parametrize("environment_step,library,template", [
    (True, "packages", 'pip install --target "{target}" scanpy'),
    (True, "conda-env", 'conda install -p "{target}" scanpy'),
    (True, ".venv/python", 'uv pip install --python "{target}" scanpy'),
    (False, ".pylib", 'pip install --target "{target}" scanpy'),
    (False, ".rlib", 'R CMD INSTALL -l "{target}" package.tar.gz'),
])
def test_group_local_absolute_targets_remain_allowed(tmp_path, environment_step, library, template):
    target = tmp_path / library
    command = f'(cd nested; {template.format(target=target)})'
    assert denial(tmp_path, command, environment_step=environment_step) is None


@pytest.mark.parametrize("environment_step", [True, False])
@pytest.mark.parametrize("command", [
    "bash -c 'pip install scanpy'",
    "sh -c 'pip install scanpy'",
    "zsh -c 'pip install scanpy'",
    "dash -c 'pip install scanpy'",
    "pwsh -Command 'pip install scanpy'",
    "pwsh -Command pip install scanpy",
    "powershell -c 'pip install scanpy'",
    "cmd /c 'pip install scanpy'",
    "cmd /c pip install scanpy",
    "cmd /k 'pip install scanpy'",
    "powershell -EncodedCommand cABpAHAA",
    "bash -c \"$SETUP; pip install scanpy\"",
    "cmd /c cmd /c cmd /c cmd /c pip install scanpy",
])
def test_nested_shell_installations_fail_closed(tmp_path, command, environment_step):
    assert denial(tmp_path, command, environment_step=environment_step)


@pytest.mark.parametrize("environment_step,command", [
    (True, "bash -c '$0 \"$@\"' pip install --target /tmp/out scanpy"),
    (False, "bash -c '\"$1\" \"${@:2}\"' ignored pip install --target ./other scanpy"),
])
def test_dynamic_nested_shell_body_checks_positional_arguments(tmp_path, environment_step, command):
    assert denial(tmp_path, command, environment_step=environment_step)


@pytest.mark.parametrize("environment_step", [True, False])
def test_noninstall_nested_shell_body_remains_allowed(tmp_path, environment_step):
    assert denial(tmp_path, "bash -c 'python script.py'", environment_step=environment_step) is None


@pytest.mark.parametrize("environment_step", [True, False])
@pytest.mark.parametrize("tool,command", [
    ("Bash", "Rscript -e 'print(df$column)'"),
    ("Bash", r'''Rscript -e "print(df\$column)"'''),
    ("Bash", '''python -c "print('$x')"'''),
    ("Bash", r"python -c 'print(\"$x\")'"),
    ("Bash", "awk '{print $1}' data.tsv"),
    ("Bash", "sed 's/$//' input.tsv"),
    ("Bash", r"printf '%s\n' \$HOME"),
    ("PowerShell", "Rscript -e 'print(df$column)'"),
    ("PowerShell", "python -c 'print(\"$x\")'"),
    ("PowerShell", "Write-Output `$HOME"),
])
def test_noninstall_data_dollars_do_not_become_dynamic_installers(tmp_path, environment_step, tool, command):
    assert denial(tmp_path, command, environment_step=environment_step, tool=tool) is None


def test_nested_shell_local_install_destinations_remain_allowed(tmp_path):
    assert denial(tmp_path, "bash -c 'pip install --target ./packages scanpy'") is None
    assert denial(tmp_path, "bash -c 'pip install --target ./.pylib scanpy'", environment_step=False) is None
