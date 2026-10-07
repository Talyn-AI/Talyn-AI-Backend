"""Row level security stays on and stays complete.

Two invariants. First, the migration's table list must equal the models'
table list: a table added to the models but not to the migration deploys
unrestricted, which is exactly the exposure the migration closed. Second,
the mechanism itself must work on real Postgres — ENABLE must actually flip
relrowsecurity, not just parse.
"""
import importlib.util

from sqlalchemy import text


def _migration_tables():
    path = "alembic/versions/d4c9a1e27f50_row_level_security.py"
    spec = importlib.util.spec_from_file_location("rls_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TABLES


def test_migration_covers_every_model_table():
    """A new model table without a migration entry fails here, not in prod."""
    from app.database import Base
    import app.models  # noqa: F401  ensure every model is registered

    assert sorted(_migration_tables()) == sorted(Base.metadata.tables.keys())


def test_enable_flip_is_real_on_postgres(db_session):
    """ENABLE ROW LEVEL SECURITY must take effect, on this database."""
    from app.database import Base
    import app.models  # noqa: F401

    tables = sorted(Base.metadata.tables.keys())
    try:
        for table in tables:
            db_session.execute(
                text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            )
        db_session.commit()

        flags = db_session.execute(
            text(
                "SELECT relname FROM pg_class "
                "WHERE relname = ANY(:names) AND relrowsecurity IS FALSE"
            ),
            {"names": tables},
        ).all()
        assert flags == [], f"RLS not enabled on: {[r[0] for r in flags]}"
    finally:
        # The suite builds its schema with create_all, which leaves RLS off.
        # Restore that so no other test can observe this one.
        for table in tables:
            db_session.execute(
                text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            )
        db_session.commit()
