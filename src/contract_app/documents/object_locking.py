"""PostgreSQL advisory locks shared by immutable object writes and garbage collection."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import psycopg


@contextmanager
def object_key_locks(
    database_url: str,
    backend: str,
    tenant_id: UUID,
    case_id: UUID,
    digests: set[str],
) -> Iterator[None]:
    """Hold stable per-object session locks until the surrounding operation finishes."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        for digest in sorted(digests):
            lock_key = f"document-object:{backend}:{tenant_id}:{case_id}:{digest}"
            connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (lock_key,))
        yield
