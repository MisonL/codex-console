from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from src.database.models import Account, Base
from src.database.session import DatabaseSessionManager
from src.web.routes import accounts as accounts_routes


def test_list_accounts_uses_lightweight_projection(monkeypatch):
    runtime_dir = Path("tests_runtime")
    runtime_dir.mkdir(exist_ok=True)
    db_path = runtime_dir / "account_list_perf.db"
    if db_path.exists():
        db_path.unlink()

    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    now = datetime(2026, 4, 13, 12, 0, 0)
    with manager.session_scope() as session:
        session.add_all(
            [
                Account(
                    email="healthy@example.com",
                    password="secret-1",
                    email_service="manual",
                    access_token="access-token",
                    refresh_token="refresh-token",
                    id_token="id-token",
                    account_id="acct_healthy",
                    session_token="session-token",
                    role_tag="parent",
                    account_label="mother",
                    priority=5,
                    last_used_at=now,
                    created_at=now,
                    status="active",
                ),
                Account(
                    email="blocked@example.com",
                    password="secret-2",
                    email_service="manual",
                    session_token="session-token",
                    extra_data={"codex_auth": {"last_block_reason": "需要先过 add-phone"}},
                    created_at=now - timedelta(minutes=1),
                    status="active",
                ),
                Account(
                    email="missing@example.com",
                    password="",
                    email_service="manual",
                    created_at=now - timedelta(minutes=2),
                    status="active",
                ),
            ]
        )

    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    def fail_if_called(_account):
        raise AssertionError("list_accounts 不应逐行调用 resolve_codex_auth_status")

    monkeypatch.setattr(accounts_routes, "get_db", fake_get_db)
    monkeypatch.setattr(accounts_routes, "resolve_codex_auth_status", fail_if_called)

    response = accounts_routes.list_accounts(
        page=1,
        page_size=20,
        status=None,
        email_service=None,
        search=None,
    )

    assert response.total == 3
    assert [item.email for item in response.accounts] == [
        "healthy@example.com",
        "blocked@example.com",
        "missing@example.com",
    ]
    assert response.accounts[0].codex_auth["health"] == "healthy"
    assert response.accounts[0].role_tag == "parent"
    assert response.accounts[0].account_label == "mother"
    assert response.accounts[0].priority == 5
    assert response.accounts[1].codex_auth["health"] == "blocked"
    assert response.accounts[1].codex_auth["reason"] == "需要先过 add-phone"
    assert response.accounts[2].codex_auth["health"] == "missing_prerequisites"


def test_sqlite_session_manager_enables_wal_and_longer_busy_timeout():
    runtime_dir = Path("tests_runtime")
    runtime_dir.mkdir(exist_ok=True)
    db_path = runtime_dir / "sqlite_pragmas.db"
    if db_path.exists():
        db_path.unlink()

    manager = DatabaseSessionManager(f"sqlite:///{db_path}")

    with manager.engine.connect() as conn:
        journal_mode = str(conn.execute(text("PRAGMA journal_mode")).scalar() or "").lower()
        busy_timeout = int(conn.execute(text("PRAGMA busy_timeout")).scalar() or 0)

    assert journal_mode == "wal"
    assert busy_timeout == 60000


def test_migrate_tables_adds_accounts_created_at_index():
    runtime_dir = Path("tests_runtime")
    runtime_dir.mkdir(exist_ok=True)
    db_path = runtime_dir / "accounts_created_at_index.db"
    if db_path.exists():
        db_path.unlink()

    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    manager.create_tables()

    with manager.engine.connect() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_accounts_created_at"))
        conn.commit()

    manager.migrate_tables()

    with manager.engine.connect() as conn:
        indexes = conn.execute(text("PRAGMA index_list('accounts')")).fetchall()

    assert any(str(row[1]) == "ix_accounts_created_at" for row in indexes)
