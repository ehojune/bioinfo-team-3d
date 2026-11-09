"""Site-term extraction of the #1331 trial judge: no ordinary words from ssh options or Windows drive letters."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import judge_1331 as j  # noqa: E402

ARGV = ["ssh", "-tt", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes",
        "-o", "HostKeyAlias=gw", "-o", "UserKnownHostsFile=C:/Users/me/.ssh/known", "-p", "13000", "alice@127.0.0.1"]


def test_connect_argv_gives_the_login_name_only():
    assert j.argv_terms(ARGV) == {"alice"}
    assert j.argv_terms(["ssh", "-J", "jump.site", "bob@login.site"]) == {"jump.site", "bob", "login.site"}


def test_a_profile_connect_argv_is_not_split_into_option_words():
    class Profile(SimpleNamespace):
        model_fields = {"connect_argv": None, "queue": None, "remote_workdir": None}

    nodes = set()
    terms = j.model_terms(Profile(connect_argv=ARGV, queue="part1", remote_workdir="/scratch/lab/me"), nodes)
    assert terms == {"part1", "/scratch/lab/me"}
    assert not {"yes", "BatchMode", "ConnectTimeout", "13000"} & terms


def test_windows_paths_stay_whole_and_short_identifiers_stay():
    found = j.value_terms(["C:/Users/me/x", "--config=D:/lab/cfg", "q", "n1"])
    assert found == {"C:/Users/me/x", "D:/lab/cfg", "q", "n1"}
