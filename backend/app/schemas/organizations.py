import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import ReportSenderCheck, SpfAllQualifierMode
from app.services.ingestion.sender_auth import can_be_trusted


class OrganizationUpdateRequest(BaseModel):
    name: str
    spf_all_qualifier_mode: SpfAllQualifierMode | None = None
    hosted_mailbox_opt_in: bool | None = None
    report_sender_check: ReportSenderCheck | None = None
    rating_window_days: Literal[30, 60, 90, 180] | None = None
    ask_ai_enabled: bool | None = None


class TrustedReporterRequest(BaseModel):
    # The sending domain as the left-out list shows it (sender_domain).
    domain: str = Field(max_length=255)

    @field_validator("domain")
    @classmethod
    def _normalize(cls, value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9-]{2,}", value):
            raise ValueError("not a domain name")
        if not can_be_trusted(value):
            raise ValueError(f"{value} is a free email domain anyone can send from, so its reports can't be trusted")
        return value
