"""The Gmail client against a fake Google: the service account's signed
token request, listing, fetching and filing messages."""

import base64
import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings
from app.services.gmail import client as gmail

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
ACCOUNT_JSON = json.dumps({
    "type": "service_account", "client_email": "yadt@project.iam.gserviceaccount.com", "client_id": "1234567890",
    "private_key": PEM, "token_uri": "https://oauth2.googleapis.com/token",
})
RAW = b"Authentication-Results: mx.google.com; dmarc=pass header.from=google.com\r\nSubject: Report\r\n\r\nbody"


@pytest.fixture
def google(monkeypatch):
    state = {"modify": [], "labels": [{"id": "INBOX", "name": "INBOX"}], "grant": "ok", "tokens": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            claims = jwt.decode(form["assertion"], KEY.public_key(), algorithms=["RS256"], audience="https://oauth2.googleapis.com/token")
            assert claims["sub"] == "dmarc@acme.example" and claims["scope"] == gmail.SCOPE
            state["tokens"] += 1
            if state["grant"] != "ok":
                return httpx.Response(401, json={"error": state["grant"]})
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        path = request.url.path.split("/users/dmarc@acme.example/")[1]
        if path == "labels/INBOX":
            return httpx.Response(200, json={"messagesTotal": 7})
        if path == "messages" and request.method == "GET":
            assert request.url.params["q"] == "is:unread in:inbox"
            return httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
        if path.startswith("messages/") and path.endswith("/modify"):
            state["modify"].append((path.split("/")[1], json.loads(request.content)))
            return httpx.Response(200, json={})
        if path.startswith("messages/"):
            if request.url.params["format"] == "minimal":
                return httpx.Response(200, json={"sizeEstimate": 1234})
            return httpx.Response(200, json={"raw": base64.urlsafe_b64encode(RAW).decode().rstrip("=")})
        if path == "labels" and request.method == "GET":
            return httpx.Response(200, json={"labels": state["labels"]})
        if path == "labels" and request.method == "POST":
            state["labels"].append({"id": "Label_9", "name": json.loads(request.content)["name"]})
            return httpx.Response(200, json={"id": "Label_9"})
        return httpx.Response(404)

    gmail.clear_tokens()
    monkeypatch.setattr(gmail, "client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(settings, "google_mail_service_account_json", ACCOUNT_JSON)
    monkeypatch.setattr(settings, "google_mail_service_account_file", None)
    return state


def _mailbox():
    return gmail.GmailMailbox(gmail.load_service_account(), "dmarc@acme.example")


async def test_reads_unread_messages_and_files_them_under_a_label(google):
    mailbox = _mailbox()
    assert await mailbox.inbox_count() == 7
    assert await mailbox.unread_ids() == ["m1", "m2"]
    assert await mailbox.size("m1") == 1234
    assert await mailbox.fetch_raw("m1") == RAW

    await mailbox.mark_read_and_move("m1")
    await mailbox.mark_read_and_move("m2")
    await mailbox.mark_read("m3")
    assert google["modify"] == [
        ("m1", {"addLabelIds": ["Label_9"], "removeLabelIds": ["UNREAD", "INBOX"]}),
        ("m2", {"addLabelIds": ["Label_9"], "removeLabelIds": ["UNREAD", "INBOX"]}),
        ("m3", {"removeLabelIds": ["UNREAD"]}),
    ]
    assert [l["name"] for l in google["labels"]].count("DMARC processed") == 1  # created once
    assert google["tokens"] == 1  # the token is reused


async def test_missing_delegation_says_what_to_grant(google):
    google["grant"] = "unauthorized_client"
    with pytest.raises(gmail.GmailError, match="client ID 1234567890 with scope https://www.googleapis.com/auth/gmail.modify"):
        await _mailbox().inbox_count()


def test_a_bad_key_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "google_mail_service_account_file", None)
    monkeypatch.setattr(settings, "google_mail_service_account_json", '{"type": "authorized_user"}')
    with pytest.raises(gmail.GmailError, match="isn't a valid JSON key"):
        gmail.load_service_account()
    monkeypatch.setattr(settings, "google_mail_service_account_json", None)
    assert gmail.load_service_account() is None
