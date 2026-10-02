"""Leaves reports whose email failed sender authentication (sender_verified
is False — see app/services/ingestion/sender_auth.py) out of every ORM
SELECT, so a forged report never reaches a pass rate, sender inventory,
rating or report list, without each of those queries having to remember a
filter. They stay in the database: a later genuine copy replaces them (see
report_writer), and they can be inspected directly.

NULL (not checked: ingested before this existed, or no verdict available)
still counts, same as before.

Opt out per statement with .execution_options(include_unverified_reports=True)
when a query genuinely needs to see them."""

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.dmarc_forensic import DmarcForensicReport
from app.models.tls_rpt import TlsRptReport

INCLUDE_UNVERIFIED = "include_unverified_reports"
_FILTERED = (DmarcAggregateReport, DmarcAggregateRecord, DmarcForensicReport, TlsRptReport)


@event.listens_for(Session, "do_orm_execute")
def _hide_unverified_reports(state: ORMExecuteState) -> None:
    if not state.is_select or state.execution_options.get(INCLUDE_UNVERIFIED):
        return
    state.statement = state.statement.options(
        *(
            with_loader_criteria(model, lambda cls: cls.sender_verified.is_not(False), include_aliases=True)
            for model in _FILTERED
        )
    )
