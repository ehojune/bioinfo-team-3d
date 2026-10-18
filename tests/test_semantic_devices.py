from labhq.research import semantic_devices
from labhq.settings import Settings


def _settings(tmp_path):
    settings = Settings()
    settings.gateway.state_dir = str(tmp_path / "state")
    settings.semantics = {"mode": "off"}
    return settings


def test_vocab_checklist_and_pack_failures_are_structured_and_path_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic_devices.output_vocab, "current", lambda: None)
    monkeypatch.setattr(semantic_devices, "configured_packs",
                        lambda settings: (_ for _ in ()).throw(ValueError("bad C:/private/pack.yaml")))

    record = semantic_devices.inspect(_settings(tmp_path), phase="cp1")

    paths = {row["path"] for row in record["errors"]}
    assert {"vocab/output_types", "vocab/topic_checklists", "research/packs"} <= paths
    assert record["vocabulary"]["status"] == record["checklist"]["status"] == record["packs"]["status"] == "error"
    assert "C:/private" not in str(record) and "<경로>" in str(record)


def test_healthy_devices_include_hashes_and_counts(tmp_path):
    record = semantic_devices.inspect(_settings(tmp_path), phase="doctor")

    assert len(record["vocabulary"]["sha256"]) == 64
    assert record["vocabulary"]["counts"]["keys"] > 0
    assert len(record["checklist"]["sha256"]) == 64 and record["checklist"]["items"] > 0
    assert record["packs"]["status"] == "ok"
