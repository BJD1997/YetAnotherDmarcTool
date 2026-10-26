from app.models.enums import AuthMethod, UserRole

from tests.conftest import login_as, seed_org_and_user


async def test_list_users(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/users")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["email"] == user.email


async def test_create_local_user_requires_admin(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/users", json={"email": "new@example.com"})

    assert response.status_code == 403


async def test_no_local_teammates_once_the_org_has_a_provider(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=True)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/users", json={"email": "new@example.com"})

    assert response.status_code == 409 and "provider" in response.text


async def test_create_local_user(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=False)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/users", json={"email": "new@example.com", "display_name": "New Person"})

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert "setup_link" in body


async def test_update_user_cannot_self_demote(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)

    response = await client.patch(f"/api/users/{user.id}", json={"role": "member"})

    assert response.status_code == 400


async def test_update_user_not_found_for_other_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    # entra=True on the discarded second user avoids colliding with the
    # partial unique index on lower(email) for auth_method='local' users —
    # seed_org_and_user hardcodes the same email for every call (see Task 4).
    _other_org, other_user = await seed_org_and_user(owner_factory, entra=True)
    await login_as(client, owner_factory, user)

    response = await client.patch(f"/api/users/{other_user.id}", json={"role": "org_admin"})

    assert response.status_code == 404


async def test_the_break_glass_account_cant_be_demoted_or_disabled_by_org_admins(api):
    from app.models.user import User as UserModel

    client, owner_factory = api
    org, admin = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    async with owner_factory() as db:
        keys = UserModel(organization_id=org.id, email="keys@example.com", role=UserRole.org_admin, is_break_glass=True, auth_method="local")
        db.add(keys)
        await db.commit()
        keys_id = keys.id
    await login_as(client, owner_factory, admin)

    assert (await client.patch(f"/api/users/{keys_id}", json={"role": "member"})).status_code == 409
    assert (await client.patch(f"/api/users/{keys_id}", json={"status": "disabled"})).status_code == 409
    # Resetting it is allowed (the user's choice: org admins may help).
    assert (await client.post(f"/api/users/{keys_id}/reset-password")).status_code == 200


async def _add_user(owner_factory, org, email, method, *, status=None, subject=None):
    from app.models.enums import UserStatus
    from app.models.user import User

    async with owner_factory() as db:
        user = User(
            organization_id=org.id, email=email, auth_method=method, role=UserRole.member,
            status=status or UserStatus.active,
            entra_object_id=subject if method == AuthMethod.entra else None,
            external_subject=subject if method not in (AuthMethod.entra, AuthMethod.local) else None,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        await db.commit()
        return user


async def test_only_accounts_nobody_can_sign_in_with_are_removable(api):
    from app.models.enums import UserStatus

    client, owner_factory = api
    # Signs in with Microsoft (the org's provider); break-glass is local.
    org, admin = await seed_org_and_user(owner_factory, entra=True)
    glass = await _add_user(owner_factory, org, "glass@test.example", AuthMethod.local)
    async with owner_factory() as db:
        from app.models.user import User

        (await db.get(User, glass.id)).is_break_glass = True
        await db.commit()
    old_oidc = await _add_user(owner_factory, org, "admin@test.example", AuthMethod.oidc, subject="authentik-sub")
    colleague = await _add_user(owner_factory, org, "colleague@test.example", AuthMethod.entra, subject="oid-2")
    gone = await _add_user(owner_factory, org, "gone@test.example", AuthMethod.entra, subject="oid-3", status=UserStatus.disabled)
    await login_as(client, owner_factory, admin)

    by_id = {u["id"]: u["removable"] for u in (await client.get("/api/users")).json()}
    assert by_id == {
        str(admin.id): False, str(glass.id): False, str(old_oidc.id): True,
        str(colleague.id): False, str(gone.id): False,
    }

    assert (await client.delete(f"/api/users/{admin.id}")).status_code == 400
    assert (await client.delete(f"/api/users/{glass.id}")).status_code == 409
    assert (await client.delete(f"/api/users/{colleague.id}")).status_code == 409
    # Disabled, but still the org's provider: removing would let them back in as a new account.
    assert (await client.delete(f"/api/users/{gone.id}")).status_code == 409
    assert (await client.delete(f"/api/users/{old_oidc.id}")).status_code == 204

    remaining = {u["id"] for u in (await client.get("/api/users")).json()}
    assert str(old_oidc.id) not in remaining and len(remaining) == 4
    events = (await client.get("/api/sign-in-events")).json()["events"]
    removal = next(e for e in events if e["failure_reason"] == "account_removed")
    assert removal["email"] == "admin@test.example" and removal["actor_email"] == admin.email


async def test_a_disabled_local_account_is_removable(api):
    from app.models.enums import UserStatus

    client, owner_factory = api
    org, admin = await seed_org_and_user(owner_factory)
    active = await _add_user(owner_factory, org, "active@test.example", AuthMethod.local)
    disabled = await _add_user(owner_factory, org, "left@test.example", AuthMethod.local, status=UserStatus.disabled)
    await login_as(client, owner_factory, admin)

    assert (await client.delete(f"/api/users/{active.id}")).status_code == 409
    assert (await client.delete(f"/api/users/{disabled.id}")).status_code == 204


async def test_members_cant_remove_accounts(api):
    client, owner_factory = api
    org, member = await seed_org_and_user(owner_factory, role=UserRole.member, entra=True)
    old = await _add_user(owner_factory, org, "old@test.example", AuthMethod.oidc, subject="s")
    await login_as(client, owner_factory, member)

    assert (await client.delete(f"/api/users/{old.id}")).status_code == 403
