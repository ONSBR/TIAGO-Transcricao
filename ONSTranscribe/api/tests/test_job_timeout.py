"""Regressão: timeout marca falha e drena o trabalho (não abandona GPU/lock)."""
import asyncio
import threading
import time

import pytest

from app.services.job_timeout import await_with_job_timeout, run_with_timeout_join


@pytest.mark.unit
def test_await_with_job_timeout_calls_on_timeout():
    called: list[str] = []

    async def slow():
        await asyncio.sleep(1.0)

    async def on_timeout():
        called.append("timeout")

    async def run():
        await await_with_job_timeout(
            slow(),
            job_id="job-slow",
            timeout_s=0.2,
            on_timeout=on_timeout,
        )

    asyncio.run(run())
    assert called == ["timeout"]


@pytest.mark.unit
def test_await_with_job_timeout_joins_work_after_timeout():
    """Worker não pode seguir com a thread antiga ainda no lock da GPU."""
    order: list[str] = []

    async def slow_holding_resource():
        order.append("work-start")
        await asyncio.sleep(0.5)
        order.append("work-end")

    async def on_timeout():
        order.append("timeout")

    async def run():
        await await_with_job_timeout(
            slow_holding_resource(),
            job_id="job-join",
            timeout_s=0.1,
            on_timeout=on_timeout,
            join_after_timeout=True,
        )
        order.append("caller-resumed")

    asyncio.run(run())
    assert order == ["work-start", "timeout", "work-end", "caller-resumed"]


@pytest.mark.unit
def test_await_with_job_timeout_completes_before_deadline():
    done: list[str] = []

    async def fast():
        done.append("ok")

    async def on_timeout():
        done.append("timeout")

    async def run():
        await await_with_job_timeout(
            fast(),
            job_id="job-fast",
            timeout_s=2,
            on_timeout=on_timeout,
        )

    asyncio.run(run())
    assert done == ["ok"]


@pytest.mark.unit
def test_run_with_timeout_join_marks_timeout_and_drains_thread():
    order: list[str] = []
    lock = threading.Lock()

    def work():
        order.append("work-start")
        time.sleep(0.4)
        with lock:
            order.append("work-end")

    def on_timeout():
        order.append("timeout")

    t0 = time.monotonic()
    run_with_timeout_join(
        work,
        job_id="sync-job",
        timeout_s=0.1,
        on_timeout=on_timeout,
        hard_grace_s=5,
    )
    elapsed = time.monotonic() - t0

    assert "timeout" in order
    assert "work-end" in order
    assert order.index("timeout") < order.index("work-end")
    # join esperou o sleep restante (~0.4s), não retornou só no 0.1s
    assert elapsed >= 0.35


@pytest.mark.unit
def test_run_with_timeout_join_no_timeout_when_fast():
    order: list[str] = []

    def work():
        order.append("ok")

    def on_timeout():
        order.append("timeout")

    run_with_timeout_join(work, job_id="fast", timeout_s=2, on_timeout=on_timeout)
    assert order == ["ok"]


@pytest.mark.unit
def test_config_default_job_timeout_is_900():
    from app.config import TranscriptionConfig

    assert TranscriptionConfig().job_timeout_s == 900
