"""Payload de falha para o front via S3/OpenSearch."""
from app.services.failure_result import (
    build_failure_transcription_payload,
    publish_failure_json_to_s3,
)


def test_build_failure_has_non_empty_speakers():
    p = build_failure_transcription_payload("Timeout 900s")
    ta = p["transcricaoAudio"]
    assert ta["speakers"]
    assert ta["speakers"][0]["name"] == "system"
    assert ta["speakers"][0]["text"].startswith("[Transcrição indisponível]")
    assert "Timeout" in ta["speakers"][0]["text"]
    assert ta["processing_status"] == "FAILED"
    assert ta["error"] == "Timeout 900s"


def test_build_failure_default_message_when_empty_error():
    p = build_failure_transcription_payload("  ")
    ta = p["transcricaoAudio"]
    assert ta["processing_status"] == "FAILED"
    assert ta["speakers"][0]["text"].startswith("[Transcrição indisponível]")
    assert ta["error"]


def test_publish_failure_calls_upload(tmp_path, monkeypatch):
    uploaded = {}

    def fake_upload(local, dest):
        uploaded["local"] = local
        uploaded["dest"] = dest
        with open(local, encoding="utf-8") as f:
            uploaded["body"] = f.read()

    publish_failure_json_to_s3(
        destination_link="s3://bucket/transcriptions/job1.json",
        error="boom",
        upload_fn=fake_upload,
    )
    assert uploaded["dest"] == "s3://bucket/transcriptions/job1.json"
    assert "boom" in uploaded["body"]
