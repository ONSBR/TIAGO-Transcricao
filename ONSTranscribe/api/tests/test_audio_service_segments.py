"""Regressão: falha de ASR em segmentos não vira 'diarização sem segmentos'.

Em HOM, OOM no Whisper por trecho era engolido e o fallback full audio
logava 'Diarização não gerou segmentos' — mensagem falsa e piora a VRAM.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.services import audio_service as svc


def _seg(speaker="SPEAKER_00", start=0.0, end=1.0):
    return {
        "speaker": speaker,
        "startTime": start,
        "endTime": end,
        "audio": object(),
    }


@pytest.mark.unit
def test_transcrever_segmentos_sequential_returns_ok_and_errors():
    segs = [_seg("A", 0, 1), _seg("B", 1, 2)]
    pipe = MagicMock(side_effect=[{"text": "ola"}, RuntimeError("oom")])

    resultados, erros = svc.transcrever_segmentos(segs, pipe, max_workers=1)

    assert len(resultados) == 1
    assert resultados[0]["text"] == "ola"
    assert len(erros) == 1
    assert "oom" in str(erros[0])


@pytest.mark.unit
def test_transcrever_segmentos_all_fail_returns_empty_and_errors():
    segs = [_seg(), _seg(start=1, end=2)]
    pipe = MagicMock(side_effect=RuntimeError("cuda oom"))

    resultados, erros = svc.transcrever_segmentos(segs, pipe, max_workers=1)

    assert resultados == []
    assert len(erros) == 2


@pytest.mark.unit
def test_executar_sem_segmentos_diarizados_usa_fallback_completo():
    """Só quando a diarização devolve zero cortes é legítimo ir pro full audio."""
    fake_diar = MagicMock()
    fake_diar.speaker_diarization = object()
    full = [{"name": "spk_0", "startTime": 0.0, "endTime": 3.0, "text": "full", "audio": None}]

    with (
        patch.object(svc, "get_diarization_pipeline", return_value=MagicMock(return_value=fake_diar)),
        patch.object(svc, "segmenta_por_speaker", return_value=[]),
        patch.object(svc, "transcrever_audio_completo", return_value=full) as full_fn,
    ):
        out = svc.executar_diarizacao_e_transcricao("/tmp/a.wav", MagicMock())

    assert out == full
    full_fn.assert_called_once()


@pytest.mark.unit
def test_executar_todos_asr_falham_nao_mascara_como_sem_diarizacao():
    """Se houve segmentos e o ASR falhou em todos, propaga erro — sem fallback full."""
    fake_diar = MagicMock()
    fake_diar.speaker_diarization = object()
    segs = [_seg(), _seg(start=2, end=3)]

    with (
        patch.object(svc, "get_diarization_pipeline", return_value=MagicMock(return_value=fake_diar)),
        patch.object(svc, "segmenta_por_speaker", return_value=segs),
        patch.object(
            svc,
            "transcrever_segmentos",
            return_value=([], [RuntimeError("cuda oom")]),
        ),
        patch.object(svc, "transcrever_audio_completo") as full_fn,
    ):
        with pytest.raises(RuntimeError, match="ASR falhou em todos"):
            svc.executar_diarizacao_e_transcricao("/tmp/a.wav", MagicMock())

    full_fn.assert_not_called()


@pytest.mark.unit
def test_executar_asr_parcial_retorna_o_que_passou():
    fake_diar = MagicMock()
    fake_diar.speaker_diarization = object()
    segs = [_seg(), _seg(start=2, end=3)]
    ok = [{"name": "A", "startTime": 0.0, "endTime": 1.0, "text": "x", "audio": None}]

    with (
        patch.object(svc, "get_diarization_pipeline", return_value=MagicMock(return_value=fake_diar)),
        patch.object(svc, "segmenta_por_speaker", return_value=segs),
        patch.object(svc, "transcrever_segmentos", return_value=(ok, [RuntimeError("x")])),
        patch.object(svc, "transcrever_audio_completo") as full_fn,
    ):
        out = svc.executar_diarizacao_e_transcricao("/tmp/a.wav", MagicMock())

    assert out == ok
    full_fn.assert_not_called()
