"""Atomic reservations. Unknown bills remain occupied, including after a month boundary."""

import json
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
from .config import Settings
from .db import Database, utcnow


class BudgetStopped(RuntimeError):
    pass


class Budget:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.settings = db, settings

    def _period(self, at=None):
        at = at or datetime.now(timezone.utc)
        if at.tzinfo is None:
            raise ValueError("计费时间必须带时区")
        local = at.astimezone(ZoneInfo(self.settings.timezone))
        return local.strftime("%Y-%m"), local.strftime("%Y-%m-%d")

    @staticmethod
    def _summary(conn, month):
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN status IN ('SETTLED','OVERAGE') "
            "THEN actual_micro_cny ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN status IN ('RESERVED','UNKNOWN') "
            "THEN reserved_micro_cny ELSE 0 END),0), "
            "COALESCE(SUM(CASE WHEN status='UNKNOWN' THEN reserved_micro_cny ELSE 0 END),0) "
            "FROM cost_calls WHERE billing_month=?",
            (month,),
        ).fetchone()
        return tuple(row)

    def summary(self, at=None):
        month, _ = self._period(at)
        with self.db.connect() as conn:
            spent, reserved, unknown = self._summary(conn, month)
            frozen = bool(
                conn.execute("SELECT 1 FROM cost_calls WHERE status='OVERAGE' LIMIT 1").fetchone()
            )
        used = spent + reserved
        return dict(
            month=month,
            spent_micro_cny=spent,
            reserved_micro_cny=reserved,
            unknown_micro_cny=unknown,
            remaining_micro_cny=max(0, self.settings.monthly_budget_micro_cny - used),
            warning=450 if used >= 450000000 else 350 if used >= 350000000 else None,
            frozen=frozen,
        )

    def reserve(self, run_id, provider, maximum, *, at=None):
        if type(maximum) is not int or maximum <= 0:
            raise ValueError("预留金额必须是正整数微元")
        month, day = self._period(at)
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM cost_calls WHERE status='OVERAGE' LIMIT 1").fetchone():
                raise BudgetStopped("存在超预留账单，暂停新增收费调用")
            spent, reserved, _ = self._summary(conn, month)
            if spent + reserved + maximum > self.settings.monthly_budget_micro_cny:
                raise BudgetStopped("月预算不足")
            if provider == "deepseek.search":
                attempts = conn.execute(
                    "SELECT count(*) FROM cost_calls WHERE provider=? AND billing_day=?",
                    (provider, day),
                ).fetchone()[0]
                if attempts >= self.settings.search.daily_query_limit:
                    raise BudgetStopped("每日搜索尝试额度不足（含重试）")
            call_id = "call_" + uuid4().hex
            now = utcnow()
            conn.execute(
                "INSERT INTO cost_calls(id,run_id,provider,billing_month,billing_day,status,"
                "reserved_micro_cny,pricing_snapshot_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'RESERVED',?,?,?,?)",
                (
                    call_id,
                    run_id,
                    provider,
                    month,
                    day,
                    maximum,
                    json.dumps(self.settings.pricing.model_dump(mode="json")),
                    now,
                    now,
                ),
            )
            return call_id

    def settle(self, call_id, actual=None):
        if actual is not None and (type(actual) is not int or actual < 0):
            raise ValueError("费用必须是非负整数微元或未知")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM cost_calls WHERE id=?", (call_id,)).fetchone()
            if not row:
                raise ValueError("调用不存在")
            if row["status"] not in ("RESERVED", "UNKNOWN"):
                if actual == row["actual_micro_cny"]:
                    return
                raise ValueError("已结算调用不可覆盖")
            status = (
                "UNKNOWN"
                if actual is None
                else ("OVERAGE" if actual > row["reserved_micro_cny"] else "SETTLED")
            )
            conn.execute(
                "UPDATE cost_calls SET status=?,actual_micro_cny=?,updated_at=? WHERE id=?",
                (status, actual, utcnow(), call_id),
            )
