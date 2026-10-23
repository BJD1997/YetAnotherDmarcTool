"""An organization's Google Workspace report mailbox: only on a verified
domain, saved after a working connection test, polled and filed away."""

import pytest
from sqlalchemy import select

from app.models.dmarc_aggregate import DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import ConsentStatus, DomainVerificationStatus, MailboxKind, UserRole
from app.models.mailbox_connection import MailboxConnection
from app.services.gmail import client as gmail_client
from app.services.gmail import poll as gmail_poll
from app.workers.jobs import mailbox_poll_job
from tests.conftest import login_as, seed_org_and_user
from tests.workers.test_poll_jobs import _aggregate


class FakeGmail:
    def __init__(self, messages: dict[str, bytes]):
        self.messages = messages
        self.read: set[str] = set()
        self.moved: list[str] = []

    async def inbox_count(self):
        return len(self.messages)

    async def unread_ids(self):
        return [m for m in self.messages if m not in self.read]

    async def size(self, gmail_id):
        return len(self.messages[gmail_id])

    async def fetch_raw(self, gmail_id):
        return self.messages[gmail_id]

    async def mark_read(self, gmail_id):
        self.read.add(gmail_id)

    async def mark_read_and_move(self, gmail_id):
        self.read.add(gmail_id)
        self.moved.append(gmail_id)


def _raw(test_id: str) -> bytes:
    return (
        "Authentication-Results: mx.google.com; dmarc=pass header.from=google.com\r\n"
        f"From: noreply-dmarc-support@google.com\r\nMessage-ID: <{test_id}@google.com>\r\nX-Test-Id: {test_id}\r\n\r\n"
    ).encode()


@pytest.fixture
def fake_gmail(monkeypatch):
    state = {"mailbox": FakeGmail({})}
    monkeypatch.setattr(gmail_poll, "mailbox_for", lambda address: state["mailbox"])
    return state


async def _admin(client, owner_factory):
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="acme.example", verification_status=DomainVerificationStatus.verified))
        db.add(Domain(organization_id=org.id, name="pending.example"))
        await db.commit()
    await login_as(client, owner_factory, user)
    return org


async def test_only_a_mailbox_on_a_verified_domain(api, fake_gmail):
    client, owner_factory = api
    await _admin(client, owner_factory)
    for address in ("dmarc@pending.example", "dmarc@victim.example"):
        response = await client.post("/api/mailbox-connection/google/test", json={"mailbox_address": address})
        assert response.status_code == 400 and "verified domains" in response.text
    ok = await client.post("/api/mailbox-connection/google/test", json={"mailbox_address": "dmarc@acme.example"})
    assert ok.json() == {"ok": True, "messages": 0}


async def test_saving_replaces_the_mailbox_after_a_working_test(api, fake_gmail, monkeypatch):
    client, owner_factory = api
    org = await _admin(client, owner_factory)

    async def _refused():
        raise gmail_client.GmailError("Google refused: the Workspace admin hasn't granted domain-wide delegation")

    fake_gmail["mailbox"].inbox_count = _refused
    refused = await client.put("/api/mailbox-connection/google", json={"mailbox_address": "dmarc@acme.example"})
    assert refused.status_code == 400 and "domain-wide delegation" in refused.text

    fake_gmail["mailbox"] = FakeGmail({})
    saved = await client.put("/api/mailbox-connection/google", json={"mailbox_address": "Dmarc@Acme.example"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["kind"] == "google" and saved.json()["mailbox_address"] == "dmarc@acme.example"


async def test_a_poll_stores_reports_and_files_them_away(api, fake_gmail, monkeypatch):
    _client, owner_factory = api
    org, _ = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified))
        db.add(MailboxConnection(organization_id=org.id, mailbox_address="dmarc@example.com", kind=MailboxKind.google,
                                 consent_status=ConsentStatus.granted))
        await db.commit()
    fake_gmail["mailbox"] = FakeGmail({"m1": _raw("r1"), "m2": _raw("junk"), "m3": _raw("r2")})

    def _parse(raw: bytes):
        test_id = raw.decode().split("X-Test-Id: ")[1].split("\r\n")[0]
        if test_id == "junk":
            raise gmail_poll.UnparseableReportError("not a report")
        return _aggregate("report-" + test_id)

    monkeypatch.setattr(gmail_poll, "parse_report_email", _parse)
    from app.db import session as session_module

    monkeypatch.setattr(mailbox_poll_job, "async_session_factory", session_module.async_session_factory)
    await mailbox_poll_job.poll_org_mailbox(org.id, None)

    assert fake_gmail["mailbox"].moved == ["m1", "m3"]
    assert fake_gmail["mailbox"].read == {"m1", "m2", "m3"}
    async with owner_factory() as db:
        rows = (await db.execute(select(DmarcAggregateReport))).scalars().all()
        assert sorted(r.report_id for r in rows) == ["report-r1", "report-r2"]
        connection = (await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == org.id))).scalar_one()
        assert connection.last_sync_status.value == "success"
