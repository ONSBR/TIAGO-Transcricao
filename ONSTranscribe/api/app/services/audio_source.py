"""Validação do path de áudio de entrada (evita reprocessar JSON de saída)."""
from __future__ import annotations

import os
from urllib.parse import unquote, urlparse

_AUDIO_EXTENSIONS = frozenset(
    {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".webm", ".aac", ".wma"}
)


class InvalidAudioSourceError(ValueError):
    """Source_link não aponta para áudio processável."""


def _path_from_source(s3_source_link: str) -> str:
    raw = (s3_source_link or "").strip()
    if not raw:
        return ""
    if raw.startswith("s3://"):
        parsed = urlparse(raw)
        return unquote(parsed.path or "")
    return unquote(raw.split("?", 1)[0])


def validate_audio_source_link(s3_source_link: str) -> None:
    """Rejeita JSON de saída / prefixo transcriptions / extensão não-áudio.

    Em DEV vimos reprocesso de ``.../transcriptions/<job>.wav.json`` como se
    fosse áudio (torchcodec Invalid data). ``splitext`` em ``.wav.json`` vira
    job_id ``*.wav`` e mascara a falha.
    """
    path = _path_from_source(s3_source_link)
    if not path:
        raise InvalidAudioSourceError("source_link vazio")

    lower = path.lower()
    if "/transcriptions/" in lower:
        raise InvalidAudioSourceError(
            f"source_link aponta para pasta de saída (transcriptions): {s3_source_link}"
        )
    if lower.endswith(".json"):
        raise InvalidAudioSourceError(
            f"source_link é JSON, não áudio: {s3_source_link}"
        )

    ext = os.path.splitext(lower)[1]
    if ext not in _AUDIO_EXTENSIONS:
        raise InvalidAudioSourceError(
            f"extensão de áudio não suportada ({ext or 'sem extensão'}): {s3_source_link}"
        )


# Cabeçalho RIFF/WAV mínimo ~44 bytes; abaixo disso (ou só header) não há PCM útil.
_MIN_AUDIO_BYTES = 1024


def validate_local_audio_file(path: str, *, min_bytes: int = _MIN_AUDIO_BYTES) -> None:
    """Rejeita arquivo local vazio, só header WAV ou ausente (HOM: 44 B via SGW)."""
    if not path or not os.path.isfile(path):
        raise InvalidAudioSourceError(f"arquivo de áudio ausente no disco: {path}")
    size = os.path.getsize(path)
    if size <= 44:
        raise InvalidAudioSourceError(
            f"arquivo de áudio vazio ou só cabeçalho WAV ({size} bytes)"
        )
    if size < min_bytes:
        raise InvalidAudioSourceError(
            f"arquivo de áudio muito pequeno para processar ({size} bytes)"
        )


def user_facing_transcription_error(exc: BaseException) -> str:
    """Mensagem curta e específica para DDB/S3/front (sem stack)."""
    if isinstance(exc, InvalidAudioSourceError):
        return f"Source de áudio inválido: {exc}"
    text = str(exc).strip()
    if not text:
        return f"Erro na transcrição ({type(exc).__name__})."
    # Evita despejar blobs enormes no JSON de falha.
    if len(text) > 400:
        text = text[:400] + "…"
    return f"Erro na transcrição: {text}"
