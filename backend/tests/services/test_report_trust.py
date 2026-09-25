"""Forged reports: anyone can mail a reporting address. A report whose email
fails sender authentication is kept but left out of every stat, and a
genuine copy with the same natural key replaces it."""

from sqlalchemy import func, select

from app.db.report_trust import INCLUDE_UNVERIFIED
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import ConsentStatus
from app.models.mailbox_connection import MailboxConnection
from app.services.ingestion.sender_auth import SenderAuth, authenticate_report_sender
from app.workers.jobs import mailbox_poll_job

from tests.conftest import seed_org_and_user
from tests.workers.test_poll_jobs import _aggregate


def _mime(*auth_results: str, sender: str = "noreply-dmarc-support@google.com") -> bytes:
    headers = "".join(f"Authentication-Results: {value}\r\n" for value in auth_results)
    return f"{headers}From: {sender}\r\nTo: reports@example.com\r\n\r\nbody".encode()


# ---------- reading the verdict ----------


def test_exchange_pass_verdict():
    raw = _mime("spf=pass smtp.mailfrom=google.com; dkim=pass header.d=google.com;dmarc=pass action=none header.from=google.com;compauth=pass")
    assert authenticate_report_sender(raw) == SenderAuth(verified=True, domain="google.com")


def test_bestguesspass_counts_as_verified():
    raw = _mime("spf=pass; dkim=none; dmarc=bestguesspass action=none header.from=small-isp.example")
    assert authenticate_report_sender(raw).verified is True


def test_failed_dmarc_is_unverified():
    raw = _mime("spf=fail; dkim=none; dmarc=fail action=none header.from=google.com")
    assert authenticate_report_sender(raw) == SenderAuth(verified=False, domain="google.com")


def test_only_the_receiving_servers_header_counts():
    # A forger can write their own "dmarc=pass" header into the message; the
    # receiving server's verdict is prepended above it.
    raw = _mime("dmarc=fail action=none header.from=google.com", "dmarc=pass header.from=google.com")
    assert authenticate_report_sender(raw).verified is False


def test_no_verdict_is_unchecked_not_unverified():
    assert authenticate_report_sender(_mime()) == SenderAuth(verified=None, domain="google.com")


# ---------- ingestion ----------

FORGED = _mime("dmarc=fail action=none header.from=google.com")
GENUINE = _mime("dmarc=pass action=none header.from=google.com")


async def _setup_org(owner_factory):
    org, _user = await seed_org_and_user(owner_factory, entra=True)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="example.com"))
        db.add(MailboxConnection(organization_id=org.id, mailbox_address="reports@example.com", consent_status=ConsentStatus.granted))
        await db.commit()
    return org


def _mock_mailbox(monkeypatch, messages: dict[str, tuple[bytes, dict]]):
    async def _fetch_ids(**_kwargs):
        return list(messages), "delta"

    async def _fetch_mime(*, message_id, **_kwargs):
        return messages[message_id][0] + f"\r\nX-Id: {message_id}".encode()

    def _parse(raw_mime: bytes):
        return messages[raw_mime.decode().rsplit("X-Id: ", 1)[1]][1]

    monkeypatch.setattr(mailbox_poll_job, "fetch_new_message_ids", _fetch_ids)
    monkeypatch.setattr(mailbox_poll_job, "fetch_message_raw_mime", _fetch_mime)
    monkeypatch.setattr(mailbox_poll_job, "parse_report_email", _parse)


async def test_forged_report_is_kept_but_left_out_of_stats(api, monkeypatch):
    _client, owner_factory = api
    org = await _setup_org(owner_factory)
    _mock_mailbox(monkeypatch, {"forged": (FORGED, _aggregate("r-1"))})

    await mailbox_poll_job._do_poll(org.id, "tenant-id")

    async with owner_factory() as db:
        # Column-only aggregate over records with no join — the shape most
        # stats queries take — is filtered too, not just entity loads.
        volume = (await db.execute(select(func.coalesce(func.sum(DmarcAggregateRecord.count), 0)))).scalar_one()
        visible = (await db.execute(select(func.count()).select_from(DmarcAggregateReport))).scalar_one()
        stored = (
            await db.execute(
                select(DmarcAggregateReport.sender_verified).execution_options(**{INCLUDE_UNVERIFIED: True})
            )
        ).scalars().all()
    assert (volume, visible) == (0, 0)
    assert stored == [False]


async def test_genuine_copy_replaces_a_forged_one_with_the_same_key(api, monkeypatch):
    _client, owner_factory = api
    org = await _setup_org(owner_factory)
    _mock_mailbox(
        monkeypatch,
        {"forged": (FORGED, _aggregate("r-1")), "genuine": (GENUINE, _aggregate("r-1"))},
    )

    await mailbox_poll_job._do_poll(org.id, "tenant-id")

    async with owner_factory() as db:
        rows = (
            await db.execute(
                select(DmarcAggregateReport.source_message_id, DmarcAggregateReport.sender_verified).execution_options(
                    **{INCLUDE_UNVERIFIED: True}
                )
            )
        ).all()
        volume = (await db.execute(select(func.sum(DmarcAggregateRecord.count)))).scalar_one()
    assert [(mid, verified) for mid, verified in rows] == [("genuine", True)]
    assert volume == 5


async def test_forged_copy_cannot_displace_a_genuine_one(api, monkeypatch):
    _client, owner_factory = api
    org = await _setup_org(owner_factory)
    _mock_mailbox(
        monkeypatch,
        {"genuine": (GENUINE, _aggregate("r-1")), "forged": (FORGED, _aggregate("r-1"))},
    )

    await mailbox_poll_job._do_poll(org.id, "tenant-id")

    async with owner_factory() as db:
        rows = (
            await db.execute(
                select(DmarcAggregateReport.source_message_id).execution_options(**{INCLUDE_UNVERIFIED: True})
            )
        ).scalars().all()
    assert rows == ["genuine"]
