from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from app.config import settings
from app.database import Base
from app import models  # noqa: F401  (registers all tables on Base.metadata)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Override the sqlalchemy.url with our .env-driven setting.
#
# Alembic stores options in a configparser using BasicInterpolation, which
# treats "%" as an interpolation marker. A percent-encoded character in a
# password (`%40` for "@", which Supabase's generated passwords need) is
# therefore read as malformed interpolation and raises before SQLAlchemy ever
# sees the URL — even though that same URL works fine for the app. Doubling
# the percent signs is what BasicInterpolation requires to store a literal
# one; SQLAlchemy then unescapes them back to the real password.
config.set_main_option(
    "sqlalchemy.url", settings.database_url.replace("%", "%%")
)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (live DB connection)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()