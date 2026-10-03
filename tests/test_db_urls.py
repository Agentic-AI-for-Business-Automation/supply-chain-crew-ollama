"""Every database role must follow DATABASE_URL unless explicitly overridden (a remapped port must not send roles elsewhere)."""
import pytest
from tools import db

ENV_NAMES = ("DATABASE_URL", "AUDIT_DATABASE_URL", "MIGRATE_DATABASE_URL", "ERP_ADMIN_URL", "AUDIT_DB_PASSWORD", "MIGRATE_DB_USER", "MIGRATE_DB_PASSWORD")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for n in ENV_NAMES:
        monkeypatch.delenv(n, raising=False)


def test_roles_follow_database_url_when_only_it_is_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://scm_ro:x@dbhost:5440/erp")
    assert db._url("ro").endswith("@dbhost:5440/erp")
    assert db._url("audit") == "postgresql+psycopg2://scm_audit:scm_audit@dbhost:5440/erp"
    assert db._url("migrate") == "postgresql+psycopg2://scm:scm@dbhost:5440/erp"
    assert db.describe("migrate") == "dbhost:5440/erp as scm" and "scm:scm" not in db.describe("migrate")


def test_explicit_variables_win(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://scm_ro:x@dbhost:5440/erp")
    monkeypatch.setenv("AUDIT_DATABASE_URL", "postgresql://a:b@other:6000/erp")
    monkeypatch.setenv("ERP_ADMIN_URL", "postgresql://admin:pw@adminhost:7000/erp")
    assert db._url("audit") == "postgresql+psycopg2://a:b@other:6000/erp"
    assert db._url("migrate").endswith("@adminhost:7000/erp")
    monkeypatch.setenv("MIGRATE_DATABASE_URL", "postgresql://m:n@mig:8000/erp")
    assert db._url("migrate").endswith("@mig:8000/erp")                 # MIGRATE_DATABASE_URL beats ERP_ADMIN_URL


def test_login_overrides_for_derived_roles(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://scm_ro:x@dbhost:5440/erp")
    monkeypatch.setenv("MIGRATE_DB_USER", "owner"); monkeypatch.setenv("MIGRATE_DB_PASSWORD", "p@ss/word")
    monkeypatch.setenv("AUDIT_DB_PASSWORD", "auditpw")
    assert db.get_engine.__name__ and "owner" in db._url("migrate") and "auditpw" in db._url("audit")


def test_without_any_configuration_the_defaults_are_the_standard_port(monkeypatch):
    assert db._url("ro").endswith("localhost:5432/erp") and db._url("audit").endswith("@localhost:5432/erp")
