from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base. Models (Phase 1+) import this to register themselves
    with Base.metadata, which Alembic autogenerate reads from in alembic/env.py."""
