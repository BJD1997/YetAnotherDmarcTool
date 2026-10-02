from pydantic import BaseModel, field_validator


class LocalLoginRequest(BaseModel):
    email: str
    password: str


class VerifyOtpRequest(BaseModel):
    code: str


class SetPasswordRequest(BaseModel):
    token: str
    new_password: str


class EnrollOtpConfirmRequest(BaseModel):
    secret: str
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
    secret: str
    code: str
