import uuid

import pyotp
import pytest
from cryptography.fernet import Fernet

from app.config import settings
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
