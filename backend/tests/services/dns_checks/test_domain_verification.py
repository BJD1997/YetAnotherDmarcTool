"""The ownership TXT record: the current name, and the name from before the
rebrand for domains that published it then and aren't verified yet."""

import pytest

from app.services.dns_checks import domain_verification


@pytest.fixture
def txt(monkeypatch):
    records: dict[str, list[str]] = {}

    async def _resolve(name):
        return records.get(name, [])

    monkeypatch.setattr(domain_verification, "resolve_txt", _resolve)
    return records


def test_new_domains_are_told_the_new_name():
    assert domain_verification.verification_record_name("example.com") == "_yadt-verify.example.com"


async def test_the_new_name_verifies(txt):
    txt["_yadt-verify.example.com"] = ["token-1"]
    assert await domain_verification.verify_domain_ownership("example.com", "token-1")


async def test_the_old_name_still_verifies(txt):
    txt["_dmarc-dashboard-verify.example.com"] = ["token-1"]
    assert await domain_verification.verify_domain_ownership("example.com", "token-1")


async def test_a_wrong_token_doesnt(txt):
    txt["_yadt-verify.example.com"] = ["someone-else"]
    assert not await domain_verification.verify_domain_ownership("example.com", "token-1")
