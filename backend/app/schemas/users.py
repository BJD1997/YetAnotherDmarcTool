from pydantic import BaseModel, EmailStr

from app.models.enums import UserRole, UserStatus


class UserUpdateRequest(BaseModel):
    role: UserRole | None = None
    status: UserStatus | None = None


class LocalUserCreateRequest(BaseModel):
    email: EmailStr
    display_name: str | None = None
    role: UserRole = UserRole.member
