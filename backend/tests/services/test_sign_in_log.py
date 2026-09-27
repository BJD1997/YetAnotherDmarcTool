from starlette.requests import Request

from app.services.auth.sign_in_log import client_network_info


def _request(headers: dict[str, str], client: tuple[str, int] = ("198.51.100.23", 51000)) -> Request:
    return Request({
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": client,
    })


def test_uses_the_proxy_resolved_client_address():
    assert client_network_info(_request({"User-Agent": "Firefox"})) == ("198.51.100.23", "Firefox")


def test_ignores_a_client_supplied_cf_connecting_ip():
    # Without Cloudflare in front (Azure, direct access) anyone can send this.
    ip, _ = client_network_info(_request({"CF-Connecting-IP": "8.8.8.8"}))
    assert ip == "198.51.100.23"
