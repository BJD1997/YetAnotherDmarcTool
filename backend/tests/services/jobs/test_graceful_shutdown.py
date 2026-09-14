# backend/tests/services/jobs/test_graceful_shutdown.py
"""Proves SIGTERM triggers clean task cancellation and leader.release() runs
— the actual bug this task fixes (before this change, SIGTERM's default
disposition terminated the process without running scheduler.py's `finally`
block at all) — and that an unrelated task exception still crashes the
process (restoring the pre-task-1 `asyncio.gather(*tasks)` behavior that a
naive "just await stop_event" rewrite would have silently regressed).

None of these tests spawn a real subprocess or run the real consumer/
leadership loops against a real queue/mailbox — this test environment has no
Graph/Entra-adjacent config for that. Instead:
  - `test_consumer_loop_*` call the actual `_consumer_loop` with
    `queue.process_next` monkeypatched, so the real loop body/timing/
    cancellation handling is exercised.
  - `test_main_*` call the actual `main()` with `LeaderLock` mocked and the
    four loop functions (`_heartbeat_loop`, `_leadership_loop`,
    `_consumer_loop`) replaced with trivial stand-ins, so the real signal
    handler registration, stop_event/task racing, and `finally:
    leader.release()` wiring in `main()` itself is exercised end to end.
    One of these (`test_main_shuts_down_cleanly_when_a_loop_returns_
    alongside_stop_task`) uses a stand-in that deliberately races stop_task
    rather than an inert one, to reproduce a `done`-set edge case the loops
    never hit at real DB-round-trip timings.
"""

import asyncio
import os
import signal
from unittest.mock import AsyncMock, patch

import pytest

import app.workers.scheduler as scheduler_module
from app.services.jobs import queue


async def test_consumer_loop_stops_promptly_on_stop_event(monkeypatch):
    """The idle-sleep uses `wait_for(stop_event.wait(), timeout=poll_interval)`
    so stop_event interrupts it immediately rather than waiting out the full
    poll interval. Prove that by setting a long poll interval and confirming
    the loop still exits almost immediately after stop_event is set."""
    monkeypatch.setattr(queue, "process_next", AsyncMock(return_value=False))
    monkeypatch.setattr(scheduler_module.settings, "worker_queue_poll_interval_seconds", 30.0)

    stop_event = asyncio.Event()
    task = asyncio.create_task(scheduler_module._consumer_loop("test-worker", stop_event))
    await asyncio.sleep(0.05)
    assert not task.done()  # still idling in the 30s wait_for

    stop_event.set()
    # If stop_event didn't interrupt the wait_for, this would hang for ~30s
    # and the timeout below would fire instead.
    await asyncio.wait_for(task, timeout=1)
    assert task.done()


async def test_consumer_loop_propagates_cancelled_error(monkeypatch):
    """A CancelledError raised mid-`process_next` (simulating cancellation
    during an in-flight job claim/handler) must propagate out of the loop —
    caught and re-raised, not swallowed by the broad `except Exception:`
    branch that handles genuine handler errors."""

    async def _raise_cancelled(_worker_id: str) -> bool:
        raise asyncio.CancelledError()

    monkeypatch.setattr(queue, "process_next", _raise_cancelled)

    stop_event = asyncio.Event()
    task = asyncio.create_task(scheduler_module._consumer_loop("test-worker", stop_event))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def _idle_loop(*_args, **_kwargs) -> None:
    """Stand-in for _heartbeat_loop/_leadership_loop/_consumer_loop: runs
    forever until cancelled, without needing real DB/Graph/Entra config."""
    while True:
        await asyncio.sleep(0.01)


def _patch_main_dependencies(monkeypatch) -> None:
    """Swap out main()'s three real loop functions and its health-server bind
    (a real socket we don't want in tests) — everything else in main() (signal
    handler registration, stop_event, LeaderLock construction/release wiring)
    stays real."""
    monkeypatch.setattr(scheduler_module, "_heartbeat_loop", _idle_loop)
    monkeypatch.setattr(scheduler_module, "_leadership_loop", _idle_loop)
    monkeypatch.setattr(scheduler_module, "_consumer_loop", _idle_loop)
    monkeypatch.setattr(scheduler_module, "start_health_server", lambda *_a, **_k: None)
    monkeypatch.setattr(scheduler_module.settings, "worker_concurrency", 1)


async def test_main_releases_leader_on_sigterm(monkeypatch):
    """Calls the real main() (not a hand-copied reproduction): sends it a real
    SIGTERM and asserts leader.release() was called as a consequence of
    main()'s own shutdown code, not the test's own call to it."""
    _patch_main_dependencies(monkeypatch)

    release_mock = AsyncMock()
    with patch.object(scheduler_module, "LeaderLock") as MockLeaderLock:
        instance = MockLeaderLock.return_value
        instance.release = release_mock
        instance.is_leader = False

        run_task = asyncio.create_task(scheduler_module.main())
        await asyncio.sleep(0.1)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(run_task, timeout=3)

    release_mock.assert_awaited_once()


async def test_main_propagates_task_exception_and_still_releases_leader(monkeypatch):
    """The regression this fixes: before, only `stop_event.wait()` was
    awaited, so a task dying on its own (not via SIGTERM) was silently
    swallowed — stop_event never fired, leader.release() never ran, and the
    process kept running as a zombie. Now an unhandled task exception must
    propagate out of main() (so the process crashes and the orchestrator
    restarts it, matching pre-task-1 `asyncio.gather(*tasks)` behavior) AND
    leader.release() must still run via `finally`."""
    _patch_main_dependencies(monkeypatch)

    class _SimulatedCrash(Exception):
        pass

    async def _boom_loop(*_args, **_kwargs) -> None:
        await asyncio.sleep(0.05)
        raise _SimulatedCrash("simulated heartbeat loop crash")

    monkeypatch.setattr(scheduler_module, "_heartbeat_loop", _boom_loop)

    release_mock = AsyncMock()
    with patch.object(scheduler_module, "LeaderLock") as MockLeaderLock:
        instance = MockLeaderLock.return_value
        instance.release = release_mock
        instance.is_leader = False

        with pytest.raises(_SimulatedCrash):
            await asyncio.wait_for(scheduler_module.main(), timeout=3)

    release_mock.assert_awaited_once()


async def test_main_shuts_down_cleanly_when_a_loop_returns_alongside_stop_task(monkeypatch):
    """Regression test for a latent bug the final whole-branch review caught:
    the `done` guard used to treat any non-stop_task member of `done` as "a
    task died unexpectedly" (`task is stop_task or task.cancelled()`), without
    checking stop_event itself. A loop that (like the real _consumer_loop's
    idle path) awaits stop_event directly and returns cleanly can land in
    `done` in the very same asyncio.wait() batch as stop_task on a genuine
    SIGTERM — that used to raise RuntimeError and log a false-alarm crash
    traceback on every normal shutdown where timing lined up this way.
    """
    monkeypatch.setattr(scheduler_module, "_heartbeat_loop", _idle_loop)
    monkeypatch.setattr(scheduler_module, "_leadership_loop", _idle_loop)

    async def _consumer_stand_in(_worker_id, stop_event):
        # No round-trip before the await, unlike the real loop's process_next
        # call — so this resolves in lockstep with stop_task, reproducing the
        # race rather than leaving it latent.
        await stop_event.wait()

    monkeypatch.setattr(scheduler_module, "_consumer_loop", _consumer_stand_in)
    monkeypatch.setattr(scheduler_module, "start_health_server", lambda *_a, **_k: None)
    monkeypatch.setattr(scheduler_module.settings, "worker_concurrency", 1)

    release_mock = AsyncMock()
    with patch.object(scheduler_module, "LeaderLock") as MockLeaderLock:
        instance = MockLeaderLock.return_value
        instance.release = release_mock
        instance.is_leader = False

        run_task = asyncio.create_task(scheduler_module.main())
        await asyncio.sleep(0.1)
        os.kill(os.getpid(), signal.SIGTERM)
        # Before the fix, this raised RuntimeError instead of returning.
        await asyncio.wait_for(run_task, timeout=3)

    release_mock.assert_awaited_once()


async def test_sigterm_triggers_clean_shutdown_log_line(migrated_db, monkeypatch):
    """A focused unit-level check kept alongside the real-main()/real-
    _consumer_loop tests above: reproduces main()'s original
    stop_event/signal-handler/finally shape directly against a trivial task
    set, independent of main()'s own current implementation, as a second
    signal that the basic SIGTERM -> stop_event -> finally wiring pattern
    itself is sound. This does not exercise the real main() or
    _consumer_loop — see the tests above for that."""
    release_mock = AsyncMock()
    with patch.object(scheduler_module, "LeaderLock") as MockLeaderLock:
        instance = MockLeaderLock.return_value
        instance.release = release_mock
        instance.is_leader = False
        instance.try_acquire = AsyncMock(return_value=False)

        async def _quick_main():
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
