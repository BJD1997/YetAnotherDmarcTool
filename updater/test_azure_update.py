"""Run with: python3 -m pytest updater/test_azure_update.py"""

import pytest

import azure_update
from azure_update import UpdateError, retag_template, update

ENV = {
    "TARGET_VERSION": "v0.1.5-rc2",
    "APP_HEALTH_URL": "https://yadt-api.example/api/health",
    "UPDATE_REQUEST_URL": "https://yadt-api.example/api/update-request",
    "IMAGE_REPOS": "ghcr.io/bjd1997/yetanotherdmarctool,ghcr.io/bjd1997/yetanotherdmarctool-resolver,ghcr.io/bjd1997/yetanotherdmarctool-updater",
    "API_APP": "yadt-api", "WORKER_APP": "yadt-worker", "MIGRATE_JOB": "yadt-migrate", "UPDATER_JOB": "yadt-updater",
    "SUBSCRIPTION_ID": "sub", "RESOURCE_GROUP": "rg", "AZURE_CLIENT_ID": "client",
}


class FakeArm:
    def __init__(self):
        self.calls = []

    def retag(self, path, repos, version):
        self.calls.append(("retag", path, version))

    def run_job(self, job):
        self.calls.append(("run", job))


def _running(monkeypatch, version):
    monkeypatch.setattr(azure_update, "running_version", lambda _url: version)


def test_runs_migrations_first_then_worker_api_and_itself(monkeypatch):
    _running(monkeypatch, "v0.1.5-rc1")
    arm = FakeArm()

    update(ENV, arm)

    assert arm.calls == [
        ("retag", "jobs/yadt-migrate", "v0.1.5-rc2"),
        ("run", "yadt-migrate"),
        ("retag", "containerApps/yadt-worker", "v0.1.5-rc2"),
        ("retag", "containerApps/yadt-api", "v0.1.5-rc2"),
        ("retag", "jobs/yadt-updater", "v0.1.5-rc2"),
    ]


@pytest.mark.parametrize(
    ("target", "running"),
    [("v0.1.5-rc1", "v0.1.5-rc1"), ("v0.1.4", "v0.1.5-rc1"), ("latest", "v0.1.5-rc1"), ("v0.1.5-rc2", None), ("", "v0.1.5-rc1")],
)
def test_refuses_anything_but_a_newer_release(monkeypatch, target, running):
    _running(monkeypatch, running)
    arm = FakeArm()

    with pytest.raises(UpdateError, match="refusing update"):
        update({**ENV, "TARGET_VERSION": target}, arm)
    assert arm.calls == []


def test_migration_failure_stops_before_the_app_changes(monkeypatch):
    _running(monkeypatch, "v0.1.5-rc1")

    class FailingMigration(FakeArm):
        def run_job(self, job):
            super().run_job(job)
            raise UpdateError("yadt-migrate execution failed")

    arm = FailingMigration()
    with pytest.raises(UpdateError):
        update(ENV, arm)
    assert [c[1] for c in arm.calls] == ["jobs/yadt-migrate", "yadt-migrate"]


def test_retag_moves_only_our_images_and_drops_app_version():
    template = {
        "revisionSuffix": "v1",
        "containers": [
            {"name": "api", "image": "ghcr.io/bjd1997/yetanotherdmarctool:v0.1.5-rc1",
             "env": [{"name": "APP_VERSION", "value": "v0.1.5-rc1"}, {"name": "FERNET_KEY", "secretRef": "fernet-key"}]},
            {"name": "resolver", "image": "ghcr.io/bjd1997/yetanotherdmarctool-resolver:v0.1.5-rc1"},
            {"name": "other", "image": "mcr.microsoft.com/something:1.2"},
        ]
    }

    result = retag_template(template, ENV["IMAGE_REPOS"].split(","), "v0.1.5-rc2")

    assert [c["image"] for c in result["containers"]] == [
        "ghcr.io/bjd1997/yetanotherdmarctool:v0.1.5-rc2",
        "ghcr.io/bjd1997/yetanotherdmarctool-resolver:v0.1.5-rc2",
        "mcr.microsoft.com/something:1.2",
    ]
    assert result["containers"][0]["env"] == [{"name": "FERNET_KEY", "secretRef": "fernet-key"}]
    assert "revisionSuffix" not in result


def test_arm_patches_the_template_and_waits(monkeypatch):
    requests = []
    states = iter(["Updating", "Succeeded"])

    def fake_http(method, url, *, headers=None, body=None):
        requests.append((method, url.split("?")[0].rsplit("/Microsoft.App/", 1)[1], body))
        if method == "GET" and len(requests) == 1:
            return 200, {"properties": {"template": {"containers": [{"name": "w", "image": "ghcr.io/bjd1997/yetanotherdmarctool:v1"}]}}}
        if method == "GET":
            return 200, {"properties": {"provisioningState": next(states)}}
        return 200, {}

    monkeypatch.setattr(azure_update, "_http", fake_http)
    arm = azure_update.Arm("token", "sub", "rg", sleep=lambda _s: None)

    arm.retag("containerApps/yadt-worker", ["ghcr.io/bjd1997/yetanotherdmarctool"], "v0.1.5-rc2")

    patch = [r for r in requests if r[0] == "PATCH"][0]
    assert patch[1] == "containerApps/yadt-worker"
    assert patch[2]["properties"]["template"]["containers"][0]["image"].endswith(":v0.1.5-rc2")
    assert [r[0] for r in requests] == ["GET", "PATCH", "GET", "GET"]


def test_run_job_waits_for_success_and_reports_failure(monkeypatch):
    statuses = iter(["Running", "Failed"])

    def fake_http(method, url, *, headers=None, body=None):
        if method == "POST":
            return 202, {"name": "yadt-migrate-abc"}
        return 200, {"properties": {"status": next(statuses)}}

    monkeypatch.setattr(azure_update, "_http", fake_http)
    arm = azure_update.Arm("token", "sub", "rg", sleep=lambda _s: None)

    with pytest.raises(UpdateError, match="yadt-migrate-abc failed"):
        arm.run_job("yadt-migrate")


def test_reads_the_requested_version_from_the_app(monkeypatch):
    _running(monkeypatch, "v0.1.5-rc1")
    monkeypatch.setattr(azure_update, "requested_version", lambda url: "v0.1.5-rc2" if url.endswith("/api/update-request") else "")
    arm = FakeArm()

    update({k: v for k, v in ENV.items() if k != "TARGET_VERSION"}, arm)

    assert arm.calls[0] == ("retag", "jobs/yadt-migrate", "v0.1.5-rc2")


def test_no_request_means_no_update(monkeypatch):
    _running(monkeypatch, "v0.1.5-rc1")
    monkeypatch.setattr(azure_update, "requested_version", lambda _url: "")
    arm = FakeArm()

    with pytest.raises(UpdateError, match="refusing update"):
        update({k: v for k, v in ENV.items() if k != "TARGET_VERSION"}, arm)
    assert arm.calls == []
