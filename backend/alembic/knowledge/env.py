"""Alembic environment for the **knowledge** database (the shared cloud corpus).

Selected with ``alembic -n knowledge ...``. The URL comes from
``config.KnowledgeSettings`` rather than alembic.ini, so there is exactly one
place to configure it — and because the setting is required with no default,
pointing this at the wrong database takes a deliberate act.

Run by maintainers only. A user's client must never reach this environment;
see `docs/knowledge-pipeline.md`.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config import get_knowledge_settings
from database.knowledge.models import Base

config = context.config

# Take the URL from application config, overriding the placeholder in
# alembic.ini. escape_percent matters: a password containing '%' would
# otherwise be read as ConfigParser interpolation syntax.
config.set_main_option(
    "sqlalchemy.url",
    get_knowledge_settings().knowledge_database_url.replace("%", "%%"),
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Only the knowledge plane's tables. The local plane has its own base and its
# own environment, so an autogenerate here can neither create nor drop a
# user's collection.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it against a database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Postgres is the deployment target, but a development corpus may be
        # SQLite, which can't ALTER most things in place. Harmless on Postgres.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
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


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
