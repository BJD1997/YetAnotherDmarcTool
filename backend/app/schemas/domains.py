import re
import uuid

from pydantic import BaseModel, field_validator

from app.models.enums import DomainMailProfile

_DOMAIN_NAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)


class DomainCreateRequest(BaseModel):
    name: str
    parent_domain_id: uuid.UUID | None = None
    notes: str | None = None
    mail_profile: DomainMailProfile | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if not _DOMAIN_NAME_RE.match(value):
            raise ValueError("not a valid domain name")
        return value


class DomainUpdateRequest(BaseModel):
    notes: str | None = None
    is_active: bool | None = None
    mail_profile: DomainMailProfile | None = None
