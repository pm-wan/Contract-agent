"""Alembic configuration for explicit SQL migrations."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)


def run_migrations_online() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for migrations")
    sqlalchemy_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(sqlalchemy_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_online()
