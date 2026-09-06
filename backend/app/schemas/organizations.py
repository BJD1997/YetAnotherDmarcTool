from pydantic import BaseModel

from app.models.enums import SpfAllQualifierMode


class OrganizationUpdateRequest(BaseModel):
    name: str
    spf_all_qualifier_mode: SpfAllQualifierMode | None = None
    hosted_mailbox_opt_in: bool | None = None
