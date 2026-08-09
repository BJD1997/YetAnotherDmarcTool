import pytest

from app.services.update_check import is_newer_version


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
        # Unparseable versions (e.g. a locally-built "dev" image) fall back
        # to plain inequality rather than silently hiding a real update.
        ("v0.1.2", "dev", True),
        ("dev", "dev", False),
    ],
)
def test_is_newer_version(latest: str, running: str, expected: bool):
    assert is_newer_version(latest, running) is expected
