"""report_destination: the organization's own mailbox wins over a domain's
hosted address; either one in rua= counts as reaching this app."""

from app.models.domain import Domain
from app.models.mailbox_connection import MailboxConnection
from app.services.report_destination import ReportDestination, report_destination


def _domain(hosted: str | None) -> Domain:
    return Domain(name="example.com", hosted_report_address=hosted)


def test_own_mailbox_wins_over_hosted_address():
    dest = report_destination(_domain("reports+abc@hosted.example"), MailboxConnection(mailbox_address="dmarc@example.com"))
    assert dest is not None
    assert dest.address == "dmarc@example.com"
    assert dest.accepted == ("dmarc@example.com", "reports+abc@hosted.example")


def test_hosted_address_without_own_mailbox():
    dest = report_destination(_domain("reports+abc@hosted.example"), None)
    assert dest == ReportDestination.single("reports+abc@hosted.example")


def test_own_mailbox_without_hosted_address():
    dest = report_destination(_domain(None), MailboxConnection(mailbox_address="dmarc@example.com"))
    assert dest == ReportDestination.single("dmarc@example.com")


def test_nothing_set_up():
    assert report_destination(_domain(None), None) is None


def test_reached_by_either_accepted_address_case_insensitive():
    dest = ReportDestination(address="dmarc@example.com", accepted=("dmarc@example.com", "reports+abc@hosted.example"))
    assert dest.reached_by(["Reports+ABC@hosted.example"])
    assert dest.reached_by(["other@vendor.example", "DMARC@example.com"])
    assert not dest.reached_by(["other@vendor.example"])
