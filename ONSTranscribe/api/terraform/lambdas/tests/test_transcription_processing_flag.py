"""Flag de enqueue: Parameter Store (runtime) vs OS only / OS + SQS."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HELPERS = (
    Path(__file__).resolve().parents[1] / "transcribe_audio_to_elastic_search"
)
sys.path.insert(0, str(_HELPERS))

from helpers.environment_helper import EnvironmentHelper  # noqa: E402
from helpers.transcription_status import (  # noqa: E402
    PAUSED_SPEAKER_TEXT,
    QUEUED_SPEAKER_TEXT,
    build_paused_transcricao_audio,
    build_queued_transcricao_audio,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, True),
        ("", True),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("off", False),
        ("no", False),
        ("lixo", True),  # valor desconhecido → default true (não pausa por engano)
    ],
)
def test_parse_processing_enabled(raw, expected):
    assert EnvironmentHelper.parse_processing_enabled(raw) is expected


def test_enabled_when_ssm_param_name_missing(monkeypatch):
    monkeypatch.delenv("TranscriptionProcessingEnabledSsmParameter", raising=False)
    assert EnvironmentHelper.is_transcription_processing_enabled() is True


def test_reads_ssm_false(monkeypatch):
    monkeypatch.setenv(
        "TranscriptionProcessingEnabledSsmParameter",
        "/ons-transcribe/hom/transcription_processing_enabled",
    )
    monkeypatch.setattr(
        EnvironmentHelper,
        "get_ssm_parameter_value",
        staticmethod(lambda name: "false"),
    )
    assert EnvironmentHelper.is_transcription_processing_enabled() is False


def test_reads_ssm_true(monkeypatch):
    monkeypatch.setenv(
        "TranscriptionProcessingEnabledSsmParameter",
        "/ons-transcribe/dev/transcription_processing_enabled",
    )
    monkeypatch.setattr(
        EnvironmentHelper,
        "get_ssm_parameter_value",
        staticmethod(lambda name: "true"),
    )
    assert EnvironmentHelper.is_transcription_processing_enabled() is True


def test_ssm_error_defaults_to_enabled(monkeypatch):
    """Falha de rede/IAM/VPC endpoint não desliga o pipeline sozinha."""
    monkeypatch.setenv(
        "TranscriptionProcessingEnabledSsmParameter",
        "/ons-transcribe/hom/transcription_processing_enabled",
    )

    def _raise(_name: str) -> str:
        raise RuntimeError("ssm unavailable")

    monkeypatch.setattr(
        EnvironmentHelper,
        "get_ssm_parameter_value",
        staticmethod(_raise),
    )
    assert EnvironmentHelper.is_transcription_processing_enabled() is True


def test_enqueue_gate_follows_ssm_flag(monkeypatch):
    """database_destination usa is_transcription_processing_enabled() como gate."""
    monkeypatch.setenv("TranscriptionProcessingEnabledSsmParameter", "/p")
    monkeypatch.setattr(
        EnvironmentHelper,
        "get_ssm_parameter_value",
        staticmethod(lambda _n: "false"),
    )
    assert EnvironmentHelper.is_transcription_processing_enabled() is False
    monkeypatch.setattr(
        EnvironmentHelper,
        "get_ssm_parameter_value",
        staticmethod(lambda _n: "true"),
    )
    assert EnvironmentHelper.is_transcription_processing_enabled() is True


def test_paused_payload_for_front():
    ta = build_paused_transcricao_audio()
    assert ta["processing_status"] == "PAUSED"
    assert ta["speakers"][0]["text"] == PAUSED_SPEAKER_TEXT
    assert ta["speakers"][0]["name"] == "system"


def test_queued_still_distinct_from_paused():
    assert build_queued_transcricao_audio()["processing_status"] == "QUEUED"
    assert build_queued_transcricao_audio()["speakers"][0]["text"] == QUEUED_SPEAKER_TEXT
    assert (
        build_paused_transcricao_audio()["speakers"][0]["text"]
        != build_queued_transcricao_audio()["speakers"][0]["text"]
    )
