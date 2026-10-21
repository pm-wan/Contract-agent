"""Create the disposable Gitea CI runtime role before Alembic migrations.

The script refuses non-CI execution and non-disposable database hosts. Production
role provisioning remains outside application migrations.
"""

import os
import time
from urllib.parse import urlparse

import psycopg
from psycopg import sql


def main() -> None:
    if os.environ.get("CI") != "true" or os.environ.get("GITEA_ACTIONS") != "true":
        raise RuntimeError("ci_bootstrap_requires_gitea_ci")
    url = os.environ.get("CI_DATABASE_ADMIN_URL", "")
    password = os.environ.get("CI_DATABASE_RUNTIME_PASSWORD", "")
    if urlparse(url).hostname != "postgres" or not password:
        raise RuntimeError("ci_bootstrap_requires_disposable_postgres_service")

    for attempt in range(20):
        try:
            with psycopg.connect(url) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = 'contract_runtime'"
                ).fetchone()
                if exists is None:
                    connection.execute(
                        sql.SQL("CREATE ROLE contract_runtime LOGIN PASSWORD {}").format(
                            sql.Literal(password)
                        )
                    )
                connection.execute("GRANT CONNECT ON DATABASE contract_audit TO contract_runtime")
                connection.execute("GRANT USAGE ON SCHEMA public TO contract_runtime")
            return
        except psycopg.OperationalError:
            if attempt == 19:
                raise
            time.sleep(1)


if __name__ == "__main__":
    main()
