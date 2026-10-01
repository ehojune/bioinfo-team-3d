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
