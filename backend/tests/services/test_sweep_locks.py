"""A stale-job reclaim can re-queue a sweep that's still legitimately
running; each sweep must then skip rather than run a second copy."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db import session as db_session
from app.services.dns_checks import domain_verification, scheduled_recheck
from app.services.jobs.advisory_lock import try_advisory_lock


@pytest_asyncio.fixture
async def lock_engine(app_db_url, monkeypatch):
    engine = create_async_engine(app_db_url, poolclass=NullPool)
    monkeypatch.setattr(db_session, "engine", engine)
    yield
    await engine.dispose()


async def test_lock_is_exclusive_until_released(lock_engine):
    async with try_advisory_lock(0x7E57) as first:
        assert first is True
        async with try_advisory_lock(0x7E57) as second:
            assert second is False
    async with try_advisory_lock(0x7E57) as again:
        assert again is True


@pytest.mark.parametrize(
    ("module", "entry_point", "body", "lock_key"),
    [
        (scheduled_recheck, "run_dns_check_sweep", "_sweep_due_domains", scheduled_recheck._DNS_CHECK_SWEEP_LOCK_KEY),
        (
            domain_verification,
            "run_domain_verification_sweep",
            "_verify_pending_domains",
            domain_verification._VERIFICATION_SWEEP_LOCK_KEY,
        ),
    ],
)
async def test_sweep_skips_while_another_copy_holds_the_lock(lock_engine, monkeypatch, module, entry_point, body, lock_key):
    runs = []

    async def _fake_body():
        runs.append(1)

    monkeypatch.setattr(module, body, _fake_body)

    async with try_advisory_lock(lock_key):
        await getattr(module, entry_point)()
    assert runs == []

    await getattr(module, entry_point)()
    assert runs == [1]
