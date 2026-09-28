"""Integration tests for Alembic schema migrations (REX-004)."""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from rex.persistence.database import create_db_engine


def test_alembic_upgrade_downgrade_cycle(tmp_path: Path):
    """Test full Alembic migration cycle: clean DB -> upgrade -> verify -> downgrade -> re-upgrade."""
    db_file = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    expected_tables = {
        "research_runs",
        "hypotheses",
        "experiments",
        "executions",
        "results",
        "analyses",
        "artifacts",
        "literature_sources",
        "claims",
        "evidence_links",
        "events",
    }

    # 1. Upgrade from clean database to head
    command.upgrade(alembic_cfg, "head")

    engine = create_db_engine(database_url=db_url)
    try:
        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        assert expected_tables.issubset(tables), (
            f"Missing tables after upgrade: {expected_tables - tables}"
        )
        assert "alembic_version" in tables
    finally:
        engine.dispose()

    # 2. Downgrade back to base
    command.downgrade(alembic_cfg, "base")

    engine = create_db_engine(database_url=db_url)
    try:
        inspector = sa.inspect(engine)
        tables_after_downgrade = set(inspector.get_table_names())
        remaining = expected_tables.intersection(tables_after_downgrade)
        assert len(remaining) == 0, f"Tables still present after downgrade: {remaining}"
    finally:
        engine.dispose()

    # 3. Re-upgrade back to head
    command.upgrade(alembic_cfg, "head")

    engine = create_db_engine(database_url=db_url)
    try:
        inspector = sa.inspect(engine)
        tables_reupgraded = set(inspector.get_table_names())
        assert expected_tables.issubset(tables_reupgraded)
    finally:
        engine.dispose()
