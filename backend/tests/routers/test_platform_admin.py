import uuid

import pyotp
import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.models.organization import Organization
from app.services.crypto import secrets as crypto_secrets
from tests.conftest import login_as_platform_admin, seed_platform_admin_with_totp


@pytest.fixture(autouse=True)
def _fernet_key_for_totp_encryption(monkeypatch):
    """PlatformAdmin.otp_secret is transparently encrypted at rest (same
    EncryptedSecret column type as User.otp_secret — see
    app/services/auth/totp_secret.py) and fails closed without a FERNET_KEY —
    same requirement production has. Every test below either seeds an
    already-enrolled admin or drives the enroll-otp/confirm flow for real,
    both of which write that column; same monkeypatch pattern as
    tests/services/test_totp_secret.py's `fernet_key` fixture and
    test_auth.py's identically-named autouse fixture, just autouse here
    since nearly every test in this module exercises that path."""
    monkeypatch.setattr(settings, "fernet_key", Fernet.generate_key().decode())
    crypto_secrets._fernet.cache_clear()
    yield
    crypto_secrets._fernet.cache_clear()


async def test_admin_login_requires_credentials(api):
    client, _owner_factory = api
    response = await client.post("/api/admin/login", json={"email": "nobody@platform.example", "password": "wrong"})
    assert response.status_code == 401


async def test_admin_login_and_verify_otp_full_flow(api):
    client, owner_factory = api
    admin, secret = await seed_platform_admin_with_totp(owner_factory)

    login_response = await client.post(
        "/api/admin/login", json={"email": admin.email, "password": "correct horse battery staple"}
    )
    assert login_response.status_code == 200
    assert login_response.json() == {"needs_enrollment": False}

    verify_response = await client.post("/api/admin/verify-otp", json={"code": pyotp.TOTP(secret).now()})
    assert verify_response.status_code == 204

    me_response = await client.get("/api/admin/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == admin.email
    assert me_response.json()["auth_type"] == "local"


async def test_admin_verify_otp_rejects_wrong_code(api):
    client, owner_factory = api
    admin, _secret = await seed_platform_admin_with_totp(owner_factory)
    await client.post("/api/admin/login", json={"email": admin.email, "password": "correct horse battery staple"})

    response = await client.post("/api/admin/verify-otp", json={"code": "000000"})

    assert response.status_code == 401


async def test_admin_enroll_otp_flow(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)  # this helper's admin has NO TOTP enrolled yet — wrong tool here

    # login_as_platform_admin bypasses the MFA-pending step entirely (mints a
    # real session directly), so it can't be used to test enroll-otp, which
    # needs a pending-MFA cookie. Re-seed a fresh not-yet-enrolled admin and
    # drive it through /login for real instead.
    from app.services.auth.password import hash_password
    from app.models.platform_admin import PlatformAdmin

    async with owner_factory() as db:
        # Unique per run, not a fixed literal — platform_admins carries no FK
        # to organizations (see the model's own docstring), so the `api`
        # fixture's `TRUNCATE organizations CASCADE` never clears this table;
        # a fixed email collides on a UniqueViolationError the next time
        # these tests run against the same persistent container. Every other
        # PlatformAdmin the test suite creates (login_as_platform_admin,
        # seed_platform_admin_with_totp) already avoids this the same way.
        admin = PlatformAdmin(
            email=f"fresh-admin+{uuid.uuid4()}@platform.example",
            password_hash=hash_password("correct horse battery staple"),
            is_active=True,
        )
        db.add(admin)
        await db.flush()
        await db.refresh(admin)
        await db.commit()

    login_response = await client.post(
        "/api/admin/login", json={"email": admin.email, "password": "correct horse battery staple"}
    )
    assert login_response.json() == {"needs_enrollment": True}

    enroll_response = await client.post("/api/admin/enroll-otp")
    assert enroll_response.status_code == 200
    secret = enroll_response.json()["secret"]

    confirm_response = await client.post(
        "/api/admin/enroll-otp/confirm", json={"secret": secret, "code": pyotp.TOTP(secret).now()}
    )
    assert confirm_response.status_code == 200
    assert len(confirm_response.json()["recovery_codes"]) == 10

    me_response = await client.get("/api/admin/me")
    assert me_response.status_code == 200


async def test_admin_logout(api):
    client, owner_factory = api
    admin, secret = await seed_platform_admin_with_totp(owner_factory)
    await client.post("/api/admin/login", json={"email": admin.email, "password": "correct horse battery staple"})
    await client.post("/api/admin/verify-otp", json={"code": pyotp.TOTP(secret).now()})

    logout_response = await client.post("/api/admin/logout")
    assert logout_response.status_code == 204

    me_response = await client.get("/api/admin/me")
    assert me_response.status_code == 401


async def test_change_password_requires_local_session(api):
    client, owner_factory = api
    admin, secret = await seed_platform_admin_with_totp(owner_factory)
    await client.post("/api/admin/login", json={"email": admin.email, "password": "correct horse battery staple"})
    await client.post("/api/admin/verify-otp", json={"code": pyotp.TOTP(secret).now()})

    response = await client.post(
        "/api/admin/change-password",
        json={"current_password": "correct horse battery staple", "new_password": "a brand new password entirely"},
    )

    assert response.status_code == 204


async def test_list_organizations_requires_admin(api):
    client, _owner_factory = api
    response = await client.get("/api/admin/organizations")
    assert response.status_code == 401


async def test_create_and_get_organization(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    create_response = await client.post("/api/admin/organizations", json={"name": "Acme Corp"})
    assert create_response.status_code == 201
    org_id = create_response.json()["id"]
    assert create_response.json()["domain_count"] == 0

    get_response = await client.get(f"/api/admin/organizations/{org_id}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Acme Corp"


async def test_get_organization_not_found(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    response = await client.get("/api/admin/organizations/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


async def test_update_organization(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org_id = (await client.post("/api/admin/organizations", json={"name": "Old Name"})).json()["id"]

    response = await client.patch(f"/api/admin/organizations/{org_id}", json={"name": "New Name"})

    assert response.status_code == 200
    assert response.json()["name"] == "New Name"


async def test_delete_organization(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org_id = (await client.post("/api/admin/organizations", json={"name": "Deletable Org"})).json()["id"]

    response = await client.delete(f"/api/admin/organizations/{org_id}")

    assert response.status_code == 204
    assert (await client.get(f"/api/admin/organizations/{org_id}")).status_code == 404


async def test_delete_operator_organization_blocked(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org_id = (await client.post("/api/admin/organizations", json={"name": "Operator Org"})).json()["id"]

    async with owner_factory() as db:
        org = await db.get(Organization, uuid.UUID(org_id))
        org.is_operator = True
        await db.commit()

    response = await client.delete(f"/api/admin/organizations/{org_id}")

    assert response.status_code == 400


async def test_create_local_user_for_org(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org_id = (await client.post("/api/admin/organizations", json={"name": "Local Auth Org"})).json()["id"]

    response = await client.post(
        f"/api/admin/organizations/{org_id}/users", json={"email": "first-user@example.com"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "first-user@example.com"
    assert "setup_link" in body


async def test_create_local_user_rejects_entra_org(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    entra_tenant_id = str(uuid.uuid4())
    org_id = (
        await client.post("/api/admin/organizations", json={"name": "Entra Org", "entra_tenant_id": entra_tenant_id})
    ).json()["id"]

    response = await client.post(f"/api/admin/organizations/{org_id}/users", json={"email": "someone@example.com"})

    assert response.status_code == 409


async def test_upsert_mailbox_connection(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org_id = (await client.post("/api/admin/organizations", json={"name": "Mailbox Org"})).json()["id"]

    response = await client.post(
        f"/api/admin/organizations/{org_id}/mailbox-connection", json={"mailbox_address": "reports@example.com"}
    )

    assert response.status_code == 201
    assert response.json()["mailbox_address"] == "reports@example.com"

    update_response = await client.post(
        f"/api/admin/organizations/{org_id}/mailbox-connection",
        json={"mailbox_address": "reports@example.com", "consent_status": "granted"},
    )
    assert update_response.status_code == 201
    assert update_response.json()["consent_status"] == "granted"


async def test_list_organizations_includes_aggregates(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    await client.post("/api/admin/organizations", json={"name": "Org One"})
    await client.post("/api/admin/organizations", json={"name": "Org Two"})

    response = await client.get("/api/admin/organizations")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    for org in body:
        assert org["domain_count"] == 0
        assert org["job_error_count_7d"] == 0
        assert org["last_report_at"] is None


async def test_list_organizations_aggregates_reflect_real_data(api):
    """test_list_organizations_includes_aggregates only exercises the
    all-zero fallback path (fresh orgs, nothing related seeded yet). This
    covers the actual GROUP BY/.in_(org_ids) batching in org_aggregates,
    the 7-day job-error cutoff, and the last_report_at.isoformat() branch —
    real non-zero data in, real non-zero rollup values out."""
    from datetime import datetime, timezone

    from app.models.dmarc_aggregate import DmarcAggregateReport
    from app.models.domain import Domain
    from app.models.enums import JobStatus, JobType
    from app.models.job_run import JobRun

    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org_id = (await client.post("/api/admin/organizations", json={"name": "Org With Data"})).json()["id"]
    other_org_id = (await client.post("/api/admin/organizations", json={"name": "Org Without Data"})).json()["id"]

    async with owner_factory() as db:
        domain = Domain(organization_id=uuid.UUID(org_id), name="example.com")
        db.add(domain)
        await db.flush()

        db.add(
            JobRun(
                job_type=JobType.mailbox_poll,
                organization_id=uuid.UUID(org_id),
                status=JobStatus.failure,
                started_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            DmarcAggregateReport(
                organization_id=uuid.UUID(org_id),
                domain_id=domain.id,
                report_id="report-1",
                org_name="reporter.example",
                date_range_begin=datetime.now(timezone.utc),
                date_range_end=datetime.now(timezone.utc),
                policy_published_domain="example.com",
                received_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    response = await client.get("/api/admin/organizations")

    assert response.status_code == 200
    body = response.json()
    org = next(o for o in body if o["id"] == org_id)
    assert org["domain_count"] == 1
    assert org["job_error_count_7d"] == 1
    assert org["last_report_at"] is not None

    other_org = next(o for o in body if o["id"] == other_org_id)
    assert other_org["domain_count"] == 0
    assert other_org["job_error_count_7d"] == 0
    assert other_org["last_report_at"] is None


async def test_job_runs_empty(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    response = await client.get("/api/admin/job-runs")

    assert response.status_code == 200
    assert response.json() == []


async def test_job_runs_summary_empty(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    response = await client.get("/api/admin/job-runs/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["last_failure"] is None
    assert body["success_rate_pct_24h"] is None
    assert body["reports_processed_today"] == 0


async def test_entra_consent_callback_success():
    """Unauthenticated, no DB fixture needed — this endpoint does no DB
    writes at all (see its own docstring)."""
    import httpx
    from httpx import ASGITransport

    from app.main import app

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/admin/entra/consent-callback")

    assert response.status_code == 200
    assert "Mail access granted" in response.text


async def test_entra_consent_callback_error():
    import httpx
    from httpx import ASGITransport

    from app.main import app

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/admin/entra/consent-callback", params={"error": "access_denied", "error_description": "User declined"}
        )

    assert response.status_code == 200
    assert "wasn't granted" in response.text
    assert "access_denied" in response.text
