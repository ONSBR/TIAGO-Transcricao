"""Regressão: no máximo um pipeline de GPU por processo.

Cobre o caso HOM/DEV em que vários BackgroundTasks (POST /transcribe/)
disparam process_transcription em paralelo e estouram VRAM.
"""
import threading
import time

from app.services.gpu_concurrency import exclusive_gpu_slot


def test_exclusive_gpu_slot_serializes_two_threads():
    """Duas threads não entram na seção crítica ao mesmo tempo."""
    order: list[str] = []
    in_critical = 0
    max_in_critical = 0
    lock_meta = threading.Lock()

    def worker(name: str, hold_s: float) -> None:
        nonlocal in_critical, max_in_critical
        with exclusive_gpu_slot(name):
            with lock_meta:
                in_critical += 1
                max_in_critical = max(max_in_critical, in_critical)
                order.append(f"{name}-in")
            time.sleep(hold_s)
            with lock_meta:
                order.append(f"{name}-out")
                in_critical -= 1

    t1 = threading.Thread(target=worker, args=("a", 0.08))
    t2 = threading.Thread(target=worker, args=("b", 0.02))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert max_in_critical == 1
    # seções completas, sem interleaving in/out entre jobs
    assert order in (
        ["a-in", "a-out", "b-in", "b-out"],
        ["b-in", "b-out", "a-in", "a-out"],
    )


def test_exclusive_gpu_slot_releases_after_exception():
    """Exceção dentro do slot não deixa o lock preso."""
    with exclusive_gpu_slot("boom"):
        raised = False
        try:
            raise RuntimeError("falha simulada")
        except RuntimeError:
            raised = True
    assert raised is True

    entered = False
    with exclusive_gpu_slot("after"):
        entered = True
    assert entered is True
