"""Worker SQS desacoplado: consome a fila e transcreve, fora do caminho HTTP.

Por que existe: antes, o consumer Lambda fazia POST na API e a API transcrevia
dentro da requisição. Sob vários áudios ao mesmo tempo a API travava, devolvia
504 e a mensagem ia para a DLQ (áudio perdido). Aqui o worker puxa a mensagem da
fila e processa de forma sequencial; um pico fica na fila e é drenado aos poucos,
sem perda. A capacidade total cresce com mais máquinas (escala horizontal), não
com concorrência dentro da máquina (que satura a CPU da diarização).

Os imports pesados (transcribe, boto3, modelo) são feitos dentro das funções para
manter `build_job`/`should_skip` testáveis sem AWS nem carregar o modelo.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Visibility inicial e heartbeat: jobs longos (espera GPU + ASR) superam 15 min.
SQS_VISIBILITY_TIMEOUT = int(os.getenv("SQS_VISIBILITY_TIMEOUT", "1800"))  # 30 min
SQS_VISIBILITY_HEARTBEAT_S = int(os.getenv("SQS_VISIBILITY_HEARTBEAT_S", "600"))  # 10 min
SQS_WAIT_TIME_SECONDS = 20  # long polling

# Extensões de mídia que NÃO entram no nome do JSON de saída.
# Se o producer mandar key com ".wav", destino vira stem.json — nunca stem.wav.json
# (poison: SNS re-enfileira transcriptions/*.wav.json como áudio).
_AUDIO_SUFFIXES = (
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
    ".m4a",
    ".webm",
    ".aac",
    ".wma",
)


def audio_object_stem(path_or_name: str) -> str:
    """Basename sem extensão de áudio (e sem .json de saída, se vier colado).

    ``foo.wav`` → ``foo``; ``foo.wav.json`` → ``foo``; ``foo`` → ``foo``.
    """
    name = (path_or_name or "").rstrip("/").split("/")[-1]
    lower = name.lower()
    if lower.endswith(".json"):
        name = name[: -len(".json")]
        lower = name.lower()
    for ext in sorted(_AUDIO_SUFFIXES, key=len, reverse=True):
        if lower.endswith(ext):
            return name[: -len(ext)]
    return name


def build_job(body: dict, bucket: str, transcriptions_key_path: str) -> dict:
    """Traduz a mensagem da fila ({raw_key, key}) nos campos do job.

    Reproduz o que o consumer Lambda e o endpoint /transcribe esperam.
    ``key`` no contrato é path sem extensão de mídia; se vier com ``.wav``
    (stress/legado), o destino ainda é ``{stem}.json``, não ``{stem}.wav.json``.
    """
    raw_key = body["raw_key"]
    key = body["key"]
    source_link = f"s3://{bucket}/{raw_key}"
    stem = audio_object_stem(key) or audio_object_stem(raw_key)
    destination_link = f"s3://{bucket}/{transcriptions_key_path}{stem}.json"
    job_id = stem
    return {
        "job_id": job_id,
        "source_link": source_link,
        "destination_link": destination_link,
        "language": "pt",
    }


def should_skip(current_status: str | None) -> bool:
    """Idempotência: pula COMPLETED e IN_PROGRESS (outro processador)."""
    from app.services.job_claim import should_skip_status

    return should_skip_status(current_status)


def message_should_delete(final_status: str | None) -> bool:
    """Apaga da fila após processamento terminal ou skip.

    COMPLETED / FAILED (resultado publicado) / IN_PROGRESS (outro worker) /
    None (skip sem status) → delete.
    PENDING residual → requeue (visibility 0) para outro consumidor.
    """
    return final_status in ("COMPLETED", "FAILED", "IN_PROGRESS", None)


def _process_message(body: dict, audio_bucket: str) -> str | None:
    """Processa uma mensagem. Returns status final no DDB (para delete condicional)."""
    from app.api.v1 import transcribe as t
    from app.services.job_claim import try_claim_job
    import asyncio

    job = build_job(
        body,
        audio_bucket,
        os.getenv("TRANSCRIPTIONS_KEY_PATH", "audios/salas/transcriptions/"),
    )
    job_id = job["job_id"]

    existing = t.table.get_item(Key={"job_id": job_id}).get("Item")
    if should_skip(existing.get("status") if existing else None):
        logger.info(
            "[worker] job %s status=%s — pulando",
            job_id,
            existing.get("status") if existing else None,
        )
        return existing.get("status") if existing else "COMPLETED"

    if not try_claim_job(
        t.table,
        job_id=job_id,
        source_link=job["source_link"],
        destination_link=job["destination_link"],
        language=job["language"],
    ):
        return "IN_PROGRESS"

    logger.info("[worker] processando job %s", job_id)
    asyncio.run(
        t.process_transcription(
            job_id, job["source_link"], job["destination_link"], job["language"]
        )
    )
    logger.info("[worker] job %s finalizado", job_id)
    final = t.table.get_item(Key={"job_id": job_id}).get("Item") or {}
    return final.get("status")


def _make_sqs_client(region: str):
    """Cliente SQS com região explícita.

    Sem region_name (e sem AWS_REGION no ambiente do ECS), o boto3 tenta resolver a
    região pelo IMDS e a criação do cliente TRAVA — a thread do worker ficava presa
    aqui, sem logar "iniciando" nem consumir a fila. Todos os outros clientes do app
    (s3, dynamodb, o sqs_client do transcribe) já passam region_name=config.aws.region;
    o worker precisa fazer igual.
    """
    import boto3

    return boto3.client("sqs", region_name=region)


class _VisibilityHeartbeat:
    """Estende VisibilityTimeout enquanto o job roda (evita DLQ no meio do ASR)."""

    def __init__(
        self,
        sqs: Any,
        queue_url: str,
        receipt_handle: str,
        visibility_s: int,
        interval_s: int,
    ):
        self._sqs = sqs
        self._queue_url = queue_url
        self._handle = receipt_handle
        self._visibility_s = visibility_s
        self._interval_s = max(30, interval_s)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="sqs-visibility-heartbeat", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_s):
            try:
                self._sqs.change_message_visibility(
                    QueueUrl=self._queue_url,
                    ReceiptHandle=self._handle,
                    VisibilityTimeout=self._visibility_s,
                )
                logger.debug(
                    "[worker] heartbeat visibility=%ss", self._visibility_s
                )
            except Exception:
                logger.exception("[worker] falha no heartbeat de visibility")


def worker_loop() -> None:
    from app.config import config

    queue_url = config.aws.sqs_queue_url
    # Bucket dos áudios: o MESMO do producer/consumer (bucket de áudios brutos,
    # ex.: exemplo-audio-bucket), não o config.aws.s3_bucket (que é o bucket dos
    # modelos). Sem isso o worker baixava do bucket errado e a transcrição dava
    # 404 no HeadObject.
    audio_bucket = os.getenv("TRANSCRIBE_AUDIO_BUCKET")
    if not queue_url or not audio_bucket:
        logger.warning(
            "TRANSCRIBE_SQS_QUEUE_URL/TRANSCRIBE_AUDIO_BUCKET não configurada; "
            "worker não iniciado."
        )
        return

    sqs = _make_sqs_client(config.aws.region)
    logger.info(
        "[worker] iniciando, fila=%s visibility=%ss heartbeat=%ss (sequencial)",
        queue_url,
        SQS_VISIBILITY_TIMEOUT,
        SQS_VISIBILITY_HEARTBEAT_S,
    )
    while True:
        try:
            resp = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,  # sequencial: 1 por vez por máquina
                WaitTimeSeconds=SQS_WAIT_TIME_SECONDS,
                VisibilityTimeout=SQS_VISIBILITY_TIMEOUT,
            )
        except Exception:
            logger.exception("[worker] erro recebendo da fila; aguardando")
            time.sleep(5)
            continue

        for msg in resp.get("Messages", []):
            handle = msg["ReceiptHandle"]
            hb = _VisibilityHeartbeat(
                sqs,
                queue_url,
                handle,
                SQS_VISIBILITY_TIMEOUT,
                SQS_VISIBILITY_HEARTBEAT_S,
            )
            hb.start()
            final_status: str | None = None
            try:
                final_status = _process_message(json.loads(msg["Body"]), audio_bucket)
            except Exception:
                logger.exception("[worker] erro processando mensagem")
                final_status = "FAILED"
            finally:
                hb.stop()
                if message_should_delete(final_status):
                    try:
                        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=handle)
                    except Exception:
                        logger.exception(
                            "[worker] erro apagando mensagem da fila "
                            "(status=%s)",
                            final_status,
                        )
                else:
                    # Devolve à fila cedo para redrive / outro worker.
                    try:
                        sqs.change_message_visibility(
                            QueueUrl=queue_url,
                            ReceiptHandle=handle,
                            VisibilityTimeout=0,
                        )
                        logger.info(
                            "[worker] mensagem reenfileirada (status=%s)",
                            final_status,
                        )
                    except Exception:
                        logger.exception(
                            "[worker] falha ao reenfileirar mensagem"
                        )


def start_worker() -> None:
    threading.Thread(target=worker_loop, daemon=True).start()
