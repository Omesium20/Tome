"""Alembic environment for the **local** database (the user's own machine).

Selected with ``alembic -n local ...``. This is the one a client runs: the
backend container's ENTRYPOINT applies it on start so a self-hoster who pulls
a new version gets the migration without a manual step.

A client upgrade must never require a knowledge migration to land first —
that decoupling is what the Knowledge API's versioning exists for.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config import get_local_settings
from database.local.models import Base

config = context.config

# Take the URL from application config, overriding the placeholder in
# alembic.ini. escape_percent matters: a password containing '%' would
# otherwise be read as ConfigParser interpolation syntax.
config.set_main_option(
    "sqlalchemy.url",
    get_local_settings().local_database_url.replace("%", "%%"),
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Only the client plane's tables. `cards` is not here and must never appear:
# an autogenerate that wanted to create it would mean something imported the
# knowledge models into the client.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it against a database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite can't ALTER most things in place; batch mode rewrites the
        # table instead. Harmless on Postgres, essential for the default setup.
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
