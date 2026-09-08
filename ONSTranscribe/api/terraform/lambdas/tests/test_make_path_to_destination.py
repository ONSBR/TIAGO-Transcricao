"""Roteamento S3→destino: transcriptions/*.json nunca vira create_job de áudio.

Regressão: `*.wav.json` sob transcriptions continha a substring "wav" e caía em
DATABASE, re-enfileirando o JSON na fila de transcrição (poison).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# helpers do Lambda vivem fora de api/app — coloca no path do pacote lambda
_HELPERS = (
    Path(__file__).resolve().parents[1]
    / "transcribe_audio_to_elastic_search"
)
sys.path.insert(0, str(_HELPERS))

from helpers.entities import DestinationEnum  # noqa: E402
from helpers.utils_helper import make_path_to_destination  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("KeyPath", "audios/salas/")
    monkeypatch.setenv(
        "TranscriptionsKeyPath", "audios/salas/transcriptions/"
    )
    monkeypatch.setenv(
        "VocabulariesKeyPath", "audios/salas/transcriptions/vocabularies/"
    )
    monkeypatch.setenv("MediaFormat", "wav")


def test_json_em_transcriptions_e_transcribe_nao_database():
    key = (
        "audios/salas/transcriptions/"
        "20260716_190728_8052_CENTRO1-GERACAO-2_82_I_AGENTE1_EXEMPLO1.json"
    )
    assert make_path_to_destination(key) == DestinationEnum.TRANSCRIBE


def test_wav_json_em_transcriptions_nunca_database():
    """Poison: destino errado *.wav.json ainda deve ir para TRANSCRIBE (indexar),
    nunca re-create_job como se fosse áudio novo.
    """
    key = (
        "audios/salas/transcriptions/"
        "20260716_190728_8052_CENTRO1-GERACAO-2_82_I_AGENTE1_EXEMPLO1.wav.json"
    )
    assert make_path_to_destination(key) == DestinationEnum.TRANSCRIBE


def test_wav_em_control_room_e_database():
    key = (
        "audios/salas/LOC1/"
        "20260716_190728_8052_CENTRO1-GERACAO-2_82_I_AGENTE1_EXEMPLO1.wav"
    )
    assert make_path_to_destination(key) == DestinationEnum.DATABASE


def test_substring_wav_no_meio_do_path_sem_sufixo_nao_e_audio():
    # não usar `"wav" in key` — exige sufixo real de mídia; path sem sufixo .wav
    key = "audios/salas/LOC1/not_audio.txt"
    assert make_path_to_destination(key) == DestinationEnum.NOWHERE
