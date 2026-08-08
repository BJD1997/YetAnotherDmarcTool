import enum

from sqlalchemy import Enum


def pg_enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    """SQLAlchemy's Enum type binds parameters using the Python enum
    member's *name* by default, not its *value* — a well-known gotcha that
    only stays invisible when every member's name happens to equal its
    value. It doesn't here: e.g. AuthResult.pass_ (name="pass_", trailing
    underscore to dodge the `pass` keyword) has value="pass", and Postgres's
    enum type only knows the value strings (they're what the initial
    migration's CREATE TYPE statements used). Without values_callable,
    inserting AuthResult.pass_ sends the literal string "pass_" and Postgres
    rejects it as not a valid `auth_result` label.

    Use this instead of raw sqlalchemy.Enum(...) for every enum column."""
    return Enum(enum_cls, name=name, native_enum=True, values_callable=lambda x: [e.value for e in x])
