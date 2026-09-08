"""Placeholder de fila: front precisa de speakers não vazios no insert OS."""
from __future__ import annotations

import sys
from pathlib import Path

_HELPERS = (
    Path(__file__).resolve().parents[1] / "transcribe_audio_to_elastic_search"
)
sys.path.insert(0, str(_HELPERS))

from helpers.transcription_status import (  # noqa: E402
    PAUSED_SPEAKER_TEXT,
    QUEUED_SPEAKER_TEXT,
    build_paused_transcricao_audio,
    build_queued_transcricao_audio,
)


def test_queued_payload_has_system_speaker_for_front():
    ta = build_queued_transcricao_audio()
    assert ta["processing_status"] == "QUEUED"
    assert ta["speakers"]
    sp = ta["speakers"][0]
    assert sp["name"] == "system"
    assert sp["text"] == QUEUED_SPEAKER_TEXT
    assert isinstance(sp["startTime"], float)
    assert isinstance(sp["endTime"], float)


def test_queued_payload_not_empty_when_user_opens_player():
    """Regressão: transcricaoAudio null → tela sem texto e dúvida se processou."""
    ta = build_queued_transcricao_audio()
    assert ta.get("speakers")
    assert any((s.get("text") or "").strip() for s in ta["speakers"])


def test_paused_payload_has_system_speaker_for_front():
    ta = build_paused_transcricao_audio()
    assert ta["processing_status"] == "PAUSED"
    assert ta["speakers"][0]["text"] == PAUSED_SPEAKER_TEXT
