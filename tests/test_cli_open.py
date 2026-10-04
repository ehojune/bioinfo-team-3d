"""`labhq open`: the web office opens signed in, and the client token never reaches the terminal (PI visit 2026-10-04)."""
from urllib.parse import parse_qs, urlsplit

from labhq.cli import _open_web_office, _terminal_report
from labhq.settings import Settings


def _settings(token="s3cret/+token"):
    return Settings.model_validate({"gateway": {"client_token": token, "url": "ws://127.0.0.1:8787"}})


def test_open_puts_the_token_in_the_address_only():
    opened = []
    message = _open_web_office(_settings(), "config/labhq.yaml", opener=lambda url: opened.append(url) or True)
    (url,) = opened
    assert url.startswith("http://127.0.0.1:8787/?token=")
    assert parse_qs(urlsplit(url).query)["token"] == ["s3cret/+token"]
    assert "s3cret" not in message and "http://127.0.0.1:8787/" in message


def test_open_3d_and_a_browser_that_does_not_open():
    opened = []
    _open_web_office(_settings(), None, three_d=True, opener=lambda url: opened.append(url) or True)
    assert opened[0].startswith("http://127.0.0.1:8787/3d?token=")
    message = _open_web_office(_settings(), "C:/lab/labhq.yaml", opener=lambda url: False)
    assert "C:/lab/labhq.yaml" in message and "gateway.client_token" in message and "s3cret" not in message


def test_terminal_output_keeps_the_pi_report_and_labels_the_execution_record():
    output = _terminal_report({"report": "PI body", "report_appendix": "step status"})
    assert output == "PI body\n\n--- 실행 기록 ---\nstep status"
