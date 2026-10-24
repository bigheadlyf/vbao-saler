import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from app.budget import Budget, BudgetStopped
from app.cli import main
from app.config import Settings, load_settings
from app.repositories import IdempotencyConflict, RunRepository


def test_config_is_strict_and_default_live_disabled():
    settings = load_settings("config/settings.toml")
    assert settings.cities == ["珠海"]
    assert settings.paths.database.is_absolute()
    with pytest.raises(ValueError):
        settings.require_live()
    assert "DEEPSEEK_API_KEY" not in settings.snapshot()
    assert len(settings.exclusions) == 3
    assert not any(e.verified for e in settings.exclusions)


@pytest.mark.parametrize(
    "values",
    [
        {"monthly_budget_micro_cny": 500000001},
        {"cities": []},
        {"cities": [" "]},
        {"timezone": "bad-zone"},
        {"api_key": "not-allowed"},
        {"search": {"count": 51}},
        {"model": {"name": "other-model"}},
        {"pricing": {"search_micro_cny": 0}},
    ],
)
def test_invalid_config(values):
    with pytest.raises(ValidationError):
        Settings.model_validate(values)


def test_live_requires_key_pricing_and_continuous_retention(setup, monkeypatch):
    _, settings, _ = setup
    assert settings.require_live() == "unit-test-credential"
    with pytest.raises(ValueError):
        settings.require_live(continuous=True)
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(ValueError):
        settings.require_live()


def test_db_migration_foreign_keys_and_rollback(setup):
    db, _, _ = setup
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert (
            conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 9
        )
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO enterprise_pages VALUES('missing','page','BUSINESS','UNKNOWN','{}')"
        )
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("DELETE FROM runs")
        raise RuntimeError("abort")
    assert len(RunRepository(db).list()) == 1


def test_enqueue_concurrent_idempotency_preserves_snapshot(setup):
    db, _, original = setup

    def submit(_):
        return RunRepository(db).enqueue("first", '{"changed":true}')

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert set(pool.map(submit, range(12))) == {original}
    with db.connect() as conn:
        assert "changed" not in conn.execute("SELECT config_json FROM runs").fetchone()[0]
    with pytest.raises(IdempotencyConflict):
        RunRepository(db).enqueue("first", "{}", {"different": True})


def test_budget_atomic_under_concurrent_reservations(setup):
    db, settings, run_id = setup
    budget = Budget(db, settings.model_copy(update={"monthly_budget_micro_cny": 5000}))

    def reserve(_):
        try:
            return budget.reserve(run_id, "deepseek.search", 5000)
        except BudgetStopped:
            return None

    with ThreadPoolExecutor(max_workers=5) as pool:
        calls = list(pool.map(reserve, range(10)))
    assert len([c for c in calls if c]) == 1
    assert budget.summary()["remaining_micro_cny"] == 0


def test_unknown_and_cross_month_preserve_reservations(setup):
    db, settings, run_id = setup
    budget = Budget(db, settings)
    at = datetime(2026, 10, 31, 15, 59, tzinfo=timezone.utc)
    call = budget.reserve(run_id, "deepseek.search", 5000, at=at)
    budget.settle(call)
    assert budget.summary(at)["unknown_micro_cny"] == 5000
    assert (
        budget.summary(datetime(2026, 10, 31, 16, 1, tzinfo=timezone.utc))["reserved_micro_cny"]
        == 0
    )
    assert budget.summary(at)["reserved_micro_cny"] == 5000
    budget.settle(call, 0)
    assert budget.summary(at)["remaining_micro_cny"] == 500000000


def test_overage_freezes_even_next_month_and_final_cost_not_overwritten(setup):
    db, settings, run_id = setup
    budget = Budget(db, settings)
    call = budget.reserve(run_id, "deepseek.search", 5000)
    budget.settle(call, 6000)
    assert budget.summary()["frozen"]
    with pytest.raises(BudgetStopped):
        budget.reserve(
            run_id, "deepseek.search", 5000, at=datetime(2027, 1, 1, tzinfo=timezone.utc)
        )
    with pytest.raises(ValueError):
        budget.settle(call, 0)


def test_daily_limit_counts_failed_attempts(setup):
    db, settings, run_id = setup
    search = settings.search.model_copy(update={"daily_query_limit": 1})
    budget = Budget(db, settings.model_copy(update={"search": search}))
    call = budget.reserve(run_id, "deepseek.search", 5000)
    budget.settle(call, 0)
    with pytest.raises(BudgetStopped):
        budget.reserve(run_id, "deepseek.search", 5000)


def test_cli_smoke(tmp_path, capsys):
    config = tmp_path / "settings.toml"
    config.write_text('[paths]\ndatabase="agent.db"\n')
    for command in ("check-config", "init-db", "budget", "status", "plan-queries"):
        assert main(["--config", str(config), command]) == 0
        json.loads(capsys.readouterr().out)
    assert main(["--config", str(config), "enqueue", "--key", "smoke"]) == 0
    assert json.loads(capsys.readouterr().out)["worker_implemented"] is False
    assert main(["--config", str(config), "search", "珠海 出口"]) == 1
    assert "DEEPSEEK_API_KEY" not in capsys.readouterr().err


def test_config_relative_paths_independent_of_cwd(tmp_path, monkeypatch):
    config = Path("config/settings.toml").resolve()
    expected = load_settings(config).paths.database
    monkeypatch.chdir(tmp_path)
    assert load_settings(config).paths.database == expected


@pytest.mark.parametrize("occupied, warning", [(350000000, 350), (450000000, 450)])
def test_budget_warning_thresholds(setup, occupied, warning):
    db, settings, run_id = setup
    budget = Budget(db, settings)
    budget.reserve(run_id, "deepseek.llm", occupied)
    assert budget.summary()["warning"] == warning
