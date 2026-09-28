"""Alembic Environment Configuration for REX (REX-004)."""

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

import rex.persistence.models  # noqa: F401 - ensure all models are registered on Base.metadata
from rex.config import get_settings
from rex.persistence.database import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model MetaData object for 'autogenerate' support
target_metadata = Base.metadata


def get_url() -> str:
    """Retrieve database URL from config or REX application settings."""
    url = config.get_main_option("sqlalchemy.url")
    if not url or url.startswith("driver://"):
        try:
            return get_settings().persistence.database_url
        except Exception:  # noqa: BLE001
            return "sqlite:///data/database/rex.db"
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # Required for SQLite schema alterations
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Allow tests or external callers to inject an existing connection
    connectable = config.attributes.get("connection", None)

    if connectable is None:
        url = get_url()
        # Ensure parent directory exists for file-based SQLite databases
        if url.startswith("sqlite:///") and ":memory:" not in url:
            db_path_str = url.replace("sqlite:///", "")
            if "?" in db_path_str:
                db_path_str = db_path_str.split("?")[0]
            db_path = Path(db_path_str)
            db_path.parent.mkdir(parents=True, exist_ok=True)

        configuration = config.get_section(config.config_ini_section, {})
        configuration["sqlalchemy.url"] = url
        connectable = engine_from_config(
            configuration,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(
            connection=connectable,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
