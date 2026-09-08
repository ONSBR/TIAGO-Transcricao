"""Publica resultado de falha no S3 para o Lambda indexar no OpenSearch/front."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)


def build_failure_transcription_payload(error: str) -> dict[str, Any]:
    """JSON no formato esperado pelo process_transcribe (speakers não vazios).

    O front passa a ver texto/metadados de falha em vez de card em branco.
    """
    msg = (error or "").strip() or "Falha na transcrição"
    return {
        "transcricaoAudio": {
            "speakers": [
                {
                    "name": "system",
                    "startTime": 0.0,
                    "endTime": 0.0,
                    "text": f"[Transcrição indisponível] {msg}",
                }
            ],
            "summary": f"Falha na transcrição: {msg}",
            "key_entities": {
                "nome_do_operador_ONS": [],
                "nome_do_operador_do_agente": [],
                "nome_do_agente": [],
                "instalacao_ou_usina_envolvida": [],
                "equipamento_envolvido": [],
                "numero_do_SGI": [],
                "assunto_do_audio": ["Falha de processamento"],
            },
            "processing_status": "FAILED",
            "error": msg,
        }
    }


def publish_failure_json_to_s3(
    *,
    destination_link: str,
    error: str,
    upload_fn,
) -> None:
    """Grava JSON de falha no destino S3 (dispara Lambda → OpenSearch)."""
    payload = build_failure_transcription_payload(error)
    dest = destination_link
    if dest.endswith("/"):
        # path incompleto — não inventa nome
        logger.warning(
            "[failure-result] destination termina em /; skip publish: %s", dest
        )
        return

    fd, path = tempfile.mkstemp(suffix=".json", prefix="fail-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        logger.info("[failure-result] enviando falha para %s", dest)
        upload_fn(path, dest)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
