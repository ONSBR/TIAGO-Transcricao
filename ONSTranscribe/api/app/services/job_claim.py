"""Claim atômico de job no DynamoDB (evita double-process POST + worker)."""
from __future__ import annotations

import logging
import time
from typing import Any

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Status que outro processador já "possui".
_BUSY = frozenset({"IN_PROGRESS", "COMPLETED"})


def should_skip_status(current_status: str | None) -> bool:
    """Pula reprocessamento de job já concluído ou em andamento por outro worker."""
    return current_status in _BUSY


def try_claim_job(
    table: Any,
    *,
    job_id: str,
    source_link: str,
    destination_link: str,
    language: str,
) -> bool:
    """Tenta gravar PENDING só se o job não estiver IN_PROGRESS/COMPLETED.

    Returns:
        True se este processador deve seguir; False se outro já tem o job.
    """
    now = int(time.time())
    item = {
        "job_id": job_id,
        "status": "PENDING",
        "source_link": source_link,
        "destination_link": destination_link,
        "language": language,
        "claimed_at": now,
    }
    try:
        # put condicional: não sobrescreve se já está busy
        existing = table.get_item(Key={"job_id": job_id}).get("Item")
        if existing and should_skip_status(existing.get("status")):
            logger.info(
                "[claim] job %s status=%s — outro processador; skip",
                job_id,
                existing.get("status"),
            )
            return False
        # Condition: attribute_not_exists OR status not in busy
        # DynamoDB: use put with condition on status
        if not existing:
            table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(job_id)",
            )
            return True
        table.put_item(
            Item=item,
            ConditionExpression="#st <> :ip AND #st <> :done",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":ip": "IN_PROGRESS", ":done": "COMPLETED"},
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info("[claim] job %s claim negado (condição)", job_id)
            return False
        raise


def try_mark_in_progress(table: Any, job_id: str) -> bool:
    """Transição PENDING/FAILED → IN_PROGRESS atômica.

    Returns:
        True se este processador marcou IN_PROGRESS; False se perdeu a corrida.
    """
    try:
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #st = :ip, progress_at = :t",
            ConditionExpression="attribute_not_exists(#st) OR #st = :pend OR #st = :fail",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":ip": "IN_PROGRESS",
                ":pend": "PENDING",
                ":fail": "FAILED",
                ":t": int(time.time()),
            },
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info(
                "[claim] job %s não passou a IN_PROGRESS (já claimed)", job_id
            )
            return False
        raise
