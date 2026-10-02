from typing import Literal

from pydantic import BaseModel

from app.models.enums import ReportSenderCheck, SpfAllQualifierMode


class OrganizationUpdateRequest(BaseModel):
    name: str
    spf_all_qualifier_mode: SpfAllQualifierMode | None = None
    hosted_mailbox_opt_in: bool | None = None
    report_sender_check: ReportSenderCheck | None = None
    rating_window_days: Literal[30, 60, 90, 180] | None = None
