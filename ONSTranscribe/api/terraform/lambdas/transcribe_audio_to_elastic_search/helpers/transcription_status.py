"""Status de transcrição visível no front (OpenSearch → API C# → Player).

O Player só renderiza `transcricao.speakers[]` com name/startTime/endTime/text.
Sem speakers o card parece "sem transcrição". Estes payloads preenchem o campo
antes do job terminar (fila) e, no pipeline da API, o de falha vai via S3
(ver app.services.failure_result).
"""
from __future__ import annotations

from typing import Any


QUEUED_SPEAKER_TEXT = "[Na fila de transcrição]"
QUEUED_SUMMARY = (
    "Áudio na fila de transcrição. Em breve o texto estará disponível."
)

PAUSED_SPEAKER_TEXT = "[Processamento de transcrição desligado]"
PAUSED_SUMMARY = (
    "Áudio registrado; a transcrição automática está pausada neste ambiente "
    "(economia de custo). Não será processado até o processamento ser religado."
)


def _empty_key_entities() -> dict[str, list]:
    return {
        "nome_do_operador_ONS": [],
        "nome_do_operador_do_agente": [],
        "nome_do_agente": [],
        "instalacao_ou_usina_envolvida": [],
        "equipamento_envolvido": [],
        "numero_do_SGI": [],
        "assunto_do_audio": [],
    }


def _system_placeholder(text: str, summary: str, status: str) -> dict[str, Any]:
    return {
        "speakers": [
            {
                "name": "system",
                "startTime": 0.0,
                "endTime": 0.0,
                "text": text,
            }
        ],
        "summary": summary,
        "key_entities": _empty_key_entities(),
        "processing_status": status,
    }


def build_queued_transcricao_audio() -> dict[str, Any]:
    """Placeholder quando o áudio será enfileirado para o worker."""
    return _system_placeholder(QUEUED_SPEAKER_TEXT, QUEUED_SUMMARY, "QUEUED")


def build_paused_transcricao_audio() -> dict[str, Any]:
    """Placeholder quando SSM transcription_processing_enabled=false (sem SQS)."""
    return _system_placeholder(PAUSED_SPEAKER_TEXT, PAUSED_SUMMARY, "PAUSED")
