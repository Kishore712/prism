"""Alembic environment used programmatically by PrismDatabase."""

from __future__ import annotations

from alembic import context

from prism.database.models import Base


target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("Prism migrations require an application-owned connection")
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
