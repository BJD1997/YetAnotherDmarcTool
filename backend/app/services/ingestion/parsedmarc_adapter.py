"""Isolates parsedmarc's exact API surface from the rest of the app — the
pinned version is parsedmarc==8.15.0 (see requirements.txt); re-verify this
file against that package's actual functions if it's ever upgraded.

Deliberately uses parsedmarc as a PARSER ONLY, not via its mailbox
orchestration (parsedmarc.mail.graph.MSGraphConnection /
parsedmarc.get_dmarc_reports_from_mailbox): that path assumes it can create
folders and move/delete messages after processing (needs Mail.ReadWrite),
and has no delta/incremental fetch support of its own. Message fetching is
handled by app/services/graph/mailbox_poller.py (Graph delta query, Mail.Read
only); this module only turns the raw MIME bytes it hands over into a parsed
report dict.
"""

from typing import Literal, TypedDict

import parsedmarc

ReportType = Literal["aggregate", "forensic", "smtp_tls"]

# offline=True skips parsedmarc's optional source-IP reverse-DNS/geolocation
# enrichment — deliberate for now since dmarc_aggregate_records doesn't have
# columns to store that enrichment (source_ip is stored as-is; the dashboard
# can resolve/geolocate on read if that's wanted later) and skipping it avoids
# an extra DNS round-trip per unique source IP during ingestion.
_OFFLINE = True


class ParsedReport(TypedDict):
    report_type: ReportType
    report: dict


class UnparseableReportError(Exception):
    """Wraps any of parsedmarc's parser exceptions (InvalidAggregateReport,
    InvalidForensicReport, InvalidSMTPTLSReport, InvalidDMARCReport,
    ParserError) so callers only need to catch one type."""


def parse_report_email(raw_mime: bytes) -> ParsedReport:
    try:
        result = parsedmarc.parse_report_email(raw_mime, offline=_OFFLINE)
    except parsedmarc.ParserError as exc:
        # Common base for InvalidAggregateReport/InvalidForensicReport/
        # InvalidSMTPTLSReport/InvalidDMARCReport — covers "not a report at
        # all" as well as "malformed report", which is fine: either way the
        # caller just logs it and moves on to the next message.
        raise UnparseableReportError(str(exc)) from exc
    return result  # {"report_type": ..., "report": {...}}
