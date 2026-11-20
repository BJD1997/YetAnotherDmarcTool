"""Where a domain's DMARC and TLS-RPT reports go, decided in one place.

The organization's own mailbox (Microsoft 365, Google Workspace or IMAP)
wins when it has one; otherwise the domain's hosted address. Both are
processed, so a rua= with either one counts as reaching this app."""

from dataclasses import dataclass

from app.models.domain import Domain
from app.models.mailbox_connection import MailboxConnection


@dataclass(frozen=True)
class ReportDestination:
    address: str  # what the builders put in rua= and the checks name
    accepted: tuple[str, ...]  # any of these in rua= reaches this app

    @classmethod
    def single(cls, address: str) -> "ReportDestination":
        return cls(address=address, accepted=(address,))

    def reached_by(self, targets: list[str]) -> bool:
        wanted = {a.lower() for a in self.accepted}
        return any(t.lower() in wanted for t in targets)


def report_destination(domain: Domain, connection: MailboxConnection | None) -> ReportDestination | None:
    own = connection.mailbox_address if connection is not None else None
    hosted = domain.hosted_report_address
    address = own or hosted
    if address is None:
        return None
    return ReportDestination(address=address, accepted=tuple(a for a in (own, hosted) if a))
