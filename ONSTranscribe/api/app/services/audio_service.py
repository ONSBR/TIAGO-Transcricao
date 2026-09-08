"""Serviços de processamento de áudio: segmentação, conversão e transcrição paralela."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import librosa
import numpy as np
import resampy
import torch
from pydub import AudioSegment
from scipy.io import wavfile

from app.config import config
from app.services.model_manager import get_diarization_pipeline

logger = logging.getLogger(__name__)


def _empty_cuda_cache() -> None:
    """Devolve ao driver a VRAM em cache do PyTorch (best-effort)."""
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def read_wav_file(
    file_path: str, target_sample_rate: int = config.transcription.target_sample_rate
) -> tuple[int, np.ndarray] | None:
    """Lê um arquivo WAV, converte para mono e reamostra se necessário.

    Args:
        file_path: Caminho para o arquivo WAV.
        target_sample_rate: Taxa de amostragem de destino em Hz.

    Returns:
        Tupla (sample_rate, data) ou None se o áudio for muito curto para reamostrar.
    """
    sample_rate, data = wavfile.read(file_path)

    if len(data.shape) > 1:
        data = np.mean(data, axis=1)

    if sample_rate != target_sample_rate:
        if len(data) >= 2:
            data = resampy.resample(data, sample_rate, target_sample_rate)
        else:
            return None

    return target_sample_rate, data


def audiosegment_to_ndarray(
    audio_segment: AudioSegment,
    target_sample_rate: int = config.transcription.target_sample_rate,
) -> np.ndarray:
    """Converte um AudioSegment para numpy.ndarray compatível com Whisper.

    Args:
        audio_segment: Segmento de áudio pydub.
        target_sample_rate: Taxa de amostragem de destino.

    Returns:
        Array float32 normalizado entre -1 e 1.
    """
    audio = (
        audio_segment.set_frame_rate(target_sample_rate)
        .set_channels(1)
        .set_sample_width(2)
    )
    return np.array(audio.get_array_of_samples()).astype(np.float32) / 32768.0


def segmenta_por_speaker(
    file_path: str,
    diarization,
    min_duration: float = config.transcription.min_segment_duration,
) -> list[dict]:
    """Corta o áudio em segmentos por speaker, agrupando falas consecutivas.

    Args:
        file_path: Caminho do arquivo WAV.
        diarization: Resultado da diarização pyannote.
        min_duration: Duração mínima em segundos para incluir um segmento.

    Returns:
        Lista de dicts com chaves 'speaker', 'startTime', 'endTime' e 'audio'.
    """
    audio = AudioSegment.from_wav(file_path)

    segments: list[dict] = []
    current_speaker = None
    current_start = None
    current_end = None

    for turn in diarization.itertracks(yield_label=True):
        start, end, speaker = turn[0].start, turn[0].end, turn[2]

        if speaker == current_speaker:
            current_end = end
        else:
            if current_speaker is not None and (current_end - current_start) >= min_duration:
                segment_audio = audio[int(current_start * 1000): int(current_end * 1000)]
                segments.append({
                    "speaker": current_speaker,
                    "startTime": current_start,
                    "endTime": current_end,
                    "audio": audiosegment_to_ndarray(segment_audio),
                })
            current_speaker = speaker
            current_start = start
            current_end = end

    if current_speaker is not None and (current_end - current_start) >= min_duration:
        segment_audio = audio[int(current_start * 1000): int(current_end * 1000)]
        segments.append({
            "speaker": current_speaker,
            "startTime": current_start,
            "endTime": current_end,
            "audio": audiosegment_to_ndarray(segment_audio),
        })

    return segments


def _transcrever_um_segmento(seg: dict, asr_pipeline) -> dict:
    # no_grad dentro da função porque ela roda nas threads do ThreadPoolExecutor;
    # o contexto do torch é thread-local e não herda um `with` da thread principal.
    with torch.no_grad():
        resultado = asr_pipeline(seg["audio"])
    return {
        "name": seg["speaker"],
        "startTime": seg["startTime"],
        "endTime": seg["endTime"],
        "text": resultado["text"],
        "audio": seg["audio"],
    }


def transcrever_segmentos(
    segments: list[dict],
    asr_pipeline,
    max_workers: int = config.transcription.max_workers,
) -> tuple[list[dict], list[BaseException]]:
    """Transcreve segmentos diarizados com Whisper.

    Default sequencial (max_workers=1) para não empilhar forwards Whisper na GPU.
    max_workers>1 usa ThreadPoolExecutor (só com margem de VRAM comprovada).

    Returns:
        (resultados ok ordenados por startTime, exceções dos trechos que falharam).
    """
    resultados: list[dict] = []
    erros: list[BaseException] = []

    if not segments:
        return [], []

    workers = max(1, int(max_workers))

    if workers == 1:
        for seg in segments:
            try:
                resultados.append(_transcrever_um_segmento(seg, asr_pipeline))
            except Exception as exc:
                erros.append(exc)
                logger.exception(
                    "Erro ao transcrever segmento de %s entre %.2fs e %.2fs",
                    seg["speaker"],
                    seg["startTime"],
                    seg["endTime"],
                )
        return sorted(resultados, key=lambda x: x["startTime"]), erros

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_transcrever_um_segmento, seg, asr_pipeline): seg
            for seg in segments
        }
        for future in as_completed(futures):
            seg = futures[future]
            try:
                resultados.append(future.result())
            except Exception as exc:
                erros.append(exc)
                logger.exception(
                    "Erro ao transcrever segmento de %s entre %.2fs e %.2fs",
                    seg["speaker"],
                    seg["startTime"],
                    seg["endTime"],
                )

    return sorted(resultados, key=lambda x: x["startTime"]), erros


# Nome antigo usado em logs/código legado; delega para a API nova.
def transcrever_segmentos_parallel(
    segments: list[dict],
    asr_pipeline,
    max_workers: int = config.transcription.max_workers,
) -> list[dict]:
    resultados, _erros = transcrever_segmentos(segments, asr_pipeline, max_workers)
    return resultados


def transcrever_audio_completo(file_path: str, asr_pipeline) -> list[dict]:
    """Fallback: transcreve o áudio inteiro como um único segmento.

    Args:
        file_path: Caminho do arquivo de áudio.
        asr_pipeline: Pipeline ASR do Whisper.

    Returns:
        Lista com um único segmento cobrindo toda a duração do áudio.
    """
    with torch.no_grad():
        raw = asr_pipeline(file_path, return_timestamps=True)
    duracao = librosa.get_duration(filename=file_path)
    return [{
        "name": "spk_0",
        "startTime": 0.0,
        "endTime": float(duracao),
        "text": raw.get("text", ""),
        "audio": None,
    }]


def executar_diarizacao_e_transcricao(file_path: str, asr_pipeline) -> list[dict]:
    """Diariza e transcreve por segmento; fallback full só se não houver cortes.

    Se a diarização gerou trechos e o ASR falhou em todos, propaga erro em vez de
    logar 'sem segmentos' e tentar o áudio inteiro (máscara OOM e piora VRAM).
    """
    diarization_pipeline = get_diarization_pipeline()
    with torch.no_grad():
        diarization = diarization_pipeline(
            file_path, num_speakers=config.transcription.diarization_num_speakers
        )

    # pyannote 4.0 retorna um DiarizeOutput (wrapper); o Annotation com .itertracks
    # fica em .speaker_diarization (no 3.x o pipeline devolvia o Annotation direto).
    segments = segmenta_por_speaker(file_path, diarization.speaker_diarization)

    # Libera a VRAM da diarização ANTES da fase de transcrição, reduzindo o pico de
    # memória simultânea na GPU (diarização + Whisper). Sem isso, os tensores da
    # diarização ficam retidos até o fim do job e somam com os do Whisper -> CUDA OOM.
    del diarization
    _empty_cuda_cache()

    if not segments:
        logger.warning(
            "Diarização não gerou segmentos (zero cortes); transcrevendo áudio completo."
        )
        return transcrever_audio_completo(file_path, asr_pipeline)

    resultados, erros = transcrever_segmentos(segments, asr_pipeline)

    if not resultados:
        n = len(segments)
        n_err = len(erros)
        logger.error(
            "ASR falhou em todos os %d segmentos diarizados (%d erros); "
            "não mascarar como diarização vazia nem forçar full-audio.",
            n,
            n_err,
        )
        cause = erros[-1] if erros else None
        msg = (
            f"ASR falhou em todos os {n} segmentos diarizados "
            f"({n_err} erro(s) capturado(s))"
        )
        raise RuntimeError(msg) from cause

    if erros:
        logger.warning(
            "ASR parcial: %d segmento(s) ok, %d falha(s) (trechos omitidos).",
            len(resultados),
            len(erros),
        )

    return resultados
