"""One rule for the admin database login used by the schema tests: the same one tools.migrate uses
(MIGRATE_DATABASE_URL, else ERP_ADMIN_URL, else the host/port/database of DATABASE_URL with user scm)."""
from tools.db import _url


def admin_dsn() -> str:
    return _url("migrate").replace("postgresql+psycopg2://", "postgresql://", 1)
