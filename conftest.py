# Shared pytest setup: gives tests a real, migrated, EMPTY Postgres database.
# Uses a separate database (risk_test) so tests never touch development data.
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

# Locally: same Postgres container, different database.
# In CI: GitHub Actions sets TEST_DATABASE_URL itself.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://risk:risk@localhost:5432/risk_test"
)


def _create_database_if_missing(url: str) -> None:
    """CREATE DATABASE can't run inside a transaction, so connect to the
    built-in 'postgres' database in AUTOCOMMIT mode to create risk_test."""
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": target.database},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")  # runs ONCE for the whole test run
def engine() -> Iterator[Engine]:
    _create_database_if_missing(TEST_DATABASE_URL)

    # Run the real migrations, so tests use the exact production schema.
    config = Config(str(Path(__file__).parent / "alembic.ini"))
    config.attributes["database_url"] = TEST_DATABASE_URL  # read by env.py (7a)
    command.upgrade(config, "head")

    eng = create_engine(TEST_DATABASE_URL)
    yield eng  # tests run here
    eng.dispose()  # cleanup after all tests finish


@pytest.fixture
def db(engine: Engine) -> Engine:
    """Per-test: wipe the tables BEFORE each test, so every test starts
    empty. (Wiping before rather than after leaves a failed test's data
    in place, so you can inspect it.)"""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE prices, tickers"))
    return engine
