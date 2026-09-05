import re

from pydantic import BaseModel, field_validator

_SELECTOR_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?(\.[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?)*$")


class SelectorCreateRequest(BaseModel):
    selector: str
    description: str | None = None

    @field_validator("selector")
    @classmethod
    def validate_selector(cls, value: str) -> str:
        value = value.strip()
        if not _SELECTOR_RE.match(value):
            raise ValueError("not a valid DKIM selector")
        return value
