"""Graph polling against a fake Microsoft 365: temporary failures are
retried, and errors people see never contain the request URL (a delta link
carries the mailbox's sync token)."""

import httpx
import pytest

from app.services.graph import mailbox_poller


@pytest.fixture
def graph(monkeypatch):
    state = {"answers": [], "requests": 0, "sleeps": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"] += 1
        answer = state["answers"].pop(0) if state["answers"] else 200
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        if answer != 200:
            return httpx.Response(answer, json={"error": {"code": "ErrorX", "message": "Something broke"}})
        if request.url.path.endswith("/$value"):
            return httpx.Response(200, content=b"raw-mime")
        return httpx.Response(200, json={"value": [{"id": "m1"}], "@odata.deltaLink": "https://graph/delta?$deltatoken=NEW"})

    real_client = httpx.AsyncClient

    async def _sleep(seconds):
        state["sleeps"].append(seconds)

    async def _token(_tenant):
        return "tok"

    monkeypatch.setattr(mailbox_poller.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(mailbox_poller.asyncio, "sleep", _sleep)
    monkeypatch.setattr(mailbox_poller, "get_graph_token", _token)
    return state


async def _list(delta_link="https://graph/delta?$deltatoken=SECRET-TOKEN"):
    return await mailbox_poller.fetch_new_message_ids(tenant_id="t", mailbox="r@example.com", delta_link=delta_link)


async def test_a_502_blip_is_retried_and_the_poll_succeeds(graph):
    graph["answers"] = [502, 503]

    ids, delta = await _list()

    assert ids == ["m1"] and delta.endswith("NEW")
    assert graph["requests"] == 3
    assert graph["sleeps"] == [2.0, 5.0]


async def test_timeouts_are_retried(graph):
    graph["answers"] = [httpx.ReadTimeout("slow")]

    assert (await _list())[0] == ["m1"]


async def test_an_outage_gives_up_with_a_readable_message_without_the_url(graph):
    graph["answers"] = [502, 502, 502, 502]

    with pytest.raises(mailbox_poller.GraphUnavailableError) as exc:
        await _list()

    message = str(exc.value)
    assert message.startswith("Microsoft 365 didn't respond (HTTP 502), also not after 3 automatic retries.")
    assert "SECRET-TOKEN" not in message and "://" not in message
    assert graph["requests"] == 4


async def test_throttling_waits_for_retry_after_capped_at_a_minute(graph):
    graph["answers"] = [httpx.Response(429, headers={"Retry-After": "600"})]

    await _list()

    assert graph["sleeps"] == [60.0]


async def test_a_refusal_is_not_retried_and_names_the_graph_error(graph):
    graph["answers"] = [403]

    with pytest.raises(mailbox_poller.GraphApiError) as exc:
        await _list()

    assert not isinstance(exc.value, mailbox_poller.GraphUnavailableError)
    assert str(exc.value) == "Microsoft 365 refused the request (HTTP 403: ErrorX: Something broke)"
    assert graph["requests"] == 1


async def test_message_download_retries_then_reports_the_outage(graph):
    graph["answers"] = [504]
    raw = await mailbox_poller.fetch_message_raw_mime(tenant_id="t", mailbox="r@example.com", message_id="m1")
    assert raw == b"raw-mime"

    graph["answers"] = [500, 500, 500, 500]
    with pytest.raises(mailbox_poller.GraphUnavailableError):
        await mailbox_poller.fetch_message_raw_mime(tenant_id="t", mailbox="r@example.com", message_id="m1")
