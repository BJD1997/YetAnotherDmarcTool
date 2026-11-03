"""Switching sign-in provider keeps one account per person: the first
sign-in through the new provider takes over the old provider's account with
the same email address, under the conditions in
sign_in_flow._account_from_previous_provider."""

import uuid

from sqlalchemy import select

from app.models.domain import Domain
from app.models.enums import AuthMethod, DomainVerificationStatus, UserRole, UserStatus
from app.models.user import User
from app.services.auth.sign_in_flow import complete_sign_in

from tests.conftest import seed_org_and_user


async def _seed(owner_factory, *, verified: bool = True):
    org, admin = await seed_org_and_user(owner_factory, entra=True)
    async with owner_factory() as db:
        db.add(
            Domain(
                organization_id=org.id, name="test.example",
                verification_status=DomainVerificationStatus.verified if verified else DomainVerificationStatus.pending,
            )
        )
        user = await db.get(User, admin.id)
        user.entra_object_id = "entra-oid-1"
        await db.commit()
    return org, admin


async def _sign_in(owner_factory, org, method, subject, email="Admin@Test.example"):
    async with owner_factory() as db:
        outcome = await complete_sign_in(
            db, org=org, method=method, subject=subject, email=email, display_name="Admin",
            ip_address="127.0.0.1", user_agent="pytest",
        )
    async with owner_factory() as db:
        users = (await db.execute(select(User).where(User.organization_id == org.id))).scalars().all()
    return outcome, users


async def test_switching_provider_and_back_keeps_one_account(api):
    _client, owner_factory = api
    org, admin = await _seed(owner_factory)

    outcome, users = await _sign_in(owner_factory, org, AuthMethod.oidc, "authentik-sub-1")
    assert outcome.error is None
    assert [(u.id, u.auth_method, u.external_subject, u.entra_object_id, u.role) for u in users] == [
        (admin.id, AuthMethod.oidc, "authentik-sub-1", None, UserRole.org_admin)
    ]

    outcome, users = await _sign_in(owner_factory, org, AuthMethod.entra, "entra-oid-1")
    assert outcome.error is None
    assert [(u.id, u.auth_method, u.external_subject, u.entra_object_id) for u in users] == [
        (admin.id, AuthMethod.entra, None, "entra-oid-1")
    ]


async def test_no_takeover_for_an_address_off_the_verified_domains(api):
    _client, owner_factory = api
    org, admin = await _seed(owner_factory, verified=False)

    _outcome, users = await _sign_in(owner_factory, org, AuthMethod.oidc, "authentik-sub-1")

    assert len(users) == 2
    assert next(u for u in users if u.id == admin.id).auth_method == AuthMethod.entra


async def test_no_takeover_when_two_accounts_have_the_address(api):
    _client, owner_factory = api
    org, _admin = await _seed(owner_factory)
    async with owner_factory() as db:
        db.add(User(organization_id=org.id, email="admin@test.example", auth_method=AuthMethod.google,
                    external_subject="google-sub", role=UserRole.member, status=UserStatus.active))
        await db.commit()

    _outcome, users = await _sign_in(owner_factory, org, AuthMethod.oidc, "authentik-sub-1")

    assert len(users) == 3


async def test_a_local_account_is_never_taken_over(api):
    _client, owner_factory = api
    org, _admin = await seed_org_and_user(owner_factory)  # local admin@test.example
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="test.example", verification_status=DomainVerificationStatus.verified))
        await db.commit()

    _outcome, users = await _sign_in(owner_factory, org, AuthMethod.oidc, "authentik-sub-1")

    assert sorted(u.auth_method.value for u in users) == ["local", "oidc"]


async def test_a_disabled_account_stays_disabled(api):
    _client, owner_factory = api
    org, admin = await _seed(owner_factory)
    async with owner_factory() as db:
        (await db.get(User, admin.id)).status = UserStatus.disabled
        await db.commit()

    outcome, users = await _sign_in(owner_factory, org, AuthMethod.oidc, f"authentik-{uuid.uuid4()}")

    assert outcome.error == "user_disabled"
    assert len(users) == 1
