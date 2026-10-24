import httpx
import pytest
from app.budget import Budget
from app.config import Settings
from app.db import Database
from app.deepseek import Gateway
from app.repositories import RunRepository


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-credential")
    settings = Settings.model_validate(
        {
            "live_enabled": True,
            "source_terms_confirmed": True,
            "pricing": {"verified_on": "2026-10-24"},
            "search": {"attempts": 1},
        }
    )
    db = Database(tmp_path / "test.db")
    db.initialize()
    run_id = RunRepository(db).enqueue("first", settings.snapshot())
    return db, settings, run_id


@pytest.fixture
def gateway_factory(setup):
    clients = []

    def make(handler, settings=None):
        db, defaults, _ = setup
        settings = settings or defaults
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        return Gateway(settings, Budget(db, settings), client)

    yield make
    for client in clients:
        client.close()
