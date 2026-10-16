import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, field_validator, model_validator

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


class OrganizationCreateRequest(BaseModel):
    name: str
    entra_tenant_id: uuid.UUID | None = None
    # Required with Microsoft sign-in: the one person who becomes org admin.
    expected_admin_email: EmailStr | None = None

    @model_validator(mode="after")
    def admin_email_with_microsoft(self) -> "OrganizationCreateRequest":
        if self.entra_tenant_id is not None and self.expected_admin_email is None:
            raise ValueError("enter the admin's email address: with Microsoft sign-in, only they become org admin")
        return self


class OrganizationUpdateRequest(BaseModel):
    name: str | None = None
    entra_tenant_id: uuid.UUID | None = None
    status: OrganizationStatus | None = None
    is_operator: bool | None = None
    expected_admin_email: EmailStr | None = None


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
