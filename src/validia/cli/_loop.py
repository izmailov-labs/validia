"""The one place validia drives an event loop: for the command line, which owns it.

Everything else in ``src/`` is loop-neutral -- time and HTTP are injected, and ruff's
``TID251`` bans ``import asyncio`` -- so a library user brings their own loop and their
own way of bounding concurrency. The ``validia`` command is an application, not a
library, and an application has to pick a loop. It picks asyncio, here, and nowhere else.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence

_SKIPPED = object()


def run_bounded[T](
    jobs: Sequence[Callable[[], Awaitable[T]]],
    *,
    limit: int,
    done: Callable[[T], None] | None = None,
    stop: Callable[[T], bool] | None = None,
    close: Callable[[], Awaitable[None]] | None = None,
) -> list[T]:
    """Run jobs on a fresh event loop, at most ``limit`` at a time, in order of results.

    Args:
        jobs: Each job, as a function that starts it.
        limit: How many may be in flight at once; at least 1.
        done: Called with each result as it arrives, for progress.
        stop: Whether a result means no further job should start. Jobs already in
            flight finish; jobs not yet started are skipped and have no result.
        close: Awaited after the last job, inside the same loop, to release what
            the jobs shared -- a pooled HTTP client is bound to the loop it was made on.

    Returns:
        The result of every job that ran, in the order of ``jobs``.
    """

    async def main() -> list[T]:
        gate = asyncio.Semaphore(limit)
        stopped = False

        async def one(job: Callable[[], Awaitable[T]]) -> object:
            nonlocal stopped
            async with gate:
                if stopped:
                    return _SKIPPED
                result = await job()
                if stop is not None and stop(result):
                    stopped = True  # set before the slot frees, so no waiting job slips in
            if done is not None:
                done(result)
            return result

        try:
            results = await asyncio.gather(*(one(job) for job in jobs))
        finally:
            if close is not None:
                await close()
        return [result for result in results if result is not _SKIPPED]  # type: ignore[misc]

    return asyncio.run(main())
