"""The command line's event loop: bounded, ordered, stoppable, and tidy afterwards."""

import asyncio

from validia.cli._loop import run_bounded


def test_jobs_run_at_most_limit_at_a_time_and_keep_their_order() -> None:
    running = 0
    peak = 0

    def job(value: int):  # type: ignore[no-untyped-def]
        async def go() -> int:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.001 * (5 - value))
            running -= 1
            return value

        return go

    seen: list[int] = []
    results = run_bounded([job(n) for n in range(5)], limit=2, done=seen.append)
    assert results == [0, 1, 2, 3, 4]
    assert peak == 2
    assert sorted(seen) == [0, 1, 2, 3, 4]


def test_a_stop_skips_every_job_not_yet_started_and_still_closes() -> None:
    started: list[int] = []
    closed: list[bool] = []

    def job(value: int):  # type: ignore[no-untyped-def]
        async def go() -> int:
            started.append(value)
            return value

        return go

    async def close() -> None:
        closed.append(True)

    results = run_bounded(
        [job(n) for n in range(6)], limit=1, stop=lambda value: value == 2, close=close
    )
    assert results == [0, 1, 2]
    assert started == [0, 1, 2]
    assert closed == [True]
