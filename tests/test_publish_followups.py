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
