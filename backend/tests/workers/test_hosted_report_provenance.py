"""Can someone put fabricated aggregate data into a customer's dashboard
through the shared hosted reporting mailbox?

Every message here is synthetic and shaped the way Exchange Online stores
it (receiving server's Authentication-Results on top, AuthAs: Anonymous for
external mail). Graph and MIME parsing are mocked; the poll loop,
report_writer, sender check, RLS and Postgres are real."""

from sqlalchemy import select

from app.db import session as session_module
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthMethod, OrganizationStatus, UserRole, UserStatus
from app.models.organization import Organization
from app.models.user import User
from app.workers.jobs import hosted_reports_poll_job

from tests.conftest import login_as, seed_org_and_user

VICTIM_ADDRESS = "rua+aaaaaaaaaaaa@hosted.example"
OTHER_ADDRESS = "rua+bbbbbbbbbbbb@hosted.example"


def _report(report_id: str, *, reporter_email: str, org_name: str, about: str, ip: str = "198.51.100.66") -> dict:
    return {
        "report_type": "aggregate",
        "report": {
            "report_metadata": {
                "report_id": report_id,
                "org_name": org_name,
                "org_email": reporter_email,
                "begin_date": "2026-09-20 00:00:00",
                "end_date": "2026-09-21 00:00:00",
            },
            "policy_published": {"domain": about, "p": "reject", "sp": None, "pct": "100", "adkim": "r", "aspf": "r"},
            "records": [
                {
                    "identifiers": {"header_from": about},
                    "source": {"ip_address": ip},
                    "count": 5000,
                    "policy_evaluated": {"disposition": "none", "dkim": "pass", "spf": "pass"},
                }
            ],
        },
    }


def _exchange_mime(*, sender_domain: str, to: str) -> bytes:
    """An external email that passed DMARC for sender_domain, as Exchange
    Online stores it in the mailbox."""
    return (
        f"Authentication-Results: spf=pass smtp.mailfrom={sender_domain}; dkim=pass header.d={sender_domain};"
        f"dmarc=pass action=none header.from={sender_domain};compauth=pass\r\n"
        "X-MS-Exchange-Organization-AuthAs: Anonymous\r\n"
        f"From: dmarc@{sender_domain}\r\nTo: {to}\r\n\r\n"
    ).encode()


async def _setup(owner_factory, monkeypatch, messages: dict[str, tuple[bytes, dict]]):
    """Two customers on the shared hosted mailbox: victim (victim.example)
    and other (other.example)."""
    victim, _user = await seed_org_and_user(owner_factory, entra=True)
    async with owner_factory() as db:
        other = Organization(name="Other Org", status=OrganizationStatus.active)
        db.add(other)
        await db.flush()
        db.add(Domain(organization_id=victim.id, name="victim.example", hosted_report_address=VICTIM_ADDRESS))
        db.add(Domain(organization_id=other.id, name="other.example", hosted_report_address=OTHER_ADDRESS))
        await db.refresh(other)
        await db.commit()

    async def _fetch_ids(**_kwargs):
        return list(messages), "delta-after-run"

    async def _fetch_mime(*, message_id, **_kwargs):
        return messages[message_id][0] + f"X-Test-Id: {message_id}\r\n".encode()

    def _parse(raw_mime: bytes):
        return messages[raw_mime.decode().split("X-Test-Id: ")[1].split("\r\n")[0]][1]

    monkeypatch.setattr(hosted_reports_poll_job, "async_session_factory", session_module.async_session_factory)
    monkeypatch.setattr(hosted_reports_poll_job, "fetch_new_message_ids", _fetch_ids)
    monkeypatch.setattr(hosted_reports_poll_job, "fetch_message_raw_mime", _fetch_mime)
    monkeypatch.setattr(hosted_reports_poll_job, "parse_report_email", _parse)
    await hosted_reports_poll_job._do_poll()
    return victim, other


async def _stored(owner_factory) -> dict[str, DmarcAggregateReport]:
    async with owner_factory() as db:
        rows = (await db.execute(select(DmarcAggregateReport).execution_options(include_unverified_reports=True))).scalars()
        return {r.report_id: r for r in rows}


async def test_genuine_reporter_is_counted(api, monkeypatch):
    """Control: a real Google report must keep working."""
    _client, owner_factory = api
    victim, _other = await _setup(owner_factory, monkeypatch, {
        "m1": (
            _exchange_mime(sender_domain="google.com", to=VICTIM_ADDRESS),
            _report("google-1", reporter_email="noreply-dmarc-support@google.com", org_name="google.com", about="victim.example"),
        ),
    })
    report = (await _stored(owner_factory))["google-1"]
    assert report.organization_id == victim.id
    assert report.sender_verified is True


async def test_self_authored_report_is_not_counted(api, monkeypatch):
    """The audit's main claim. attacker.example passes DMARC for its own
    domain and names itself as the reporter, so 'sender is who the report
    says' holds — but nothing shows attacker.example ever received any of
    victim.example's mail. Its numbers must not count."""
    _client, owner_factory = api
    victim, _other = await _setup(owner_factory, monkeypatch, {
        "m1": (
            _exchange_mime(sender_domain="attacker.example", to=VICTIM_ADDRESS),
            _report("forged-1", reporter_email="dmarc@attacker.example", org_name="attacker.example", about="victim.example"),
        ),
    })
    report = (await _stored(owner_factory))["forged-1"]
    assert report.organization_id == victim.id  # routed to the right mailbox owner, as expected
    assert report.sender_verified is not True, "fabricated report from a self-chosen reporter was counted"


async def test_report_about_another_tenants_domain_stays_out_of_that_tenant(api, monkeypatch):
    """Tenant isolation: a report sent to the victim's address but about
    other.example must not show up in Other Org, nor be linked to any
    domain in the victim's org."""
    _client, owner_factory = api
    victim, other = await _setup(owner_factory, monkeypatch, {
        "m1": (
            _exchange_mime(sender_domain="google.com", to=VICTIM_ADDRESS),
            _report("cross-1", reporter_email="noreply-dmarc-support@google.com", org_name="google.com", about="other.example"),
        ),
    })
    report = (await _stored(owner_factory))["cross-1"]
    assert report.organization_id == victim.id
    assert report.domain_id is None
    async with owner_factory() as db:
        other_records = (await db.execute(
            select(DmarcAggregateRecord).where(DmarcAggregateRecord.organization_id == other.id)
            .execution_options(include_unverified_reports=True)
        )).scalars().all()
    assert other_records == []


# ---------- trusting a reporter ----------

FORGED_TO_BOTH = {
    "m1": (
        _exchange_mime(sender_domain="attacker.example", to=VICTIM_ADDRESS),
        _report("to-victim", reporter_email="dmarc@attacker.example", org_name="attacker.example", about="victim.example"),
    ),
    "m2": (
        _exchange_mime(sender_domain="attacker.example", to=OTHER_ADDRESS),
        _report("to-other", reporter_email="dmarc@attacker.example", org_name="attacker.example", about="other.example"),
    ),
}


async def _user(owner_factory, org_id, role: UserRole) -> User:
    async with owner_factory() as db:
        user = User(
            organization_id=org_id, email=f"{role.value}-{org_id}@test.example", display_name="U",
            role=role, status=UserStatus.active, auth_method=AuthMethod.local,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        await db.commit()
    return user


async def _victim_domain_id(owner_factory, victim_id):
    async with owner_factory() as db:
        return (await db.execute(select(Domain.id).where(Domain.organization_id == victim_id))).scalar_one()


async def test_unknown_reporter_is_listed_for_review_as_trustable(api, monkeypatch):
    client, owner_factory = api
    victim, _other = await _setup(owner_factory, monkeypatch, FORGED_TO_BOTH)
    await login_as(client, owner_factory, await _user(owner_factory, victim.id, UserRole.member))

    listed = (await client.get(f"/api/domains/{await _victim_domain_id(owner_factory, victim.id)}/left-out-reports")).json()

    assert [(r["sender_domain"], r["reason"], r["trustable"]) for r in listed] == [
        ("attacker.example", "Sent by attacker.example, a reporter you haven't trusted yet", True),
    ]


async def test_trusting_a_reporter_counts_its_reports_in_that_org_only(api, monkeypatch):
    """Tenant isolation: one org's trust decision never makes the same
    sender count for another org, nor is it visible to it."""
    client, owner_factory = api
    victim, other = await _setup(owner_factory, monkeypatch, FORGED_TO_BOTH)
    async with owner_factory() as db:
        victim_admin = (await db.execute(select(User).where(User.organization_id == victim.id))).scalar_one()
    await login_as(client, owner_factory, victim_admin)

    response = await client.post("/api/organizations/current/trusted-reporters", json={"domain": "Attacker.Example."})
    assert response.status_code == 200
    assert [r["domain"] for r in response.json()] == ["attacker.example"]

    stored = await _stored(owner_factory)
    assert stored["to-victim"].sender_verified is True  # already-received report re-evaluated
    assert stored["to-other"].sender_verified is False
    async with owner_factory() as db:
        other_counted = (await db.execute(
            select(DmarcAggregateRecord).where(DmarcAggregateRecord.organization_id == other.id)
        )).scalars().all()
    assert other_counted == []  # the global filter still hides Other Org's copy

    await login_as(client, owner_factory, await _user(owner_factory, other.id, UserRole.org_admin))
    assert (await client.get("/api/organizations/current/trusted-reporters")).json() == []


async def test_removing_a_trusted_reporter_leaves_its_reports_out_again(api, monkeypatch):
    client, owner_factory = api
    victim, _other = await _setup(owner_factory, monkeypatch, FORGED_TO_BOTH)
    async with owner_factory() as db:
        victim_admin = (await db.execute(select(User).where(User.organization_id == victim.id))).scalar_one()
    await login_as(client, owner_factory, victim_admin)
    await client.post("/api/organizations/current/trusted-reporters", json={"domain": "attacker.example"})

    response = await client.delete("/api/organizations/current/trusted-reporters/attacker.example")

    assert response.status_code == 200 and response.json() == []
    assert (await _stored(owner_factory))["to-victim"].sender_verified is False
    assert (await client.delete("/api/organizations/current/trusted-reporters/attacker.example")).status_code == 404


async def test_only_org_admins_can_trust_a_reporter(api, monkeypatch):
    client, owner_factory = api
    victim, _other = await _setup(owner_factory, monkeypatch, FORGED_TO_BOTH)
    await login_as(client, owner_factory, await _user(owner_factory, victim.id, UserRole.member))

    response = await client.post("/api/organizations/current/trusted-reporters", json={"domain": "attacker.example"})

    assert response.status_code == 403
    assert (await _stored(owner_factory))["to-victim"].sender_verified is False


async def test_trusted_reporter_must_be_a_domain_name(api, monkeypatch):
    client, owner_factory = api
    victim, _other = await _setup(owner_factory, monkeypatch, {})
    async with owner_factory() as db:
        victim_admin = (await db.execute(select(User).where(User.organization_id == victim.id))).scalar_one()
    await login_as(client, owner_factory, victim_admin)

    for bad in ("", "localhost", "evil example", "a@b.example", "x" * 300 + ".example"):
        response = await client.post("/api/organizations/current/trusted-reporters", json={"domain": bad})
        assert response.status_code == 422, bad
