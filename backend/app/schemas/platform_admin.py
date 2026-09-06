import uuid

from pydantic import BaseModel, EmailStr, field_validator

from app.models.enums import ConsentStatus, OrganizationStatus, UserRole


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


class AdminVerifyOtpRequest(BaseModel):
    code: str


class AdminEnrollOtpConfirmRequest(BaseModel):
    secret: str
    code: str


class OrganizationCreateRequest(BaseModel):
    name: str
    entra_tenant_id: uuid.UUID | None = None


class OrganizationUpdateRequest(BaseModel):
    name: str | None = None
    entra_tenant_id: uuid.UUID | None = None
    status: OrganizationStatus | None = None


class MailboxConnectionRequest(BaseModel):
    mailbox_address: str
    consent_status: ConsentStatus | None = None


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
