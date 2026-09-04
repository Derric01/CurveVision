"""Portable column types.

CurveVision targets PostgreSQL in production but the unit-test suite runs on SQLite so a
contributor needs no services to work on the codebase. These decorators are the only place
that difference is allowed to exist.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import CHAR, JSON, Dialect, String, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID


class GUID(TypeDecorator[uuid.UUID]):
    """UUID as native ``uuid`` on PostgreSQL, ``char(36)`` elsewhere."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PGUUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> uuid.UUID | None:
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class EnumString(TypeDecorator[Any]):
    """A ``StrEnum`` stored as a plain string, and rehydrated as the enum on load.

    Storing enums as strings keeps them readable in ``psql`` and immune to the reordering
    bugs integer enums invite. Without this decorator, though, a value read back from the
    database is an ordinary ``str``, so ``value is SomeEnum.MEMBER`` is silently ``False``
    everywhere -- a whole class of bug that is invisible until behaviour quietly stops
    happening. Rehydrating on load makes identity comparisons work as written.

    A value not in the enum is returned as-is rather than raising: a row written by a
    newer version of the schema must not make an older reader crash.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[StrEnum], length: int = 32) -> None:
        self.enum_class = enum_class
        super().__init__(length)

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return str(value.value) if isinstance(value, StrEnum) else str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        try:
            return self.enum_class(value)
        except ValueError:
            return value


#: JSON document column: ``jsonb`` on PostgreSQL (indexable, compact), ``json`` elsewhere.
JSONDocument = JSON().with_variant(JSONB(), "postgresql")
