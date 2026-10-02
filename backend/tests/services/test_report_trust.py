"""Forged reports: anyone can mail a reporting address. A report whose email
fails sender authentication is kept but left out of every stat, and a
genuine copy with the same natural key replaces it."""

import itertools

import pytest
from sqlalchemy import Boolean, String, func, literal, select

from app.db.report_trust import INCLUDE_UNVERIFIED
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import ConsentStatus
from app.models.mailbox_connection import MailboxConnection
from app.models.enums import ReportSenderCheck
from app.services.ingestion.sender_auth import (
    UNCHECKED,
    SenderAuth,
    authenticate_report_sender,
    claimed_domain,
    decide,
    decide_sql,
)
from app.workers.jobs import mailbox_poll_job

from app.models.user import User
from tests.conftest import login_as, seed_org_and_user
from tests.workers.test_poll_jobs import _aggregate


def _mime(*auth_results: str, sender: str = "noreply-dmarc-support@google.com", auth_as: str | None = "Anonymous") -> bytes:
    headers = "".join(f"Authentication-Results: {value}\r\n" for value in auth_results)
    if auth_as is not None:
        headers += f"X-MS-Exchange-Organization-AuthAs: {auth_as}\r\n"
    return f"{headers}From: {sender}\r\nTo: reports@example.com\r\n\r\nbody".encode()


# ---------- reading the facts ----------


def test_exchange_pass_verdict():
    raw = _mime("spf=pass smtp.mailfrom=google.com; dkim=pass header.d=google.com;dmarc=pass action=none header.from=google.com;compauth=pass")
    assert authenticate_report_sender(raw) == SenderAuth(origin="external", dmarc="pass", domain="google.com")


def test_only_the_receiving_servers_header_counts():
    # A forger can write their own "dmarc=pass" header into the message; the
    # receiving server's verdict is prepended above it.
    raw = _mime("dmarc=fail action=none header.from=google.com", "dmarc=pass header.from=google.com")
    assert authenticate_report_sender(raw).dmarc == "fail"


def test_no_verdict_is_recorded_as_missing():
    assert authenticate_report_sender(_mime()).dmarc == "missing"


def test_origin_needs_exchanges_external_marker():
    assert authenticate_report_sender(_mime("dmarc=pass header.from=google.com", auth_as="Internal")).origin == "internal"
    assert authenticate_report_sender(_mime("dmarc=pass header.from=google.com", auth_as=None)).origin == "internal"


def test_reporter_match_is_recorded():
    passing = SenderAuth(origin="external", dmarc="pass", domain="evil.example")
    assert passing.for_reporter(["google.com"]).matches_reporter is False
    assert SenderAuth(origin="external", dmarc="pass", domain="mail.microsoft.com").for_reporter(["microsoft.com"]).matches_reporter is True
    assert SenderAuth(origin="external", dmarc="pass", domain="dmarc.yahoo.com").for_reporter(["yahooinc.com"]).matches_reporter is True
    # Nothing named to compare against (forensic reports).
    assert passing.for_reporter([]).matches_reporter is None


def test_claimed_domain_reads_contact_fields():
    assert claimed_domain("noreply-dmarc-support@google.com") == "google.com"
    assert claimed_domain("mailto:tlsrpt@microsoft.com") == "microsoft.com"
    assert claimed_domain("https://reports.example.net/tls") == "reports.example.net"
    assert claimed_domain("google.com") == "google.com"
    assert claimed_domain("Google Inc.") is None


# ---------- the three settings ----------

STANDARD, STRICT, OFF = ReportSenderCheck.standard, ReportSenderCheck.strict, ReportSenderCheck.off


@pytest.mark.parametrize(
    ("sender", "expected"),
    [
        # (origin, dmarc, matches) -> (standard, strict, off)
        (("external", "pass", True), (True, True, True)),
        (("external", "bestguesspass", None), (True, True, True)),
        (("external", "none", True), (True, False, True)),  # reporter without DMARC
        (("external", "missing", True), (True, False, True)),
        (("external", "fail", True), (False, False, True)),  # e.g. spoofed google.com
        (("external", "pass", False), (False, False, True)),  # own domain, claims Google
        (("internal", "pass", True), (False, False, True)),
    ],
)
def test_decide(sender, expected):
    auth = SenderAuth(origin=sender[0], dmarc=sender[1], domain="x.example", matches_reporter=sender[2])
    assert (decide(STANDARD, auth), decide(STRICT, auth), decide(OFF, auth)) == expected


def test_unchecked_reports_always_count():
    assert {decide(mode, UNCHECKED) for mode in ReportSenderCheck} == {None}


async def test_sql_rule_matches_python_rule(rls_sessions):
    """decide_sql re-evaluates stored reports; it must give the same answer
    as decide() does at ingestion, for every combination of facts."""
    owner, _ = rls_sessions
    combos = list(itertools.product(["external", "internal", None], ["pass", "bestguesspass", "none", "missing", "fail", None], [True, False, None]))
    for mode in ReportSenderCheck:
        for origin, dmarc, matches in combos:
            auth = SenderAuth(origin=origin, dmarc=dmarc, domain="x.example", matches_reporter=matches)
            row = select(
                decide_sql(mode, _Facts(origin, dmarc, matches))
            )
            assert (await owner.execute(row)).scalar_one() == decide(mode, auth), (mode, origin, dmarc, matches)


class _Facts:
    """Literal stand-ins for a report table's sender_* columns."""

    def __init__(self, origin, dmarc, matches):
        self.sender_origin = literal(origin, String)
        self.sender_dmarc = literal(dmarc, String)
        self.sender_matches_reporter = literal(matches, Boolean)


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


async def test_report_from_a_domain_other_than_its_named_reporter_is_left_out(api, monkeypatch):
    """Passing DMARC for your own domain doesn't let you speak for Google."""
    _client, owner_factory = api
    org = await _setup_org(owner_factory)
    impostor = _mime("dmarc=pass action=none header.from=evil.example", sender="reports@evil.example")
    report = _aggregate("r-1")
    report["report"]["report_metadata"]["org_email"] = "noreply-dmarc-support@google.com"
    _mock_mailbox(monkeypatch, {"impostor": (impostor, report)})

    await mailbox_poll_job._do_poll(org.id, "tenant-id")

    async with owner_factory() as db:
        stored = (
            await db.execute(
                select(DmarcAggregateReport.sender_verified, DmarcAggregateReport.sender_domain).execution_options(
                    **{INCLUDE_UNVERIFIED: True}
                )
            )
        ).all()
    assert [tuple(r) for r in stored] == [(False, "evil.example")]


# ---------- the setting and the review list ----------


async def test_changing_the_setting_re_evaluates_stored_reports(api, monkeypatch):
    client, owner_factory = api
    org = await _setup_org(owner_factory)
    no_dmarc = _mime("dmarc=none action=none header.from=google.com")
    _mock_mailbox(monkeypatch, {"forged": (FORGED, _aggregate("r-1")), "no-dmarc": (no_dmarc, _aggregate("r-2"))})
    await mailbox_poll_job._do_poll(org.id, "tenant-id")
    async with owner_factory() as db:
        admin = (await db.execute(select(User).where(User.organization_id == org.id))).scalar_one()
    await login_as(client, owner_factory, admin)

    async def counted() -> set[str]:
        async with owner_factory() as db:
            return set((await db.execute(select(DmarcAggregateReport.report_id))).scalars())

    assert await counted() == {"r-2"}  # standard: only the explicit DMARC failure is left out
    for mode, expected in (("strict", set()), ("off", {"r-1", "r-2"}), ("standard", {"r-2"})):
        response = await client.patch("/api/organizations/current", json={"name": "Test Org", "report_sender_check": mode})
        assert response.json()["report_sender_check"] == mode
        assert await counted() == expected, mode
    async with owner_factory() as db:
        volume = (await db.execute(select(func.sum(DmarcAggregateRecord.count)))).scalar_one()
    assert volume == 5  # records follow their report


async def test_left_out_reports_can_be_reviewed(api, monkeypatch):
    client, owner_factory = api
    org = await _setup_org(owner_factory)
    impostor = _mime("dmarc=pass action=none header.from=evil.example", sender="reports@evil.example")
    report = _aggregate("r-2")
    report["report"]["report_metadata"]["org_email"] = "noreply-dmarc-support@google.com"
    _mock_mailbox(monkeypatch, {"forged": (FORGED, _aggregate("r-1")), "impostor": (impostor, report)})
    await mailbox_poll_job._do_poll(org.id, "tenant-id")
    async with owner_factory() as db:
        admin = (await db.execute(select(User).where(User.organization_id == org.id))).scalar_one()
        domain = (await db.execute(select(Domain).where(Domain.organization_id == org.id))).scalar_one()
    await login_as(client, owner_factory, admin)

    listed = (await client.get(f"/api/domains/{domain.id}/left-out-reports")).json()

    assert sorted((r["type"], r["reporter"], r["reason"]) for r in listed) == [
        ("DMARC", "google.com", "Failed DMARC for google.com"),
        ("DMARC", "google.com", "Sent by evil.example, not by the reporter it names"),
    ]

