"""Initial run and query-cache repositories; other domain repositories follow later."""

import hashlib
import json
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from .db import Database, utcnow


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class IdempotencyConflict(ValueError):
    pass


class RunRepository:
    def __init__(self, db: Database):
        self.db = db

    def enqueue(self, key: str, config_json: str, request: dict | None = None) -> str:
        if not key.strip() or len(key) > 128:
            raise ValueError("幂等键长度必须为1—128")
        request_hash = fingerprint(request or {})
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM runs WHERE idempotency_key=?", (key,)).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise IdempotencyConflict("幂等键对应不同请求")
                return row["id"]
            run_id = "run_" + uuid4().hex
            conn.execute(
                "INSERT INTO runs(id,idempotency_key,request_hash,trigger,config_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (run_id, key, request_hash, "manual", config_json, utcnow()),
            )
            return run_id

    def list(self, limit=20):
        with self.db.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT id,status,stage,created_at,export_status FROM runs ORDER BY created_at DESC LIMIT ?",
                    (min(max(limit, 1), 100),),
                )
            ]


class SearchCache:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM search_cache WHERE cache_key=? AND expires_at>?",
                (key, utcnow()),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, key, params, results, ttl):
        now = datetime.now(timezone.utc)
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO search_cache "
                "(cache_key,query,params_json,provider_version,result_json,fetched_at,expires_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    key,
                    params["query"],
                    canonical_json(params),
                    "deepseek-search-v1",
                    canonical_json(results),
                    now.isoformat(),
                    (now + timedelta(seconds=ttl)).isoformat(),
                ),
            )
