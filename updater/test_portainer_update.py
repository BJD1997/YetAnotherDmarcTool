"""Portainer mode: the updater redeploys its own stack through Portainer's
API with the new IMAGE_TAG, keeping the stack file and every other
variable as they are."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import portainer_update
from portainer_update import Portainer, PortainerError, config_problem, with_image_tag

STACK = {
    "Id": 7,
    "Name": "yadt",
    "EndpointId": 3,
    "Type": 2,
    "Env": [{"name": "IMAGE_TAG", "value": "v0.2.0-beta5"}, {"name": "FERNET_KEY", "value": "k"}],
}


@pytest.fixture
def fake_portainer():
    seen: list[tuple[str, str, dict | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            seen.append(("GET", self.path, None))
            if self.headers.get("X-API-Key") != "ptr_token":
                return self._reply(401, {"message": "Invalid API key"})
            if self.path == "/api/stacks/7":
                return self._reply(200, STACK)
            if self.path == "/api/stacks/7/file":
                return self._reply(200, {"StackFileContent": "services: {}\n"})
            return self._reply(404, {"message": "Object not found inside the database"})

        def do_PUT(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(("PUT", self.path, body))
            return self._reply(200, {**STACK, "Env": body["env"]})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()


def test_with_image_tag_replaces_or_adds():
    assert with_image_tag(STACK["Env"], "v0.2.0") == [
        {"name": "IMAGE_TAG", "value": "v0.2.0"},
        {"name": "FERNET_KEY", "value": "k"},
    ]
    assert with_image_tag([{"name": "A", "value": "1"}], "v0.2.0") == [
        {"name": "A", "value": "1"},
        {"name": "IMAGE_TAG", "value": "v0.2.0"},
    ]


def test_redeploys_the_stack_with_the_new_tag(fake_portainer):
    url, seen = fake_portainer
    Portainer(url, "ptr_token", "7").redeploy("v0.2.0")

    method, path, body = seen[-1]
    assert (method, path) == ("PUT", "/api/stacks/7?endpointId=3")
    assert body == {
        "stackFileContent": "services: {}\n",
        "env": [{"name": "IMAGE_TAG", "value": "v0.2.0"}, {"name": "FERNET_KEY", "value": "k"}],
        "prune": False,
        "pullImage": True,
    }


def test_errors_say_what_portainer_said(fake_portainer):
    url, _seen = fake_portainer
    with pytest.raises(PortainerError, match="Invalid API key"):
        Portainer(url, "wrong", "7").redeploy("v0.2.0")
    with pytest.raises(PortainerError, match="stack 8"):
        Portainer(url, "ptr_token", "8").redeploy("v0.2.0")


def test_config_problem(monkeypatch):
    for name in ("PORTAINER_URL", "PORTAINER_API_KEY", "PORTAINER_STACK_ID"):
        monkeypatch.delenv(name, raising=False)
    assert config_problem() is None  # not in Portainer mode
    monkeypatch.setenv("PORTAINER_URL", "https://portainer.example:9443")
    assert "PORTAINER_API_KEY" in config_problem() and "PORTAINER_STACK_ID" in config_problem()
    monkeypatch.setenv("PORTAINER_API_KEY", "ptr_x")
    monkeypatch.setenv("PORTAINER_STACK_ID", "seven")
    assert "number" in config_problem()
    monkeypatch.setenv("PORTAINER_STACK_ID", "7")
    assert config_problem() is None
