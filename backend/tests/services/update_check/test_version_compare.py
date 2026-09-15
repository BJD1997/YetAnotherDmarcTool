import pytest

from app.services.update_check import is_dev_build, is_newer_version


@pytest.mark.parametrize(
    "latest,running,expected",
    [
        ("v0.1.2", "v0.1.1", True),
        ("v0.1.1", "v0.1.2", False),
        ("v0.1.2", "v0.1.2", False),
        # A stable release outranks a prerelease of the same version.
        ("v0.1.2", "v0.1.2-rc1", True),
        ("v0.1.2-rc1", "v0.1.2", False),
        # Higher rc number of the same version is newer.
        ("v0.1.2-rc2", "v0.1.2-rc1", True),
        ("v0.1.2-rc1", "v0.1.2-rc2", False),
        # The exact bug this guards against: switching prereleases back off
        # while running a newer rc shouldn't look like an available update
        # to an older stable release.
        ("v0.1.1", "v0.1.2-rc2", False),
        # A locally-built "dev" (unparseable) running version is off the
        # release channel — never treated as having an update available, so
        # the updater can't silently downgrade it to an older tagged release.
        ("v0.1.2", "dev", False),
        ("v0.1.3-rc2", "dev", False),
        ("dev", "dev", False),
        # An unparseable *latest* likewise gives nothing safe to compare.
        ("dev", "v0.1.2", False),
        # The exact bug this guards against: -betaN was never recognized by
        # the version regex at all (only -rcN was), so any beta tag failed
        # to parse and every comparison involving one silently fell through
        # to False — no beta release ever looked like an update to anything,
        # including a stable release running a full minor version behind.
        ("v0.1.5-beta4", "v0.1.4", True),
        # A stable release outranks a prerelease of the same version,
        # beta included.
        ("v0.1.5", "v0.1.5-beta1", True),
        ("v0.1.5-beta1", "v0.1.5", False),
        # Higher beta number of the same version is newer.
        ("v0.1.5-beta2", "v0.1.5-beta1", True),
        ("v0.1.5-beta1", "v0.1.5-beta2", False),
        # -rcN outranks -betaN of the same version regardless of number —
        # beta and rc are different channels, not one counter.
        ("v0.1.5-rc1", "v0.1.5-beta4", True),
        ("v0.1.5-beta4", "v0.1.5-rc1", False),
    ],
)
def test_is_newer_version(latest: str, running: str, expected: bool):
    assert is_newer_version(latest, running) is expected


@pytest.mark.parametrize(
    "version,expected",
    [
        ("v0.1.4", False),
        ("v0.1.4-rc1", False),
        # The exact bug this guards against: a real tagged beta release
        # running in production was previously indistinguishable from an
        # actual locally-built dev image, both here and in is_newer_version
        # (same underlying _parse_version) — the admin console showed a
        # tagged beta release as "off the release channel, managed
        # manually" and POST /admin/updates/trigger refused it outright.
        ("v0.1.5-beta4", False),
        # A genuinely unparseable running version (a locally-built "dev"
        # image, or anything else that isn't a recognized release tag)
        # still is a dev build — that part of the behavior is unchanged.
        ("dev", True),
        ("", True),
    ],
)
def test_is_dev_build(version: str, expected: bool):
    assert is_dev_build(version) is expected
