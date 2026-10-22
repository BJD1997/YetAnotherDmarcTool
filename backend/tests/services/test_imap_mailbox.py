"""IMAP mailboxes: the sender check trusts only the mailbox's own server, a
poll stores reports and files them away, and the API only saves settings
that work. The IMAP server itself is a fake here; the client was also run
against a real one (GreenMail) during development."""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.services.crypto import secrets as crypto_secrets

from app.models.dmarc_aggregate import DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import ConsentStatus, DomainVerificationStatus, ImapSecurity, MailboxKind, SyncStatus, UserRole
from app.models.mailbox_connection import MailboxConnection
from app.repositories.mailbox_connections import list_orgs_with_granted_mailbox_connections
from app.services.imap import client as imap_client
from app.services.imap import poll as imap_poll
from app.services.ingestion.sender_auth import authenticate_report_sender
from app.workers.jobs import mailbox_poll_job

from tests.conftest import login_as, seed_org_and_user
from tests.workers.test_poll_jobs import _aggregate

OWN = "mx.mail.example"


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    """The IMAP password is encrypted at rest, like two-factor secrets."""
    monkeypatch.setattr(settings, "fernet_key", Fernet.generate_key().decode())
    crypto_secrets._fernet.cache_clear()
    yield
    crypto_secrets._fernet.cache_clear()


def _raw(test_id: str, *headers: str) -> bytes:
    return ("".join(f"{h}\r\n" for h in headers) + f"Message-ID: <{test_id}@test>\r\nX-Test-Id: {test_id}\r\n\r\n").encode()


# --- the sender check -------------------------------------------------------

def test_only_the_mailbox_servers_own_check_counts():
    genuine = _raw("a", f"Authentication-Results: {OWN}; dmarc=pass header.from=google.com")
    assert authenticate_report_sender(genuine, imap_authserv_id=OWN).origin == "external"
    assert authenticate_report_sender(genuine, imap_authserv_id=OWN).domain == "google.com"


def test_a_fake_authentication_results_header_is_ignored():
    # The sender wrote one claiming google.com; the receiving server's own,
    # on top, says otherwise.
    forged = _raw(
        "b",
        f"Authentication-Results: {OWN}; dmarc=fail header.from=evil.example",
        f"Authentication-Results: {OWN}; dmarc=pass header.from=google.com",
    )
    sender = authenticate_report_sender(forged, imap_authserv_id=OWN)
    assert sender.dmarc == "fail" and sender.domain == "evil.example"


def test_without_the_servers_own_check_nothing_is_trusted():
    only_forged = _raw("c", "Authentication-Results: mx.google.com; dmarc=pass header.from=google.com")
    assert authenticate_report_sender(only_forged, imap_authserv_id=OWN).origin == "unrecognized"
    assert authenticate_report_sender(only_forged, imap_authserv_id="").origin == "unrecognized"


def test_a_server_that_signs_each_check_separately():
    # One header per check, each with its own ID under the provider's domain;
    # the top one (BIMI) has no DMARC result.
    split = _raw(
        "d",
        "Authentication-Results: bimi.provider.example; bimi=skipped reason=insufficient dmarc",
        "Authentication-Results: dmarc.provider.example; dmarc=pass header.from=google.com",
        "Authentication-Results: dkim-verifier.provider.example; dkim=pass header.d=google.com",
        "Authentication-Results: dmarc.provider.example; dmarc=pass header.from=evil.example",
    )
    sender = authenticate_report_sender(split, imap_authserv_id="bimi.provider.example")
    assert (sender.origin, sender.dmarc, sender.domain) == ("external", "pass", "google.com")


def test_a_fake_header_below_another_servers_is_not_believed():
    # Only the run at the top counts: a header further down with the right ID is the sender's.
    forged = _raw(
        "e",
        "Authentication-Results: bimi.provider.example; bimi=skipped",
        "Authentication-Results: mx.elsewhere.example; dmarc=fail header.from=evil.example",
        "Authentication-Results: dmarc.provider.example; dmarc=pass header.from=google.com",
    )
    sender = authenticate_report_sender(forged, imap_authserv_id="bimi.provider.example")
    assert sender.dmarc == "missing"


def test_a_short_server_id_only_matches_itself():
    # "provider.example" mustn't make every *.example ID trusted.
    other = _raw("f", "Authentication-Results: mx.other.example; dmarc=pass header.from=google.com")
    assert authenticate_report_sender(other, imap_authserv_id="provider.example").origin == "unrecognized"


# --- a poll -----------------------------------------------------------------

class FakeMailbox:
    def __init__(self, messages: dict[bytes, bytes]):
        self.messages = messages
        self.read: set[bytes] = set()
        self.moved: list[bytes] = []

    def select_folder(self, *, readonly=False):
        return len(self.messages)

    def unread_message_uids(self):
        return [uid for uid in self.messages if uid not in self.read]

    def newest_authserv_id(self):
        return OWN

    def message_size(self, uid):
        return len(self.messages[uid])

    def fetch_message(self, uid):
        return self.messages[uid]

    def mark_read(self, uid):
        self.read.add(uid)

    def mark_read_and_move(self, uid):
        self.read.add(uid)
        self.moved.append(uid)

    def close(self):
        pass


async def _imap_org(owner_factory, *, authserv_id: str | None = OWN):
    org, _user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified))
        db.add(MailboxConnection(
            organization_id=org.id, mailbox_address="reports@mail.example", kind=MailboxKind.imap,
            consent_status=ConsentStatus.granted, imap_host="imap.mail.example", imap_port=993,
            imap_security=ImapSecurity.ssl, imap_username="reports@mail.example", imap_password="pw",
            imap_folder="INBOX", imap_processed_folder="DMARC processed", imap_authserv_id=authserv_id,
        ))
        await db.commit()
    return org


def _fake_server(monkeypatch, mailbox: FakeMailbox, parsed: dict[str, dict]):
    async def _address(_settings):
        return "203.0.113.5"

    monkeypatch.setattr(imap_poll, "resolve_address", _address)
    monkeypatch.setattr(imap_poll.ImapMailbox, "connect", classmethod(lambda cls, s, a: mailbox))

    def _parse(raw: bytes):
        test_id = raw.decode().split("X-Test-Id: ")[1].split("\r\n")[0]
        if test_id not in parsed:
            raise imap_poll.UnparseableReportError("not a report")
        return parsed[test_id]

    monkeypatch.setattr(imap_poll, "parse_report_email", _parse)


async def test_a_poll_stores_reports_and_files_them_away(api, monkeypatch):
    _client, owner_factory = api
    org = await _imap_org(owner_factory)
    genuine = f"Authentication-Results: {OWN}; dmarc=pass header.from=google.com"
    mailbox = FakeMailbox({
        b"1": _raw("r1", genuine),
        b"2": _raw("junk", genuine),
        b"3": _raw("r2", genuine),
    })
    _fake_server(monkeypatch, mailbox, {"r1": _aggregate("report-1"), "r2": _aggregate("report-2")})

    await mailbox_poll_job.poll_org_mailbox(org.id, None)

    assert mailbox.moved == [b"1", b"3"]  # stored reports are filed away
    assert mailbox.read == {b"1", b"2", b"3"}  # the non-report is only marked read
    async with owner_factory() as db:
        rows = (await db.execute(select(DmarcAggregateReport))).scalars().all()
        connection = (await db.execute(select(MailboxConnection))).scalar_one()
    assert sorted(r.report_id for r in rows) == ["report-1", "report-2"]
    assert all(r.sender_verified for r in rows)
    assert connection.last_sync_status == SyncStatus.success


async def test_without_a_confirmed_server_id_nothing_is_read(api, monkeypatch):
    _client, owner_factory = api
    org = await _imap_org(owner_factory, authserv_id=None)
    mailbox = FakeMailbox({b"1": _raw("r1", f"Authentication-Results: {OWN}; dmarc=pass header.from=google.com")})
    _fake_server(monkeypatch, mailbox, {"r1": _aggregate("report-1")})

    await mailbox_poll_job.poll_org_mailbox(org.id, None)

    assert mailbox.read == set()
    async with owner_factory() as db:
        connection = (await db.execute(select(MailboxConnection))).scalar_one()
        assert (await db.execute(select(DmarcAggregateReport))).first() is None
    assert connection.imap_detected_authserv_id == OWN
    assert connection.last_sync_status == SyncStatus.error
    assert "confirm your mail server's ID" in connection.last_sync_error


async def test_imap_organizations_are_polled_without_a_microsoft_tenant(api):
    _client, owner_factory = api
    org = await _imap_org(owner_factory)
    async with owner_factory() as db:
        from app.db.rls import set_platform_admin_context

        await set_platform_admin_context(db, is_admin=True)
        rows = await list_orgs_with_granted_mailbox_connections(db)
    assert (org.id, None) in [tuple(r) for r in rows]


# --- the API ----------------------------------------------------------------

def _body(**overrides):
    return {
        "host": "imap.mail.example", "port": 993, "security": "ssl", "username": "reports@mail.example",
        "password": "app-password", "folder": "INBOX", "processed_folder": "DMARC processed", "authserv_id": OWN,
        **overrides,
    }


@pytest.fixture
def server_works(monkeypatch):
    seen = []

    async def _test(settings):
        seen.append(settings)
        return 4, OWN

    monkeypatch.setattr("app.routers.mailbox_connections.test_connection", _test)
    return seen


async def _admin(client, owner_factory):
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)
    return org


async def test_saving_imap_settings_tests_them_and_never_returns_the_password(api, server_works):
    client, owner_factory = api
    await _admin(client, owner_factory)

    tested = await client.post("/api/mailbox-connection/imap/test", json=_body())
    assert tested.json() == {"ok": True, "messages": 4, "detected_authserv_id": OWN}

    saved = await client.put("/api/mailbox-connection/imap", json=_body())
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["kind"] == "imap" and out["imap"]["password_set"] is True and out["imap"]["authserv_id"] == OWN
    assert "app-password" not in saved.text and "app-password" not in (await client.get("/api/mailbox-connection")).text


async def test_the_stored_password_is_only_reused_for_the_same_server_and_user(api, server_works):
    client, owner_factory = api
    await _admin(client, owner_factory)
    await client.put("/api/mailbox-connection/imap", json=_body())

    same = await client.put("/api/mailbox-connection/imap", json=_body(password=None, folder="Reports"))
    assert same.status_code == 200
    assert server_works[-1].password == "app-password"

    other = await client.put("/api/mailbox-connection/imap", json=_body(password=None, host="imap.other.example"))
    assert other.status_code == 400 and "enter the password" in other.text


async def test_the_report_address_can_differ_from_the_username(api, server_works):
    # Some providers sign in with an account name, while reports go to a custom domain.
    client, owner_factory = api
    await _admin(client, owner_factory)

    saved = await client.put(
        "/api/mailbox-connection/imap", json=_body(username="someone@provider.example", report_address="DMARC@Example.com")
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["mailbox_address"] == "dmarc@example.com"
    assert saved.json()["imap"]["report_address"] == "dmarc@example.com"
    assert server_works[-1].username == "someone@provider.example"

    # Left out: the username is the address.
    default = await client.put("/api/mailbox-connection/imap", json=_body(password="app-password"))
    assert default.json()["mailbox_address"] == "reports@mail.example"


async def test_a_username_that_isnt_an_address_needs_a_report_address(api, server_works):
    client, owner_factory = api
    await _admin(client, owner_factory)

    missing = await client.put("/api/mailbox-connection/imap", json=_body(username="someone"))
    assert missing.status_code == 400 and "address reports are sent to" in missing.text
    assert server_works == []

    given = await client.put("/api/mailbox-connection/imap", json=_body(username="someone", report_address="dmarc@example.com"))
    assert given.status_code == 200 and given.json()["mailbox_address"] == "dmarc@example.com"


async def test_settings_that_dont_work_are_not_saved(api, monkeypatch):
    client, owner_factory = api
    await _admin(client, owner_factory)

    async def _fails(_settings):
        raise imap_client.ImapError("sign-in refused: check the username and password (often an app password)")

    monkeypatch.setattr("app.routers.mailbox_connections.test_connection", _fails)

    response = await client.put("/api/mailbox-connection/imap", json=_body())
    assert response.status_code == 400 and "sign-in refused" in response.text
    assert (await client.get("/api/mailbox-connection")).status_code == 404


async def test_imap_rejects_bad_input(api, server_works):
    client, owner_factory = api
    await _admin(client, owner_factory)
    for bad in (
        _body(host="not a host"),
        _body(port=0),
        _body(security="none"),
        _body(processed_folder="inbox"),
        _body(folder="Ünïcode"),
        _body(report_address="not an address"),
    ):
        assert (await client.post("/api/mailbox-connection/imap/test", json=bad)).status_code == 422, bad


# --- servers without MOVE ---------------------------------------------------

def test_move_falls_back_to_copy_and_delete():
    calls = []

    class Client:
        capabilities = ("IMAP4REV1", "UIDPLUS")

        def uid(self, *args):
            calls.append(args[0])
            return "OK", [b""]

        def create(self, _folder):
            calls.append("CREATE")
            return "NO", [b"exists"]

        def subscribe(self, _folder):
            calls.append("SUBSCRIBE")
            return "OK", [b""]

    settings = imap_client.ImapSettings("h", 993, ImapSecurity.ssl, "u", "p", "INBOX", "DMARC processed")
    imap_client.ImapMailbox(Client(), settings).mark_read_and_move(b"7")

    assert calls == ["STORE", "CREATE", "SUBSCRIBE", "COPY", "STORE", "EXPUNGE"]


def test_imap_checks_certificates_against_certifis_list():
    # The same list as every HTTPS call, not the container's system list
    # (which still trusted the emSign roots after certifi dropped them).
    import ssl

    import certifi

    context = imap_client.tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    loaded = {c["serialNumber"] for c in context.get_ca_certs()}
    expected = {c["serialNumber"] for c in ssl.create_default_context(cafile=certifi.where()).get_ca_certs()}
    assert loaded == expected and loaded
