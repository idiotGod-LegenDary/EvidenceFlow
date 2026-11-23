from pathlib import Path

from sqlalchemy import create_engine, inspect

from alembic import command
from alembic.config import Config
from app.repositories.models import CaseEventRow, CaseRow


def test_case_migration_can_upgrade_downgrade_and_reapply(tmp_path):
    config = Config()
    config.set_main_option("script_location", str(Path("alembic").resolve()))
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        for model in (CaseRow, CaseEventRow):
            assert {c["name"] for c in inspector.get_columns(model.__tablename__)} == set(
                model.__table__.columns.keys()
            )
        indexes = inspector.get_indexes("service_cases")
        assert any(index["column_names"] == ["action_id"] and index["unique"] for index in indexes)
        command.downgrade(config, "0002")
        assert "service_cases" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        assert "case_events" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
