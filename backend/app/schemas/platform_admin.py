import re
import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.models.enums import ConsentStatus, OrganizationStatus, UserRole
from app.schemas.auth import TotpSecret


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


class AdminVerifyOtpRequest(BaseModel):
    code: str


class AdminEnrollOtpConfirmRequest(BaseModel):
    secret: TotpSecret
    code: str


_SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"


class _SignInFields(BaseModel):
    # The direct sign-in link name, /login/<slug>.
    slug: str | None = Field(default=None, pattern=_SLUG_PATTERN)
    entra_tenant_id: uuid.UUID | None = None
    google_workspace_domain: str | None = Field(default=None, max_length=253)
    oidc_issuer: str | None = Field(default=None, max_length=2048)
    oidc_client_id: str | None = Field(default=None, max_length=255)
    # Left out on an update: keep the stored secret.
    oidc_client_secret: str | None = Field(default=None, max_length=1024)
    # With a sign-in provider: the one person who becomes org admin.
    expected_admin_email: EmailStr | None = None

    @field_validator("google_workspace_domain")
    @classmethod
    def _workspace(cls, value: str | None) -> str | None:
        value = (value or "").strip().lower().rstrip(".")
        if not value:
            return None
        if "." not in value or not re.fullmatch(r"[a-z0-9.-]+", value):
            raise ValueError("enter the Workspace's domain, for example example.com")
        return value


class OrganizationCreateRequest(_SignInFields):
    name: str

    @model_validator(mode="after")
    def admin_email_with_a_provider(self) -> "OrganizationCreateRequest":
        if (self.entra_tenant_id or self.google_workspace_domain or self.oidc_issuer) and self.expected_admin_email is None:
            raise ValueError("enter the admin's email address: with a sign-in provider, only they become org admin")
        return self


class OrganizationUpdateRequest(_SignInFields):
    name: str | None = None
    status: OrganizationStatus | None = None
    is_operator: bool | None = None


class MailboxConsentRequest(BaseModel):
    consent_status: ConsentStatus


class LocalUserCreateRequest(BaseModel):
    email: EmailStr
    display_name: str | None = None
    role: UserRole = UserRole.org_admin


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        if len(value) < 12:
            raise ValueError("new password must be at least 12 characters")
        return value


class AdminSessionChoiceRequest(BaseModel):
    choice: Literal["local", "operator_org"] | None
