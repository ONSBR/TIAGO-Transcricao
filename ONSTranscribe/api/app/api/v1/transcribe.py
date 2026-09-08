"""Endpoints da API de transcrição de áudio."""
import asyncio
import gc
import json
import logging
import os
from decimal import Decimal

import boto3
import torch
from botocore.config import Config
from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.config import config
from app.schemas import AudioRequest
from app.services.audio_source import (
    InvalidAudioSourceError,
    user_facing_transcription_error,
    validate_audio_source_link,
    validate_local_audio_file,
)
from app.services.failure_result import publish_failure_json_to_s3
from app.services.gpu_concurrency import exclusive_gpu_slot
from app.services.job_claim import try_claim_job, try_mark_in_progress
from app.services.job_timeout import run_with_timeout_join
from app.services.model_manager import download_model_if_not_exists
from app.services.s3_service import download_file_from_s3, upload_file_to_s3
from app.services.transcribe_service import transcribe_audio

router = APIRouter()
logger = logging.getLogger(__name__)

# Timeouts e retentativas curtas: uma chamada DynamoDB lenta NÃO pode prender o
# event loop ASGI por muito tempo (causa raiz dos Read timeouts na submissão).
_DDB_CONFIG = Config(
    connect_timeout=5,
    read_timeout=5,
    retries={"max_attempts": 3, "mode": "standard"},
)
dynamodb = boto3.resource("dynamodb", region_name=config.aws.region, config=_DDB_CONFIG)
table = dynamodb.Table(config.aws.dynamodb_table)

sqs_client = boto3.client("sqs", region_name=config.aws.region)
sqs_queue_url = config.aws.sqs_queue_url

download_model_if_not_exists(
    s3_bucket=config.aws.s3_bucket,
    s3_folder_path=config.aws.s3_model_path,
    local_folder_path=config.models.whisper_local_path,
)
download_model_if_not_exists(
    s3_bucket=config.aws.s3_bucket,
    s3_folder_path=config.aws.s3_segmentation_model_path,
    local_folder_path=config.models.segmentation_local_path,
)


def _convert_floats_to_decimals(obj):
    if isinstance(obj, list):
        return [_convert_floats_to_decimals(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _convert_floats_to_decimals(v) for k, v in obj.items()}
    if isinstance(obj, float):
        return Decimal(str(obj))
    return obj


def _decimal_to_float(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _update_job_status(job_id: str, status: str, info: dict | None = None) -> None:
    update_expr = "SET #st = :s"
    attr_values: dict = {":s": status}
    if info is not None:
        update_expr += ", info = :i"
        attr_values[":i"] = info
    table.update_item(
        Key={"job_id": job_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues=attr_values,
    )


def _publish_job_failure(
    job_id: str,
    destination_link: str,
    error: str,
    *,
    update_ddb: bool = True,
) -> None:
    """FAILED no DDB + JSON de falha no S3 (Lambda → OpenSearch/front)."""
    if update_ddb:
        item = table.get_item(Key={"job_id": job_id}).get("Item") or {}
        if item.get("status") != "COMPLETED":
            _update_job_status(job_id, "FAILED", {"error": error})
    try:
        publish_failure_json_to_s3(
            destination_link=destination_link,
            error=error,
            upload_fn=upload_file_to_s3,
        )
    except Exception:
        logger.exception(
            "[failure-result] job %s falhou ao publicar JSON de erro", job_id
        )


def _release_gpu_memory() -> None:
    """Libera memória CUDA presa no alocador do PyTorch após cada transcrição.

    Sem isso, os blocos alocados por Whisper/pyannote não são devolvidos ao
    driver entre chamadas sequenciais no mesmo processo, causando CUDA OOM
    acumulativo mesmo com MAX_CONCURRENT_TRANSCRIPTIONS=1.
    """
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _extract_gravador(s3_source_link: str) -> str:
    """Extrai o nome do gravador a partir do path do S3.

    Usa o mapa code|name definido na env var ``RecodersList`` (pares separados
    por ``_``, ex.: ``RIO|Rio de Janeiro_REC|Recife``). Auxilia na desambiguação
    geográfica de instalações/usinas.

    Args:
        s3_source_link: URL/chave S3 do arquivo de áudio de entrada.

    Returns:
        Nome do gravador correspondente ou string vazia se não identificado.
    """
    recorders_list = os.getenv("RecodersList", "")
    for pair in recorders_list.split("_"):
        if "|" in pair:
            code, name = pair.split("|", 1)
            if f"/{code}/" in s3_source_link or s3_source_link.startswith(f"{code}/"):
                return name
    return ""


def _transcription_work_after_gpu(
    key: str, s3_source_link: str, s3_destination_link: str, language: str
) -> None:
    """Download + ASR/LLM + upload. Chamado já com slot GPU e status IN_PROGRESS."""
    audio_filename = os.path.basename(s3_source_link)
    local_path = os.path.join(config.models.temp_dir, audio_filename)

    logger.info("Baixando áudio do S3: %s", s3_source_link)
    download_file_from_s3(s3_source_link, local_path)
    # HOM: Storage Gateway às vezes grava WAV de 44 B (só header) com duração 0 no nome.
    validate_local_audio_file(local_path)

    gravador = _extract_gravador(s3_source_link)

    logger.info("Iniciando transcrição: %s", local_path)
    try:
        transcription = transcribe_audio(local_path, language, gravador)
    finally:
        _release_gpu_memory()
    logger.info("Transcrição concluída: %s", transcription)

    job_name = os.path.splitext(audio_filename)[0]
    local_json_path = os.path.join(config.models.temp_dir, f"{job_name}.json")
    info_decimal = _convert_floats_to_decimals(transcription)

    with open(local_json_path, "w", encoding="utf-8") as json_file:
        json.dump(info_decimal, json_file, default=_decimal_to_float)

    destination = (
        s3_destination_link + f"{job_name}.json"
        if s3_destination_link.endswith("/")
        else s3_destination_link
    )

    logger.info("Enviando resultado para S3: %s", destination)
    upload_file_to_s3(local_json_path, destination)

    _update_job_status(key, "COMPLETED", info_decimal)


def _process_transcription_sync(
    key: str, s3_source_link: str, s3_destination_link: str, language: str
) -> None:
    """Pipeline síncrono: slot GPU → claim IN_PROGRESS → trabalho com timeout+join.

    O timeout (``JOB_TIMEOUT_S``) conta **só após** adquirir o slot. Espera por
    GPU (Lambda ESM + worker, ou dois POSTs) não consome o orçamento.

    No timeout: marca FAILED, publica JSON de falha no S3 (front), join da
    thread (libera GPU). Se a thread não terminar no grace, encerra o processo
    para o ECS reiniciar a task (evita fila parada para sempre).
    """
    timeout_s = config.transcription.job_timeout_s
    hard_grace_s = int(os.getenv("JOB_TIMEOUT_HARD_GRACE_S", "120"))

    with exclusive_gpu_slot(key):
        try:
            try:
                validate_audio_source_link(s3_source_link)
            except InvalidAudioSourceError as exc:
                logger.error("Source inválido job %s: %s", key, exc)
                err = f"Source de áudio inválido: {exc}"
                _publish_job_failure(key, s3_destination_link, err)
                return

            if not try_mark_in_progress(table, key):
                logger.info(
                    "[claim] job %s perdeu corrida IN_PROGRESS; aborta", key
                )
                return

            def _on_work_timeout() -> None:
                item = table.get_item(Key={"job_id": key}).get("Item") or {}
                if item.get("status") == "COMPLETED":
                    logger.info(
                        "[job-timeout] job %s completou durante o drain; "
                        "mantém COMPLETED",
                        key,
                    )
                    return
                err = (
                    f"Timeout: job excedeu {timeout_s}s de trabalho "
                    "após adquirir GPU (estava IN_PROGRESS)."
                )
                _publish_job_failure(key, s3_destination_link, err)

            def _work() -> None:
                try:
                    _transcription_work_after_gpu(
                        key, s3_source_link, s3_destination_link, language
                    )
                except Exception as exc:
                    logger.exception("Erro durante transcrição do job %s", key)
                    item = table.get_item(Key={"job_id": key}).get("Item") or {}
                    if item.get("status") != "COMPLETED":
                        _publish_job_failure(
                            key,
                            s3_destination_link,
                            user_facing_transcription_error(exc),
                        )

            run_with_timeout_join(
                _work,
                job_id=key,
                timeout_s=timeout_s,
                on_timeout=_on_work_timeout,
                hard_grace_s=hard_grace_s,
            )

        except Exception as exc:
            logger.exception("Erro durante transcrição do job %s", key)
            _publish_job_failure(
                key,
                s3_destination_link,
                user_facing_transcription_error(exc),
            )


async def process_transcription(
    key: str, s3_source_link: str, s3_destination_link: str, language: str
) -> None:
    """Executa o pipeline completo de transcrição em background.

    Serializa GPU (BackgroundTasks HTTP + worker SQS) com ``exclusive_gpu_slot``.
    Timeout de trabalho após o slot; drain da thread no timeout para o worker
    não puxar o próximo job com lock preso.

    Args:
        key: Identificador do job (job_id no DynamoDB).
        s3_source_link: URL S3 do arquivo de áudio de entrada.
        s3_destination_link: URL S3 de destino para o JSON de saída.
        language: Código de idioma da transcrição.
    """
    await asyncio.to_thread(
        _process_transcription_sync,
        key,
        s3_source_link,
        s3_destination_link,
        language,
    )


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/transcribe/")
async def transcribe_audio_endpoint(
    request: AudioRequest, background_tasks: BackgroundTasks
):
    audio_filename = os.path.basename(request.audio_source_link)
    key = os.path.splitext(audio_filename)[0]

    logger.info(
        "Requisição recebida: source=%s destination=%s language=%s",
        request.audio_source_link,
        request.destination_link,
        request.language,
    )

    claimed = await asyncio.to_thread(
        try_claim_job,
        table,
        job_id=key,
        source_link=request.audio_source_link,
        destination_link=request.destination_link,
        language=request.language,
    )
    if not claimed:
        return {
            "status": "Job already in progress or completed",
            "job_id": key,
        }

    background_tasks.add_task(
        process_transcription,
        key,
        request.audio_source_link,
        request.destination_link,
        request.language,
    )

    return {"status": "Transcription started", "job_id": key}


@router.get("/transcribe/status/{key}")
async def get_transcription_status(key: str):
    response = await asyncio.to_thread(table.get_item, Key={"job_id": key})
    if "Item" not in response:
        raise HTTPException(status_code=404, detail="Job not found")

    item = response["Item"]
    return {
        "job_id": key,
        "status": item.get("status"),
        "info": item.get("info", {}),
        "source_link": item.get("source_link"),
        "destination_link": item.get("destination_link"),
        "language": item.get("language"),
    }
