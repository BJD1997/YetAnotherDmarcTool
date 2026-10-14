from typing import Annotated

from pydantic import BaseModel, StringConstraints, field_validator

# Exactly what totp.generate_secret() hands out (32 base32 characters, 160
# bits): enrollment sends it back with the first code, and a shorter or
# home-made secret would make that account's 2FA weaker.
TotpSecret = Annotated[str, StringConstraints(pattern=r"^[A-Z2-7]{32}$")]


class LocalLoginRequest(BaseModel):
    email: str
    password: str


class VerifyOtpRequest(BaseModel):
    code: str


class SetPasswordRequest(BaseModel):
    token: str
    new_password: str


class EnrollOtpConfirmRequest(BaseModel):
    secret: TotpSecret
    code: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        if len(value) < 12:
            raise ValueError("password must be at least 12 characters")
        return value


class MfaResetStartRequest(BaseModel):
    current_password: str


class MfaResetConfirmRequest(BaseModel):
    current_password: str
    secret: TotpSecret
    code: str
