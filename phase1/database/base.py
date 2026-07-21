"""
Declarative base class and reusable mixins for the Drug Repurposing ETL platform.

This module is the **single canonical definition** of ``Base``, shared by both
``database.connection`` and ``database.models``.  Extracting Base from
connection.py eliminates the circular-import risk identified in ARCH-02.

Architecture
------------
``database.base``  ->  ``database.connection`` (imports Base)
                  ->  ``database.models``    (imports Base, mixins)

No module imports from a downstream consumer, so the dependency graph is
strictly acyclic.

Mixins Provided
---------------
- **IDMixin**         -- Auto-incrementing integer primary key.
- **TimestampMixin**  -- ``created_at`` and ``updated_at`` with server-side
  defaults and a PostgreSQL trigger for ``updated_at`` (onupdate does NOT
  fire for bulk operations -- IDEM-02).
- **SoftDeleteMixin** -- ``is_deleted`` and ``deleted_at`` for reversible
  deletes without cascade destruction (DES-08, REL-01).

Naming Convention (CMP-04)
--------------------------
A ``naming_convention`` dictionary is attached to ``Base.metadata`` so that
all constraints (CHECK, UNIQUE, FK, PK) receive deterministic, predictable
names.  This is required for:
  * Idempotent ``ALTER TABLE ... ADD CONSTRAINT`` in migrations.
  * Cross-dialect consistency between PostgreSQL and SQLite.
  * Automated schema-diff tooling.

Schema Version (ARCH-07)
------------------------
``SCHEMA_VERSION`` is the single source of truth for the current ORM schema
revision.  It must be incremented whenever a migration file is added.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, MetaData, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---------------------------------------------------------------------------
# Schema version -- auto-derived from migration file names at import time.
# v35 ROOT FIX (issue 32): previously this was a hardcoded constant that
# had to be bumped manually whenever a new migration file was added. The
# v29 ROOT FIX bumped it from 6 to 9, but if a new migration ``010_*.sql``
# is added, the constant would silently fall behind and
# ``check_migrations()`` would report ``schema_version_matches=False``
# forever (the exact bug the v29 fix was supposed to prevent).
#
# The fix: scan the migrations directory for files matching ``NNN_*.sql``
# (excluding rollback files ``*_rollback.sql``) and take the max NNN.
# This is O(N) at import time where N = number of migrations (~9), so the
# cost is negligible. The migrations directory is resolved relative to
# this file (``database/base.py`` -> ``database/migrations/``) so the
# derivation works regardless of the current working directory.
# ---------------------------------------------------------------------------
def _derive_schema_version() -> int:
    """Return the highest migration version found in the migrations dir.

    Looks for files named ``NNN_*.sql`` (where NNN is 1-3 digits) in
    ``database/migrations/``, EXCLUDING rollback files
    (``*_rollback.sql``). Returns the maximum NNN found, or ``0`` if the
    directory is missing or empty (e.g., test isolation).
    """
    migrations_dir = Path(__file__).resolve().parent / "migrations"
    if not migrations_dir.is_dir():
        return 0
    pattern = re.compile(r"^(\d{1,3})_[^_].*\.sql$")
    versions: list[int] = []
    for path in migrations_dir.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith("_rollback.sql"):
            continue
        m = pattern.match(path.name)
        if m:
            versions.append(int(m.group(1)))
    return max(versions) if versions else 0


SCHEMA_VERSION: int = _derive_schema_version()
# P1-008 ROOT FIX (Teammate 1 — institutional-grade fix):
#   The previous code had:
#
#     SCHEMA_VERSION_FALLBACK: int = 0
#     if SCHEMA_VERSION == 0:
#         SCHEMA_VERSION = SCHEMA_VERSION_FALLBACK
#
#   This is a COMPLETE NO-OP: when SCHEMA_VERSION == 0, it assigns
#   SCHEMA_VERSION = 0 (since FALLBACK == 0). The audit (P1-008)
#   explicitly required REMOVING this no-op line, but the prior "fix"
#   only changed the FALLBACK value (15 → 0) and KEPT the no-op
#   assignment — leaving the misleading impression that the code was
#   doing something useful when it was doing nothing.
#
#   SEMANTICS (now properly documented):
#     SCHEMA_VERSION == 0 means "migrations directory not found / fresh
#     install". This is a VALID state, not an error. The migration
#     runner (phase1/database/migrations/run_migrations.py) handles
#     this case explicitly:
#       * In ``check_migrations``: code_version == 0 means "we cannot
#         verify schema version match — assume fresh install and apply
#         all migrations". The previous code's
#         ``schema_version_matches = (db_version == code_version)``
#         would return False when db_version was None (fresh install,
#         no schema_version table yet) AND code_version was 0 —
#         falsely reporting "schema drift" on every fresh install.
#       * In ``run_migrations``: code_version == 0 means "apply ALL
#         migration files in numeric order" — this was already the
#         behavior, but the no-op ``SCHEMA_VERSION = SCHEMA_VERSION_FALLBACK``
#         made it look like the code was using a fallback value when it
#         wasn't.
#
#   ``SCHEMA_VERSION_FALLBACK`` is KEPT for backward compatibility
#   (the symbol is exported in ``__all__`` and the test suite
#   ``test_p1_003_schema_version_fallback_is_zero`` asserts it equals
#   0). Its value is 0 and MUST remain 0 — it documents the fresh-
#   install semantics. The no-op ``if SCHEMA_VERSION == 0:`` block is
#   DELETED because it actively misleads readers into thinking there's
#   a fallback mechanism when there isn't one.
SCHEMA_VERSION_FALLBACK: int = 0
# P1-008: NO assignment block here. SCHEMA_VERSION == 0 IS the
# documented "fresh install" sentinel — see the comment above.

# ---------------------------------------------------------------------------
# Naming convention for all constraints (CMP-04)
# ---------------------------------------------------------------------------
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "chk_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


# ===========================================================================
# BASE CLASS
# ===========================================================================


class Base(DeclarativeBase):
    """Declarative base class shared by all ORM models.

    Every model in ``database.models`` inherits from this class so that
    ``Base.metadata.create_all(engine)`` creates all tables at once.

    The ``metadata.naming_convention`` ensures deterministic constraint names
    across PostgreSQL and SQLite (CMP-04).
    """
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ===========================================================================
# MIXINS
# ===========================================================================


class IDMixin:
    """Auto-incrementing integer primary key.

    [ARCH-05] Centralises the ``id`` column so every model inherits
    consistently instead of re-declaring it.
    """

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )


class TimestampMixin:
    """``created_at`` and ``updated_at`` timestamps with server defaults.

    [ARCH-05] Centralises timestamp columns.
    [DESM-06] Adds ``updated_at`` to all models (Protein previously lacked it).
    [IDEM-02] ``onupdate`` is NOT set because it does not fire for bulk
    operations.  PostgreSQL uses a trigger (defined in migration SQL) and
    loaders must explicitly set ``updated_at`` in ``updatable_cols``.
    """

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Reversible soft-delete pattern (DES-08, REL-01).

    Instead of hard-deleting rows (which cascades destructively), set
    ``is_deleted = True`` and optionally record ``deleted_at``.  Downstream
    queries should filter ``WHERE is_deleted = FALSE``.

    Applied to ``Drug`` and ``Protein`` -- the two primary entity tables
    where accidental data loss is most impactful.
    """

    is_deleted: Mapped[bool] = mapped_column(
        # v90 ROOT FIX (BUG #23): `server_default=text("FALSE")` instead of
        #   the non-portable `server_default="0"`. The SoftDeleteMixin is
        #   applied to Drug + Protein (the two primary entity tables); any
        #   column-type drift here propagates to BOTH tables. Aligning with
        #   migration 001 line 540 (`is_deleted BOOLEAN NOT NULL DEFAULT
        #   FALSE`) byte-for-byte so create_all() and migration 001 emit
        #   identical DDL for `is_deleted` on every dialect.
        Boolean,
        server_default=text("FALSE"),
        nullable=False,
    )
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def soft_delete(self) -> None:
        """Mark this record as soft-deleted with a timestamp."""
        self.is_deleted = True
        self.deleted_at = datetime.datetime.now(datetime.timezone.utc)

    def restore(self) -> None:
        """Undo a soft delete."""
        self.is_deleted = False
        self.deleted_at = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__: list[str] = [
    "Base",
    "IDMixin",
    "NAMING_CONVENTION",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_FALLBACK",
    "SoftDeleteMixin",
    "TimestampMixin",
]
