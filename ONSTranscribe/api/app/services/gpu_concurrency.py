"""Limite de 1 pipeline de transcrição por processo (e por máquina).

O worker SQS é sequencial, mas BackgroundTasks (HTTP) e ``asyncio.to_thread``
podem intercalar acquires. ``threading.Lock`` serializa threads no processo;
``fcntl.flock`` serializa também se houver mais de um processo no host.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_gpu_lock = threading.Lock()
_GPU_LOCK_PATH = os.getenv("GPU_LOCK_PATH", "/tmp/ons-transcribe-gpu.lock")


@contextmanager
def exclusive_gpu_slot(job_id: str = "") -> Iterator[None]:
    """Seção crítica da GPU: no máximo um pipeline por máquina.

    Preferir este context manager envolvendo **todo** o trabalho síncrono do job
    (acquire + work + release na mesma thread). Separar acquire/release em
    ``asyncio.to_thread`` distintos facilita interleaving confuso nos logs e
    falhas se o release não rodar.
    """
    label = job_id or "?"
    logger.info("[gpu-slot] aguardando slot para job %s", label)
    _gpu_lock.acquire()
    lock_file = open(_GPU_LOCK_PATH, "a+", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        logger.info("[gpu-slot] slot adquirido job %s", label)
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            logger.exception("[gpu-slot] falha ao liberar flock job %s", label)
        try:
            lock_file.close()
        except Exception:
            pass
        _gpu_lock.release()
        logger.info("[gpu-slot] slot liberado job %s", label)


def acquire_gpu_slot(job_id: str = "") -> None:
    """Adquire o slot (bloqueante). Preferir ``exclusive_gpu_slot``."""
    label = job_id or "?"
    logger.info("[gpu-slot] aguardando slot para job %s", label)
    _gpu_lock.acquire()
    logger.info("[gpu-slot] slot adquirido job %s", label)


def release_gpu_slot(job_id: str = "") -> None:
    """Libera o slot adquirido com ``acquire_gpu_slot``."""
    _gpu_lock.release()
    logger.info("[gpu-slot] slot liberado job %s", job_id or "?")
