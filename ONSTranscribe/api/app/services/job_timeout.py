"""Timeout de job com drain obrigatório (não abandona trabalho nem lock)."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def await_with_job_timeout(
    work: Awaitable[None],
    *,
    job_id: str,
    timeout_s: int,
    on_timeout: Callable[[], Awaitable[None]],
    join_after_timeout: bool = True,
) -> None:
    """Aguarda ``work``; se estourar o tempo, chama ``on_timeout`` e ainda drena ``work``.

    ``asyncio.wait_for`` em ``to_thread`` **não** cancela a thread (CPython). Se
    retornarmos no timeout sem ``await work``, o worker SQS pega o próximo job
    enquanto a thread antiga mantém o lock da GPU → cascata de timeouts falsos
    (HOM 2026-07-23). Com ``join_after_timeout=True`` (default) o caller só
    segue depois que ``work`` termina e libera recursos.
    """
    task = asyncio.ensure_future(work)
    try:
        if timeout_s and timeout_s > 0:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
        else:
            await task
    except asyncio.TimeoutError:
        logger.error(
            "[job-timeout] job %s excedeu %ss sem concluir; marcando falha",
            job_id,
            timeout_s,
        )
        await on_timeout()
        if join_after_timeout:
            logger.warning(
                "[job-timeout] job %s — aguardando thread/trabalho drenar "
                "(GPU/lock) antes do próximo job",
                job_id,
            )
            try:
                await task
            except Exception:
                logger.exception(
                    "[job-timeout] job %s falhou ao drenar após timeout", job_id
                )


def run_with_timeout_join(
    work: Callable[[], None],
    *,
    job_id: str,
    timeout_s: int,
    on_timeout: Callable[[], None],
    hard_grace_s: int = 120,
) -> None:
    """Roda ``work`` em thread; no timeout marca falha e faz join antes de retornar.

    Uso típico: já dentro de ``exclusive_gpu_slot``, para o timeout contar só o
    trabalho de transcrição (não a espera pelo slot) e nunca soltar o slot com
    trabalho ainda rodando.

    Se após o timeout + ``hard_grace_s`` a thread continuar viva (hang real),
    encerra o processo para o ECS reiniciar a task — melhor que fila parada.
    """
    if not timeout_s or timeout_s <= 0:
        work()
        return

    thread = threading.Thread(
        target=work,
        name=f"job-timeout-{job_id}",
        daemon=False,
    )
    thread.start()
    thread.join(timeout=timeout_s)
    if thread.is_alive():
        logger.error(
            "[job-timeout] job %s excedeu %ss sem concluir; marcando falha",
            job_id,
            timeout_s,
        )
        try:
            on_timeout()
        except Exception:
            logger.exception(
                "[job-timeout] job %s falhou no callback on_timeout", job_id
            )
        logger.warning(
            "[job-timeout] job %s — join (grace %ss) até a thread soltar a GPU",
            job_id,
            hard_grace_s,
        )
        if hard_grace_s and hard_grace_s > 0:
            thread.join(timeout=hard_grace_s)
        else:
            thread.join()
        if thread.is_alive():
            logger.critical(
                "[job-timeout] job %s ainda travado após grace %ss — "
                "encerrando processo para o ECS reciclar a task",
                job_id,
                hard_grace_s,
            )
            # Não usa sys.exit: threads daemon/não-daemon podem bloquear.
            os._exit(1)
