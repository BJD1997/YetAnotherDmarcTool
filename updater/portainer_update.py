"""In-app updates for a Portainer stack: the counterpart of server.py's
Docker Compose path for stacks Portainer manages. Instead of running
`docker compose` itself (which would leave Portainer's stack definition on
the old IMAGE_TAG, so the next redeploy in Portainer rolls the app back),
the updater asks Portainer's API to redeploy its own stack with the new
IMAGE_TAG, pulling the images. Portainer then runs migrate, api, worker
and this updater on the new release.

Only this container holds the Portainer access token, never the api;
server.py has already checked the shared secret and that the version is
a real, newer release. Stdlib only, like server.py.

Settings: PORTAINER_URL (e.g. https://portainer.example:9443),
PORTAINER_API_KEY (a Portainer access token), PORTAINER_STACK_ID (the id
in the stack's Portainer URL), PORTAINER_TLS_VERIFY (default true)."""

import json
import os
import ssl
import urllib.error
import urllib.request

# Portainer (checked with 2.45) answers right away and pulls and redeploys
# in the background; older versions answer when it's done.
TIMEOUT_SECONDS = 600


class PortainerError(Exception):
    pass


def portainer_mode() -> bool:
    return bool(os.environ.get("PORTAINER_URL"))


def config_problem() -> str | None:
    """What's missing for Portainer mode, or None (fine, or not in it)."""
    if not portainer_mode():
        return None
    missing = [name for name in ("PORTAINER_API_KEY", "PORTAINER_STACK_ID") if not os.environ.get(name)]
    if missing:
        return f"in-app updates through Portainer need {' and '.join(missing)} on the updater"
    if not os.environ["PORTAINER_STACK_ID"].isdigit():
        return "PORTAINER_STACK_ID must be the stack's number (the id= in its Portainer URL)"
    return None


def with_image_tag(env: list[dict], version: str) -> list[dict]:
    """The stack's variables with IMAGE_TAG set to `version`, the rest as is."""
    out = [dict(item) for item in env if item.get("name") != "IMAGE_TAG"]
    for index, item in enumerate(env):
        if item.get("name") == "IMAGE_TAG":
            out.insert(index, {"name": "IMAGE_TAG", "value": version})
            return out
    return out + [{"name": "IMAGE_TAG", "value": version}]


class Portainer:
    def __init__(self, url: str, api_key: str, stack_id: str, *, verify_tls: bool = True) -> None:
        self.url = url.rstrip("/")
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self.stack_id = stack_id
        self.context = ssl.create_default_context() if verify_tls else ssl._create_unverified_context()

    @classmethod
    def from_env(cls) -> "Portainer":
        return cls(
            os.environ["PORTAINER_URL"],
            os.environ["PORTAINER_API_KEY"],
            os.environ["PORTAINER_STACK_ID"],
            verify_tls=os.environ.get("PORTAINER_TLS_VERIFY", "true").lower() not in ("false", "0", "no"),
        )

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=self.context) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read()).get("message") or exc.reason
            except (ValueError, AttributeError):
                message = exc.reason
            if exc.code == 404 and path.startswith(f"/api/stacks/{self.stack_id}"):
                message = f"Portainer has no stack {self.stack_id} (check PORTAINER_STACK_ID): {message}"
            elif exc.code in (401, 403):
                message = f"Portainer refused the access token (check PORTAINER_API_KEY): {message}"
            raise PortainerError(f"{method} {path.split('?')[0]} → {exc.code}: {message}") from exc
        except (urllib.error.URLError, OSError) as exc:
            hint = ""
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                hint = (
                    " — Portainer's certificate isn't trusted (out of the box it's self-signed): use Portainer's "
                    "http:// port (9000) on a network the stack shares with it, or set PORTAINER_TLS_VERIFY=false"
                )
            raise PortainerError(f"can't reach Portainer at {self.url}: {exc}{hint}") from exc

    def redeploy(self, version: str) -> None:
        stack = self._call("GET", f"/api/stacks/{self.stack_id}")
        if stack.get("GitConfig"):
            raise PortainerError(
                "this stack is deployed from a Git repository; update it in Portainer (pull and redeploy) instead"
            )
        content = self._call("GET", f"/api/stacks/{self.stack_id}/file").get("StackFileContent", "")
        self._call(
            "PUT",
            f"/api/stacks/{self.stack_id}?endpointId={stack['EndpointId']}",
            {
                "stackFileContent": content,
                "env": with_image_tag(stack.get("Env") or [], version),
                "prune": False,
                "pullImage": True,
            },
        )
