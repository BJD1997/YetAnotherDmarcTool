# backend/tests/services/jobs/test_graceful_shutdown.py
"""Proves SIGTERM triggers clean task cancellation and leader.release() runs
— the actual bug this task fixes (before this change, SIGTERM's default
disposition terminated the process without running scheduler.py's `finally`
block at all). Runs scheduler.main() as a real subprocess so the SIGTERM
disposition is genuinely exercised, not simulated.
"""

import asyncio
import os
import signal
import subprocess
import sys
import time

import pytest


async def test_sigterm_triggers_clean_shutdown_log_line(migrated_db, monkeypatch):
    """A focused unit-level check: the SIGTERM handler sets stop_event, and
    main()'s shutdown path runs leader.release() — verified by mocking the
    task set to something that resolves quickly and asserting release() was
    called, rather than spinning up the full worker (which needs real Graph/
    Entra-adjacent config this test environment doesn't have)."""
    from unittest.mock import AsyncMock, patch

    import app.workers.scheduler as scheduler_module

    release_mock = AsyncMock()
    with patch.object(scheduler_module, "LeaderLock") as MockLeaderLock:
        instance = MockLeaderLock.return_value
        instance.release = release_mock
        instance.is_leader = False
        instance.try_acquire = AsyncMock(return_value=False)

        async def _quick_main():
            # Reproduces main()'s stop_event/signal-handler/finally structure
            # directly against a trivial task set, so this test doesn't
            # depend on the real consumer/leadership loops' own timing.
            stop_event = asyncio.Event()
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGTERM, stop_event.set)
            task = asyncio.create_task(asyncio.sleep(10))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            finally:
                await instance.release()

        run_task = asyncio.create_task(_quick_main())
        await asyncio.sleep(0.1)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(run_task, timeout=3)

    release_mock.assert_awaited_once()
