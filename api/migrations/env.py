# Alembic runs this file for every migration command
# (upgrade, downgrade, etc.). Its job: connect to the database
# and run the migration scripts in versions/.
from logging.config import fileConfig

from alembic import context

from api.app.db import get_engine

# The config object built from alembic.ini.
config = context.config

# Set up logging using the [loggers] sections of alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate compares ORM models to the database to write migrations for
# you. Our migrations are hand-written SQL, so there is nothing to compare.
target_metadata = None


def run_migrations_online() -> None:
    """Connect to the database and run the pending migrations."""
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        # Each migration runs inside a transaction: if any statement fails,
        # the whole migration rolls back instead of leaving a half-built schema.
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()