from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class PlatformAdmin(UUIDPkMixin, TimestampMixin, Base):
    """Local-auth account for the operator staff who provision organizations.

    Deliberately separate from per-org Entra SSO users: an Organization must
    exist before its users can SSO in, so *something* needs a bootstrap-safe
    login to create the first Organization. No row-level security applies to
    this table — it isn't tenant-scoped data.
    """

    __tablename__ = "platform_admins"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
