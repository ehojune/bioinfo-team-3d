import logging

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

    codes = {row["code"] for row in record["errors"]}
    assert {"vocabulary_load_failed", "checklist_load_failed", "pack_load_failed"} <= codes
    assert record["vocabulary"]["status"] == record["checklist"]["status"] == record["packs"]["status"] == "error"
    assert "C:/private" not in str(record)
    assert all(set(row) == {"code", "device", "stage", "at", "message"} for row in record["errors"])


def test_exception_body_never_enters_the_public_device_record(tmp_path, monkeypatch):
    private_text = "DUA participant CASE-SECRET-491"
    monkeypatch.setattr(semantic_devices, "configured_packs",
                        lambda settings: (_ for _ in ()).throw(ValueError(private_text)))

    record = semantic_devices.inspect(_settings(tmp_path), phase="cp1")

    assert private_text not in str(record)
    assert record["errors"][-1] == {
        "code": "pack_load_failed",
        "device": "research pack",
        "stage": "load",
        "at": record["errors"][-1]["at"],
        "message": "research pack을 불러오지 못했습니다.",
    }


def test_exception_log_keeps_only_public_code_and_class(caplog):
    private_text = "DUA participant CASE-SECRET-491"

    with caplog.at_level(logging.WARNING, logger="labhq.semantic_devices"):
        semantic_devices.log_exception("pack_load_failed", ValueError(private_text))

    row = caplog.records[-1]
    assert row.getMessage() == "meaning device failure pack_load_failed (ValueError)"
    assert row.exc_info is None
    assert private_text not in caplog.text


def test_healthy_devices_include_hashes_and_counts(tmp_path):
    record = semantic_devices.inspect(_settings(tmp_path), phase="doctor")

    assert len(record["vocabulary"]["sha256"]) == 64
    assert record["vocabulary"]["counts"]["keys"] > 0
    assert len(record["checklist"]["sha256"]) == 64 and record["checklist"]["items"] > 0
    assert record["packs"]["status"] == "ok"
