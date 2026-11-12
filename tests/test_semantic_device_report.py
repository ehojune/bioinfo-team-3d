"""No new semantic report; public safety contracts now exercise the retained publication path."""
from pathlib import Path
from labhq.request_bundle import build_request_bundle
from labhq.settings import Settings
from tests.test_request_bundle import pending_bundle_fixture


def test_legacy_report_setting_never_generates_a_new_report(tmp_path):
    settings, request, task = pending_bundle_fixture(tmp_path, "retired_report")
    settings = Settings.model_validate({**settings.model_dump(), "semantics": {"mode": "ab"},
                                       "research": {"semantic_report": True}})
    request['research_contract'] = {'schema_version': 1}
    bundle = Path(build_request_bundle(request, settings, tasks={"t": task})['path'])
    assert not (bundle / 'semantic_report.md').exists()
    assert not (bundle / 'semantic_report.json').exists()
    assert 'semantic_report' not in (bundle / 'README.md').read_text(encoding='utf-8')
