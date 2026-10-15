import re

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import ImapSecurity


class MailboxConnectionSetRequest(BaseModel):
    mailbox_address: str


_HOST_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$")
# Printable ASCII: IMAP folder names outside it need a special encoding.
_FOLDER_RE = re.compile(r"^[ -~]{1,255}$")


class ImapConnectionRequest(BaseModel):
    host: str
    port: int = Field(ge=1, le=65535)
    security: ImapSecurity
    username: str = Field(min_length=1, max_length=320)
    # Left out: keep the stored password (only for the same server and user).
    password: str | None = Field(default=None, min_length=1, max_length=512)
    folder: str = "INBOX"
    processed_folder: str = "DMARC processed"
    # The mail server's Authentication-Results ID (RFC 8601), usually its
    # host name: only its checks count for the report sender check. Empty
    # until known; Test connection detects it.
    authserv_id: str | None = Field(default=None, max_length=255)

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if not _HOST_RE.match(value):
            raise ValueError("not a valid server name")
        return value

    @field_validator("folder", "processed_folder")
    @classmethod
    def _folder(cls, value: str) -> str:
        value = value.strip()
        if not _FOLDER_RE.match(value):
            raise ValueError("use letters, digits, spaces and common punctuation only")
        return value

    @field_validator("authserv_id")
    @classmethod
    def _authserv_id(cls, value: str | None) -> str | None:
        value = (value or "").strip().lower()
        if not value:
            return None
        if not re.fullmatch(r"[a-z0-9._:-]{1,255}", value):
            raise ValueError("an Authentication-Results ID looks like a host name, for example mx.example.com")
        return value

    @model_validator(mode="after")
    def _different_folders(self) -> "ImapConnectionRequest":
        if self.folder.lower() == self.processed_folder.lower():
            raise ValueError("the processed folder must be different from the folder reports arrive in")
        return self
