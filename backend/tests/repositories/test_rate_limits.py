from app.repositories.rate_limits import count_recent_hits, prune_hits_older_than, record_hit


async def test_record_hit_and_count_recent_hits(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        await record_hit(db, "test-bucket-1")
        await record_hit(db, "test-bucket-1")
        await db.commit()
        count = await count_recent_hits(db, "test-bucket-1", window_seconds=60)
    assert count == 2


async def test_count_recent_hits_excludes_old_hits(api):
    from sqlalchemy import text

    _client, owner_factory = api
    async with owner_factory() as db:
        await record_hit(db, "test-bucket-2")
        await db.commit()
        await db.execute(
            text("UPDATE rate_limit_hits SET hit_at = now() - make_interval(secs => 120) WHERE bucket = 'test-bucket-2'")
        )
        await db.commit()
        count = await count_recent_hits(db, "test-bucket-2", window_seconds=60)
    assert count == 0


async def test_prune_hits_older_than_removes_old_rows(api):
    from sqlalchemy import text

    _client, owner_factory = api
    async with owner_factory() as db:
        await record_hit(db, "test-bucket-3")
        await db.commit()
        await db.execute(
            text("UPDATE rate_limit_hits SET hit_at = now() - make_interval(secs => 7200) WHERE bucket = 'test-bucket-3'")
        )
        await db.commit()
        await prune_hits_older_than(db, max_age_seconds=3600)
        await db.commit()
        count = await count_recent_hits(db, "test-bucket-3", window_seconds=999999)
    assert count == 0
