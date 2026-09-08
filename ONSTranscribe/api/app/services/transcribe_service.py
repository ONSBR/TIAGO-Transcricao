"""Orquestrador do pipeline de transcrição de áudio."""
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from time import time

from app.config import config
from app.services.audio_service import executar_diarizacao_e_transcricao
from app.services.llm_cost_tracker import JobLlmUsageAccumulator
from app.services.llm_service import (
    get_audio_subject,
    get_key_entities,
    generate_summary,
    run_pipeline_beautify,
)
from app.services.model_manager import get_whisper_asr
from app.utils.text_utils import correct_common_names, preprocess_text

logger = logging.getLogger(__name__)

SEGMENT_SEPARATOR = " %$% "


def _sanitize_speaker_names(chunks: list[dict]) -> list[dict]:
    """Remove rótulos sintéticos de speaker ('spk_N') antes de enviar ao resumo.

    Args:
        chunks: Lista de segmentos com chave 'name'.

    Returns:
        Nova lista de segmentos com 'name' vazio quando era um rótulo 'spk_N'.
    """
    sanitized = []
    for chunk in chunks:
        name = chunk.get("name", "")
        new_chunk = dict(chunk)
        new_chunk["name"] = "" if re.match(r"^spk_\d+$", name) else name
        sanitized.append(new_chunk)
    return sanitized


def _extrair_metadados_paralelo(
    unified_chunks: list[dict],
    gravador: str = "",
    usage_acc: JobLlmUsageAccumulator | None = None,
):
    """Extrai entidades-chave, assunto e resumo em paralelo, com timeout.

    Args:
        unified_chunks: Segmentos unificados da transcrição.
        gravador: Identificação do gravador (desambiguação de instalações).
        usage_acc: Acumulador de uso LLM do job.

    Returns:
        Tupla (key_entities, subject, summary).
    """
    timeout = config.transcription.metadata_timeout_s
    with ThreadPoolExecutor(max_workers=3) as executor:
        t_meta = time()
        fut_entities = executor.submit(
            get_key_entities, unified_chunks, gravador=gravador, usage_acc=usage_acc
        )
        fut_subject = executor.submit(get_audio_subject, unified_chunks, usage_acc=usage_acc)
        fut_summary = executor.submit(
            generate_summary, _sanitize_speaker_names(unified_chunks), usage_acc=usage_acc
        )

        key_entities = fut_entities.result(timeout=timeout)
        subject = fut_subject.result(timeout=timeout)
        summary = fut_summary.result(timeout=timeout)
        logger.info("[timing] metadados paralelos: %.2fs", time() - t_meta)

    if isinstance(key_entities, str) and key_entities.startswith("Não foi possível"):
        logger.error("[get_key_entities] retornou erro: %s", key_entities)
    if isinstance(subject, str) and subject.startswith("Não foi possível"):
        logger.error("[get_audio_subject] retornou erro: %s", subject)

    return key_entities, subject, summary


def unificar_falas_consecutivas_por_speaker(data: dict) -> list[dict]:
    """Unifica falas consecutivas do mesmo speaker em um único bloco.

    Args:
        data: Dicionário com chave 'transcricaoAudio' contendo lista 'speakers'.

    Returns:
        Lista de falas unificadas, cada uma com 'name', 'startTime', 'endTime' e 'text'.
    """
    speakers = data["transcricaoAudio"]["speakers"]
    if not speakers:
        return []

    falas_unificadas: list[dict] = []
    falante_atual = speakers[0]["name"]
    inicio_atual = speakers[0]["startTime"]
    fim_atual = speakers[0]["endTime"]
    texto_atual = speakers[0]["text"]

    for bloco in speakers[1:]:
        if bloco["name"] == falante_atual:
            texto_atual += " " + bloco["text"]
            fim_atual = bloco["endTime"]
        else:
            falas_unificadas.append({
                "name": falante_atual,
                "startTime": inicio_atual,
                "endTime": fim_atual,
                "text": texto_atual,
            })
            falante_atual = bloco["name"]
            inicio_atual = bloco["startTime"]
            fim_atual = bloco["endTime"]
            texto_atual = bloco["text"]

    falas_unificadas.append({
        "name": falante_atual,
        "startTime": inicio_atual,
        "endTime": fim_atual,
        "text": texto_atual,
    })

    return falas_unificadas


def _build_speaker_map(resultados: list[dict]) -> dict[str, str]:
    """Mapeia labels internos de speaker para 'spk_0' e 'spk_1'.

    Args:
        resultados: Lista de segmentos com chave 'name'.

    Returns:
        Dicionário de mapeamento label -> spk_N.
    """
    speaker_map: dict[str, str] = {}
    if not resultados:
        return speaker_map

    primeiro_label = resultados[0].get("name")
    speaker_map[primeiro_label] = "spk_0"

    for r in resultados[1:]:
        label = r.get("name")
        if label != primeiro_label and label not in speaker_map:
            speaker_map[label] = "spk_1"
            break

    return speaker_map


def ressincronizar_transcricao(
    resultados: list[dict], pieces_beaultify: list[str]
) -> list[dict]:
    """Ressincroniza a transcrição beautificada com os metadados temporais da diarização.

    Args:
        resultados: Segmentos originais da diarização com 'name', 'startTime', 'endTime'.
        pieces_beaultify: Textos corrigidos pelo LLM, um por segmento.

    Returns:
        Lista de segmentos com texto beautificado e metadados temporais preservados.
        Em caso de mismatch, retorna os segmentos com texto original.
    """
    speaker_map = _build_speaker_map(resultados)

    try:
        if len(pieces_beaultify) != len(resultados):
            raise ValueError(
                f"Mismatch entre segmentos ({len(resultados)}) "
                f"e textos beautify ({len(pieces_beaultify)})"
            )

        resultados_beautify = [
            {
                "name": speaker_map.get(r.get("name"), "spk_0"),
                "startTime": r["startTime"],
                "endTime": r["endTime"],
                "text": pieces_beaultify[i],
            }
            for i, r in enumerate(resultados)
        ]
        logger.info("Ressincronização com beautify concluída.")
        return resultados_beautify

    except Exception:
        logger.warning(
            "Falha na ressincronização; usando transcrição bruta como fallback.",
            exc_info=True,
        )
        return [
            {
                "name": speaker_map.get(r.get("name"), "spk_0"),
                "startTime": r["startTime"],
                "endTime": r["endTime"],
                "text": r["text"],
            }
            for r in resultados
        ]


def _executar_pipeline_asr(
    file_path: str,
    pipe,
    usage_acc: JobLlmUsageAccumulator | None = None,
) -> list[dict]:
    """Executa diarização, transcrição paralela e pós-processamento textual.

    Args:
        file_path: Caminho do arquivo WAV.
        pipe: Pipeline ASR do Whisper.
        usage_acc: Acumulador de uso LLM do job.

    Returns:
        Lista de segmentos com texto pré-processado e corrigido, pronto para beautify.
    """
    resultados = executar_diarizacao_e_transcricao(file_path, pipe)

    texto_raw = SEGMENT_SEPARATOR.join(r["text"] for r in resultados)
    texto_preprocessado = preprocess_text(texto_raw)
    texto_corrigido = correct_common_names(texto_preprocessado)

    filename = os.path.basename(file_path)
    texto_beautify = run_pipeline_beautify(texto_corrigido, filename, usage_acc=usage_acc)
    pieces_beautify = texto_beautify.split("%$%")

    return ressincronizar_transcricao(resultados, pieces_beautify)


def transcribe_audio(file_path: str, language: str, gravador: str = "") -> dict:
    """Pipeline completo de transcrição: ASR + diarização + LLM + metadados.

    Args:
        file_path: Caminho local do arquivo de áudio.
        language: Código de idioma (ex: 'pt').
        gravador: Identificação do gravador (sinal geográfico para desambiguação
            de instalações/usinas).

    Returns:
        Dicionário com transcrição, entidades-chave, assunto e resumo.
    """
    start = time()
    start_dt = datetime.now(timezone.utc)
    usage_acc = JobLlmUsageAccumulator()

    _, _, pipe = get_whisper_asr()
    unified_chunks = _executar_pipeline_asr(file_path, pipe, usage_acc=usage_acc)

    key_entities, subject, summary = _extrair_metadados_paralelo(
        unified_chunks, gravador, usage_acc=usage_acc
    )

    if isinstance(key_entities, dict):
        key_entities["assunto_do_audio"] = subject
    else:
        key_entities = {"raw": key_entities, "assunto_do_audio": subject}

    end_dt = datetime.now(timezone.utc)
    summary_text = summary.get("resumo", summary) if isinstance(summary, dict) else summary

    return {
        "transcricaoAudio": {
            "transcription_start": start_dt.isoformat(),
            "transcription_end": end_dt.isoformat(),
            "transcription_processing_time": time() - start,
            "speakers": unified_chunks,
            "key_entities": key_entities,
            "summary": summary_text,
            "llm_usage_metrics": usage_acc.to_dict(),
        }
    }
