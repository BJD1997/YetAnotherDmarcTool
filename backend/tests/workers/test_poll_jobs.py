"""Regression coverage for poison-message handling in both mailbox poll jobs.

The report mailboxes are public (they're published as rua= in DNS), so
anyone can drop an arbitrary "report" into them. One malformed report must
be skipped, not fail the whole run — a failed run never advances delta_link,
so the next run re-fetches the same poison message and fails again forever.

Graph fetching and MIME parsing are mocked; the loop, report_writer, and
the database are real, since the bug was a Postgres error aborting the
enclosing transaction."""

from sqlalchemy import func, select

from app.db import session as session_module
from app.models.dmarc_aggregate import DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import ConsentStatus, SyncStatus
from app.models.mailbox_connection import MailboxConnection
from app.workers.jobs import hosted_reports_poll_job, mailbox_poll_job

from tests.conftest import seed_org_and_user

GOOD_1 = "good-report-1"
GOOD_2 = "good-report-2"


def _aggregate(report_id: str, *, pct: str = "100", header_from: str = "example.com") -> dict:
    return {
        "report_type": "aggregate",
        "report": {
            "report_metadata": {
                "report_id": report_id,
                "org_name": "google.com",
                "org_email": None,
                "begin_date": "2026-09-20 00:00:00",
                "end_date": "2026-09-21 00:00:00",
            },
            "policy_published": {"domain": "example.com", "p": "reject", "sp": None, "pct": pct, "adkim": "r", "aspf": "r"},
            "records": [
                {
                    "identifiers": {"header_from": header_from},
                    "source": {"ip_address": "203.0.113.10"},
                    "count": 5,
                    "policy_evaluated": {"disposition": "none", "dkim": "pass", "spf": "pass"},
                }
            ],
        },
    }


# Poison placed between two good reports: the second good one proves the
# transaction survived the bad one, not just that the bad one was skipped.
POISON_CASES = {
    "non_numeric_pct": _aggregate("poison", pct="not-a-number"),
    # Exceeds the String(253) column — a Postgres DataError, which aborts the
    # whole transaction unless it's isolated in its own savepoint.
    "oversized_header_from": _aggregate("poison", header_from="a" * 300 + ".example.com"),
}


def _mock_graph(monkeypatch, module, poison: dict, *, recipient: str = "reports@example.com"):
    parsed_by_message = {"m1": _aggregate(GOOD_1), "m2": poison, "m3": _aggregate(GOOD_2)}

    async def _fetch_ids(**_kwargs):
        return list(parsed_by_message), "delta-after-run"

    async def _fetch_mime(*, message_id, **_kwargs):
        return f"To: {recipient}\r\nX-Test-Id: {message_id}\r\n\r\n".encode()

    def _parse(raw_mime: bytes):
        message_id = raw_mime.decode().split("X-Test-Id: ")[1].split("\r\n")[0]
        return parsed_by_message[message_id]

    monkeypatch.setattr(module, "fetch_new_message_ids", _fetch_ids)
    monkeypatch.setattr(module, "fetch_message_raw_mime", _fetch_mime)
    monkeypatch.setattr(module, "parse_report_email", _parse)


async def _written_report_ids(owner_factory) -> set[str]:
    async with owner_factory() as db:
        return set((await db.execute(select(DmarcAggregateReport.report_id))).scalars())


async def _run_org_poll_with_poison(api, monkeypatch, poison: dict):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory, entra=True)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="example.com"))
        db.add(
            MailboxConnection(
                organization_id=org.id, mailbox_address="reports@example.com", consent_status=ConsentStatus.granted
            )
        )
        await db.commit()
    _mock_graph(monkeypatch, mailbox_poll_job, poison)

    await mailbox_poll_job._do_poll(org.id, "tenant-id")

    async with owner_factory() as db:
        connection = (
            await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == org.id))
        ).scalar_one()
    return connection, await _written_report_ids(owner_factory)


async def test_org_poll_skips_non_numeric_pct_and_keeps_going(api, monkeypatch):
    connection, written = await _run_org_poll_with_poison(api, monkeypatch, POISON_CASES["non_numeric_pct"])

    assert connection.last_sync_status == SyncStatus.success
    assert connection.delta_link == "delta-after-run"
    assert written == {GOOD_1, GOOD_2}


async def test_org_poll_skips_oversized_field_without_aborting_transaction(api, monkeypatch):
    connection, written = await _run_org_poll_with_poison(api, monkeypatch, POISON_CASES["oversized_header_from"])

    assert connection.last_sync_status == SyncStatus.success
    assert connection.delta_link == "delta-after-run"
    assert written == {GOOD_1, GOOD_2}


async def test_hosted_poll_skips_poison_and_keeps_going_for_other_orgs(api, monkeypatch):
    """The hosted mailbox is shared by every org using a hosted address, so
    a stall here would cut off all of them at once."""
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory, entra=True)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="example.com", hosted_report_address="rua+abc@hosted.example"))
        await db.commit()
    # conftest's api fixture repoints mailbox_poll_job at the test DB but not
    # this module, which imports async_session_factory directly.
    monkeypatch.setattr(hosted_reports_poll_job, "async_session_factory", session_module.async_session_factory)
    _mock_graph(monkeypatch, hosted_reports_poll_job, POISON_CASES["oversized_header_from"], recipient="rua+abc@hosted.example")

    await hosted_reports_poll_job._do_poll()

    assert await _written_report_ids(owner_factory) == {GOOD_1, GOOD_2}
    async with owner_factory() as db:
        count = (await db.execute(select(func.count()).select_from(DmarcAggregateReport))).scalar_one()
    assert count == 2
