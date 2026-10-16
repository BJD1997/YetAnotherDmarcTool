"""The hosted reporting mailbox read over IMAP: routed by recipient, filed
away once stored, and nothing read before the mail server's ID is set."""

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import session as session_module
from app.models.dmarc_aggregate import DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus, SyncStatus
from app.models.hosted_reports_poll_state import HostedReportsPollState
from app.workers.jobs import hosted_reports_poll_job
from tests.conftest import seed_org_and_user
from tests.services.test_imap_mailbox import FakeMailbox
from tests.workers.test_poll_jobs import _aggregate

OWN = "mx.hosted.example"
ADDRESS = "rua+aaaaaaaaaaaa@hosted.example"


def _raw(test_id: str, *, to: str, extra: str = "") -> bytes:
    return (
        f"Authentication-Results: {OWN}; dmarc=pass header.from=google.com\r\n{extra}"
        f"From: noreply-dmarc-support@google.com\r\nTo: {to}\r\nMessage-ID: <{test_id}@google.com>\r\n"
        f"X-Test-Id: {test_id}\r\n\r\n"
    ).encode()


@pytest.fixture
def hosted_imap(monkeypatch):
    for name, value in {
        "hosted_reports_source": "imap", "hosted_reports_mailbox_address": "rua@hosted.example",
        "hosted_reports_imap_host": "mail.internal", "hosted_reports_imap_username": "rua", "hosted_reports_imap_password": "pw",
        "hosted_reports_imap_authserv_id": OWN, "hosted_reports_tenant_id": None,
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(hosted_reports_poll_job, "async_session_factory", session_module.async_session_factory)
    seen = {}

    async def _address(imap_settings, *, allow_private=False):
        seen["allow_private"] = allow_private
        return "10.0.0.25"

    monkeypatch.setattr(hosted_reports_poll_job, "resolve_address", _address)
    return seen


def _serve(monkeypatch, mailbox: FakeMailbox, parsed: dict[str, dict]):
    monkeypatch.setattr(hosted_reports_poll_job.ImapMailbox, "connect", classmethod(lambda cls, s, a: mailbox))

    def _parse(raw: bytes):
        test_id = raw.decode().split("X-Test-Id: ")[1].split("\r\n")[0]
        if test_id not in parsed:
            raise hosted_reports_poll_job.UnparseableReportError("not a report")
        return parsed[test_id]

    monkeypatch.setattr(hosted_reports_poll_job, "parse_report_email", _parse)


async def _customer(owner_factory):
    org, _ = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified,
                      hosted_report_address=ADDRESS))
        await db.commit()
    return org


async def test_the_hosted_mailbox_is_ready_with_imap_settings(hosted_imap):
    assert settings.hosted_mailbox_ready is True


async def test_reports_are_routed_by_recipient_and_filed_away(api, monkeypatch, hosted_imap):
    _client, owner_factory = api
    org = await _customer(owner_factory)
    mailbox = FakeMailbox({
        b"1": _raw("r1", to=ADDRESS),
        b"2": _raw("r2", to="someone@else.example", extra=f"Delivered-To: {ADDRESS}\r\n"),
        b"3": _raw("lost", to="rua+unknown@hosted.example"),
        b"4": _raw("junk", to=ADDRESS),
    })
    _serve(monkeypatch, mailbox, {"r1": _aggregate("report-1"), "r2": _aggregate("report-2"), "lost": _aggregate("report-3")})

    await hosted_reports_poll_job._do_imap_poll()

    assert hosted_imap["allow_private"] is True
    assert mailbox.moved == [b"1", b"2"]
    assert mailbox.read == {b"1", b"2", b"3", b"4"}
    async with owner_factory() as db:
        rows = (await db.execute(select(DmarcAggregateReport).execution_options(include_unverified_reports=True))).scalars().all()
        assert sorted(r.report_id for r in rows) == ["report-1", "report-2"]
        assert {r.organization_id for r in rows} == {org.id}
        state = (await db.execute(select(HostedReportsPollState))).scalar_one()
        assert state.last_sync_status == SyncStatus.success


async def test_nothing_is_read_before_the_server_id_is_set(api, monkeypatch, hosted_imap):
    _client, owner_factory = api
    await _customer(owner_factory)
    monkeypatch.setattr(settings, "hosted_reports_imap_authserv_id", None)
    mailbox = FakeMailbox({b"1": _raw("r1", to=ADDRESS)})
    _serve(monkeypatch, mailbox, {"r1": _aggregate("report-1")})

    await hosted_reports_poll_job._do_imap_poll()

    assert mailbox.read == set()
    async with owner_factory() as db:
        state = (await db.execute(select(HostedReportsPollState))).scalar_one()
        assert state.last_sync_status == SyncStatus.error and "HOSTED_REPORTS_IMAP_AUTHSERV_ID" in state.last_sync_error
        assert "mx.mail.example" in state.last_sync_error  # what the server says it is
