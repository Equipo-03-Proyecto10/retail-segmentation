"""Alembic environment for the retail segmentation platform.

Two things here are load-bearing and should not be simplified away.

**The naming convention.** Without it, SQLAlchemy emits anonymous constraint
names and a later migration cannot drop a constraint by name — you end up
querying the catalog to find out what PostgreSQL happened to call it. The
convention is fixed in section 2 of ``docs/data/postgresql-model.md`` and the
frozen DDL already follows it, so autogenerate must use the same one or it will
propose renaming every constraint in the schema.

**The connection string.** Migrations connect as the schema owner through
``DATABASE_MIGRATION_URL``. The application connects as a restricted role
through ``DATABASE_URL`` and must not be able to UPDATE or DELETE ``audit_log``.
A REVOKE has no effect against the role that owns the schema, so if these two
collapse into one DSN the append-only audit trail is decoration (D-14, R-18).
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import MetaData, engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# docs/data/postgresql-model.md section 2
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "ux": "ux_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}

# No ORM models exist yet: S0-03 builds the application and its declarative base,
# and will point this at that base's metadata. Until then autogenerate has
# nothing to compare against, which is correct rather than broken -- the M1
# schema is authored as DDL and reviewed as DDL.
target_metadata = MetaData(naming_convention=NAMING_CONVENTION)

ENV_VAR = "DATABASE_MIGRATION_URL"


def _load_dotenv() -> None:
    """Read a repository-root .env if present.

    Deliberately hand-rolled: python-dotenv is a dependency the migration
    tooling does not otherwise need, and the parsing required is trivial.
    Real environment variables always win over the file.
    """
    for candidate in (
        Path(__file__).resolve().parents[2] / ".env",
        Path.cwd() / ".env",
    ):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        return


def get_url() -> str:
    _load_dotenv()
    url = os.environ.get(ENV_VAR) or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            f"{ENV_VAR} is not set.\n\n"
            "Migrations run as the schema owner and the application runs as a\n"
            "restricted role (D-14). Set the owner DSN, for example:\n\n"
            f"  export {ENV_VAR}=postgresql+psycopg://postgres:postgres@localhost:5432/retail\n\n"
            "See .env.example."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
