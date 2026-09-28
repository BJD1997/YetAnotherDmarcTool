from app.config import Settings


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_hosted_address_domain_comes_from_the_mailbox():
    s = _settings(hosted_reports_mailbox_address="reports@davidshostingreports.nl", public_base_url="https://davidshosting.nl")
    assert s.hosted_reports_domain == "davidshostingreports.nl"


def test_explicit_address_domain_still_wins():
    s = _settings(hosted_reports_mailbox_address="reports@example.com", hosted_reports_address_domain="Example.com")
    assert s.hosted_reports_domain == "example.com"


def test_no_mailbox_no_domain():
    assert _settings().hosted_reports_domain is None
