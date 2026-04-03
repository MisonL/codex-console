from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.models import Account, Base, BindCardTask
from src.web.routes import payment as payment_routes


def test_bind_fail_stats_route_returns_failed_counts_without_405(monkeypatch):
    with TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "payment_fail_stats.db"
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SessionLocal = sessionmaker(bind=engine)
        Base.metadata.create_all(engine)

        session = SessionLocal()
        try:
            account_one = Account(email="a@example.com", email_service="duckmail", status="active")
            account_two = Account(email="b@example.com", email_service="duckmail", status="active")
            session.add_all([account_one, account_two])
            session.commit()
            session.refresh(account_one)
            session.refresh(account_two)
            account_one_id = account_one.id
            account_two_id = account_two.id
            account_one_email = account_one.email
            account_two_email = account_two.email

            session.add_all(
                [
                    BindCardTask(
                        account_id=account_one_id,
                        account_email=account_one_email,
                        plan_type="plus",
                        checkout_url="https://example.com/checkout/a1",
                        status="failed",
                    ),
                    BindCardTask(
                        account_id=account_one_id,
                        account_email=account_one_email,
                        plan_type="plus",
                        checkout_url="https://example.com/checkout/a2",
                        status="failed",
                    ),
                    BindCardTask(
                        account_id=account_one_id,
                        account_email=account_one_email,
                        plan_type="plus",
                        checkout_url="https://example.com/checkout/a3",
                        status="completed",
                    ),
                    BindCardTask(
                        account_id=account_two_id,
                        account_email=account_two_email,
                        plan_type="team",
                        checkout_url="https://example.com/checkout/b1",
                        status="failed",
                    ),
                ]
            )
            session.commit()
        finally:
            session.close()

        @contextmanager
        def override_get_db():
            db = SessionLocal()
            try:
                yield db
            finally:
                db.close()

        monkeypatch.setattr(payment_routes, "get_db", override_get_db)

        app = FastAPI()
        app.include_router(payment_routes.router, prefix="/payment")
        client = TestClient(app)

        response = client.post(
            "/payment/bind-card/tasks/fail-stats",
            json={"account_ids": [account_one_id, account_two_id, 9999]},
        )

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "stats": [
                {"account_id": account_one_id, "fail_count": 2},
                {"account_id": account_two_id, "fail_count": 1},
                {"account_id": 9999, "fail_count": 0},
            ],
        }
