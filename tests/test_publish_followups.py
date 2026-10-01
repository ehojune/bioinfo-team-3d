"""Follow-ups of PR #176 on the publish side (#180 #181 #183): what a project report or round record may still name
after `publish_clean`, the one cleaner for every text labhq posts."""

import json

from labhq.integrations.github import publish_clean
from labhq.settings import Settings


def _published(text: str, settings: Settings | None = None, requests=()) -> str:
    return publish_clean(text, settings or Settings(), list(requests))


# ---------------- #180: a webhook URL percent-encoded inside another URL ----------------

def test_a_percent_encoded_webhook_does_not_reach_a_published_text():
    text = ("a https://app.example/login?next=https%3A%2F%2Fhooks.slack.com%2Fservices%2FT0AAA%2FB0BBB%2Fsl4ckSECRET1"
            "&lang=ko "
            "b https://app.example/r?u=https%253A%252F%252Fdiscord.com%252Fapi%252Fwebhooks%252F123%252Fd1scordSECRET2 "
            "c see https%3a%2f%2fapi.telegram.org%2fbot123%3At3legramSECRET3 "
            "d https://lab.example/x?hook=hooks.slack.com%2Fworkflows%2FT0CCC%2Fsl4ckSECRET4")
    out = _published(text)
    for secret in ("sl4ckSECRET1", "T0AAA", "d1scordSECRET2", "t3legramSECRET3", "sl4ckSECRET4", "T0CCC"):
        assert secret not in out, (secret, out)
    assert "hooks.slack.com%2Fservices%2F<redacted-secret>&lang=ko" in out, "the outer URL's next parameter stays"
    assert "https://hooks.slack.com/services/<redacted-secret>" in _published(
        "https://hooks.slack.com/services/T0AAA/B0BBB/plainSECRET5"), "the plain form is hidden as before"


# ---------------- #181: a PI default repository on a self-hosted GitHub ----------------

def _with_default(kind: str, value: str) -> Settings:
    from labhq.intake import Reference

    settings = Settings()
    settings.pi_profile.references = [Reference(kind=kind, value=value)]
    return settings


def test_a_github_enterprise_default_hides_its_owner_and_repository_in_reports():
    settings = _with_default("url", "https://github.example.edu/hpc-lab/cohort-pipeline/tree/main")
    text = ("1 https://github.example.edu/hpc-lab/cohort-pipeline/blob/dev/README.md "
            "2 hpc-lab/cohort-pipeline "
            r"3 C:\src\hpc-lab\cohort-pipeline\run.py "
            "4 git@github.example.edu:hpc-lab/cohort-pipeline.git "
            "5 cloned cohort-pipeline here")
    out = _published(text, settings)
    assert "hpc-lab" not in out and "cohort-pipeline" not in out, out
    assert out.count("<private-reference>") >= 5, out


def test_a_forge_default_hides_its_identity_in_json_round_records():
    settings = _with_default("url", "https://gitlab.lab.org/genomics/variant-calls")
    record = json.dumps({"note": "see https://gitlab.lab.org/genomics/variant-calls and genomics/variant-calls"})
    out = _published(record, settings)
    assert "genomics" not in out and "variant-calls" not in out, out


def test_a_non_forge_default_url_keeps_its_path_words_outside_the_url():
    """A wiki page is not a repository: only its URL is hidden, not every `pi/notes` in the text."""
    settings = _with_default("url", "https://wiki.example.org/pi/notes")
    out = _published("the wiki https://wiki.example.org/pi/notes/today and pi/notes", settings)
    assert "wiki.example.org" not in out and out.endswith("and pi/notes"), out


# ---------------- #183: another account's home with a space or a JSON-escaped name ----------------

HOME_REFERENCE = [{"references": [{"kind": "path", "value": "~/refs/llm-wiki", "source": "request"}]}]


def test_a_home_reference_is_hidden_under_an_account_with_a_space_or_a_json_escaped_name():
    texts = {
        "space": "see C:" + chr(92) + "Users" + chr(92) + "Jane Doe" + chr(92) + "refs" + chr(92) + "llm-wiki" + chr(92)
                 + "a.md now",
        "posix space": "see /home/jane q doe/refs/llm-wiki/a.md now",
        "json": "see " + json.dumps({"p": "C:" + chr(92) + "Users" + chr(92) + "장지훈" + chr(92) + "refs" + chr(92)
                                         + "llm-wiki" + chr(92) + "a.md"}),
        "json slash": "see " + json.dumps({"p": "/home/장 지훈/refs/llm-wiki/a.md"}),
    }
    for name, text in texts.items():
        out = _published(text, requests=HOME_REFERENCE)
        assert "llm-wiki" not in out and "<reference-path>" in out, (name, out)
        for account in ("Jane", "jane", "uc7a5", "장지훈", "\uc7a5"):
            assert account not in out, (name, account, out)


def test_a_spaced_account_does_not_swallow_the_prose_before_a_reference():
    out = _published("files in /home/alice and then /refs/llm-wiki/a.md", requests=HOME_REFERENCE)
    assert out == "files in /home/alice and then /refs/llm-wiki/a.md", "the home is not a prefix of that path"
    out = _published("files in /home/alice and also /home/bob/refs/llm-wiki/a.md", requests=HOME_REFERENCE)
    assert out == "files in /home/alice and also <reference-path>/a.md", out


def test_the_home_mask_stays_linear_on_long_spaced_lines():
    import time

    n = 60_000
    lines = ["C:/Users/" + "a " * (n // 2), "/home/" + "a b c d " * (n // 8), ("/home/a b " * (n // 10)),
             "C:" + chr(92) + "Users" + (chr(92) + "u0041") * (n // 6)]
    started = time.perf_counter()
    for line in lines:
        _published(f"x {line} ~/refs/llm-wiki/a.md", requests=HOME_REFERENCE)
    assert time.perf_counter() - started < 5, "the account component must stay linear in the text length"
