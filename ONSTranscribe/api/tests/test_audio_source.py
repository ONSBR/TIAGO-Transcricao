"""Regressão: não processar JSON de saída como se fosse áudio; rejeitar WAV vazio."""
import pytest

from app.services.audio_source import (
    InvalidAudioSourceError,
    user_facing_transcription_error,
    validate_audio_source_link,
    validate_local_audio_file,
)


@pytest.mark.unit
def test_accepts_wav_s3_uri():
    validate_audio_source_link(
        "s3://bucket/testes/oom-repro/2026-07-20/audios/salas/LOC1/foo.wav"
    )


@pytest.mark.unit
def test_rejects_transcriptions_json_output():
    with pytest.raises(InvalidAudioSourceError, match="transcriptions|JSON"):
        validate_audio_source_link(
            "s3://bucket/audios/salas/transcriptions/foo.wav.json"
        )


@pytest.mark.unit
def test_rejects_json_extension():
    with pytest.raises(InvalidAudioSourceError, match="JSON"):
        validate_audio_source_link("s3://bucket/path/foo.json")


@pytest.mark.unit
def test_rejects_empty():
    with pytest.raises(InvalidAudioSourceError):
        validate_audio_source_link("")


@pytest.mark.unit
def test_rejects_unknown_extension():
    with pytest.raises(InvalidAudioSourceError, match="extensão"):
        validate_audio_source_link("s3://bucket/path/foo.txt")


@pytest.mark.unit
def test_rejects_wav_header_only_44_bytes(tmp_path):
    p = tmp_path / "empty.wav"
    p.write_bytes(b"R" * 44)
    with pytest.raises(InvalidAudioSourceError, match="vazio|cabeçalho"):
        validate_local_audio_file(str(p))


@pytest.mark.unit
def test_rejects_missing_local_file(tmp_path):
    with pytest.raises(InvalidAudioSourceError, match="ausente"):
        validate_local_audio_file(str(tmp_path / "nope.wav"))


@pytest.mark.unit
def test_accepts_local_file_above_min(tmp_path):
    p = tmp_path / "ok.wav"
    p.write_bytes(b"x" * 2048)
    validate_local_audio_file(str(p))


@pytest.mark.unit
def test_user_facing_error_keeps_invalid_audio_detail():
    exc = InvalidAudioSourceError("arquivo de áudio vazio ou só cabeçalho WAV (44 bytes)")
    msg = user_facing_transcription_error(exc)
    assert "inválido" in msg.lower() or "Source" in msg
    assert "44 bytes" in msg


@pytest.mark.unit
def test_user_facing_error_includes_exception_text():
    msg = user_facing_transcription_error(RuntimeError("torchcodec Invalid data"))
    assert "torchcodec Invalid data" in msg
    assert msg.startswith("Erro na transcrição:")
