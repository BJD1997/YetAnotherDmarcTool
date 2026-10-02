"""Run with: python3 -m pytest updater/test_server.py"""

import json
import subprocess

import pytest

import server
from server import check_requested_version, set_env_var


@pytest.mark.parametrize(
    ("requested", "running"),
    [
        ("v0.1.6", "v0.1.5"),
        ("v0.1.5", "v0.1.5-rc3"),
        ("v0.1.5-rc1", "v0.1.5-beta9"),
        ("v0.1.5-beta10", "v0.1.5-beta9"),
    ],
)
def test_allows_a_strictly_newer_release(requested, running):
    assert check_requested_version(requested, running) is None


@pytest.mark.parametrize(
    ("requested", "running"),
    [
        ("latest", "v0.1.5"),  # not a release tag
        ("v0.1.5-beta9-policyfix", "v0.1.5"),  # not a release tag
        ("v0.1.5;rm -rf /", "v0.1.5"),  # not a release tag
        ("v0.1.4", "v0.1.5"),  # downgrade to an older release
        ("v0.1.5", "v0.1.5"),  # same version
        ("v0.1.5-beta9", "v0.1.5-rc1"),  # beta ranks below rc
        ("v0.1.6", "dev"),  # running a local build
        ("v0.1.6", None),  # couldn't reach the app
    ],
)
def test_refuses_anything_else(requested, running):
    assert check_requested_version(requested, running) is not None


def test_set_env_var_replaces_existing_line_and_keeps_the_rest(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nPOSTGRES_PASSWORD=secret\nIMAGE_TAG=v0.1.4\nOTHER=1")
    inode = env.stat().st_ino

    set_env_var(str(env), "IMAGE_TAG", "v0.1.5")

    assert env.read_text() == "# comment\nPOSTGRES_PASSWORD=secret\nIMAGE_TAG=v0.1.5\nOTHER=1"
    assert env.stat().st_ino == inode  # written in place — a bind-mounted file must keep its inode


def test_set_env_var_appends_when_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=1")

    set_env_var(str(env), "IMAGE_TAG", "v0.1.5")

    assert env.read_text() == "OTHER=1\nIMAGE_TAG=v0.1.5\n"


def test_run_update_pulls_migrates_restarts_records_tag_then_self_updates(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("IMAGE_TAG=v0.1.4\n")
    monkeypatch.setattr(server, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(server, "_compose_project_name", lambda: "proj")
    monkeypatch.setattr(server, "_project_host_dir", lambda: "/srv/dmarc")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs.get("env", {"IMAGE_TAG": "v0.1.5"})["IMAGE_TAG"] == "v0.1.5"
        stdout = json.dumps({"services": {"updater": {"image": "ghcr.io/x/updater:v0.1.5"}}})
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout)

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    server._run_update("v0.1.5")

    compose_steps = [cmd[cmd.index(f"{tmp_path}/docker-compose.yml") + 1:] for cmd in calls[:4]]
    assert compose_steps == [
        ["pull", "api", "worker", "migrate", "updater"],
        ["run", "--rm", "--no-deps", "migrate"],
        ["up", "-d", "--no-deps", "api", "worker"],
        ["config", "--format", "json"],
    ]
    assert (tmp_path / ".env").read_text() == "IMAGE_TAG=v0.1.5\n"

    helper = calls[4]
    assert helper[:4] == ["docker", "run", "-d", "--rm"]
    assert "/srv/dmarc:/srv/dmarc:ro" in helper  # checkout at its real host path
    assert "ghcr.io/x/updater:v0.1.5" in helper  # the NEW image does the recreating
    assert helper[-1].endswith(
        "--project-directory /srv/dmarc --env-file /srv/dmarc/.env -f /srv/dmarc/docker-compose.yml up -d --no-deps updater"
    )


def test_failed_update_leaves_tag_and_updater_alone(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("IMAGE_TAG=v0.1.4\n")
    monkeypatch.setattr(server, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(server, "_compose_project_name", lambda: "proj")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "migrate" in cmd and "run" in cmd:
            raise server.subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    server._run_update("v0.1.5")

    assert len(calls) == 2  # pull, then the failing migrate — nothing after
    assert (tmp_path / ".env").read_text() == "IMAGE_TAG=v0.1.4\n"
