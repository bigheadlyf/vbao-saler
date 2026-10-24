"""Local foundation commands; enqueue does not start a business Worker."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4
from pydantic import ValidationError
from .budget import Budget, BudgetStopped
from .config import load_settings
from .crawlers.deepseek_search import DeepSeekSearch, plan_queries
from .db import Database, utcnow
from .deepseek import Gateway, ProviderError
from .repositories import RunRepository, SearchCache


def main(argv=None):
    parser = argparse.ArgumentParser(description="出海商机Agent基础开发命令")
    parser.add_argument("--config", type=Path, default=Path("config/settings.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check-config", "init-db", "budget", "status", "plan-queries"):
        commands.add_parser(name)
    enqueue = commands.add_parser("enqueue", help="仅持久化任务；Worker尚未实现")
    enqueue.add_argument("--key", required=True)
    search = commands.add_parser("search", help="一次性发现验证，不执行企业分析或导出")
    search.add_argument("query")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        db = Database(settings.paths.database)
        if args.command == "check-config":
            output = dict(
                valid=True,
                live_enabled=settings.live_enabled,
                model=settings.model.name,
                timezone=settings.timezone,
            )
        elif args.command == "plan-queries":
            output = plan_queries(settings)
        elif args.command == "init-db":
            db.initialize()
            output = dict(initialized=True, schema_version=1, sqlite_version=sqlite3.sqlite_version)
        elif args.command == "budget":
            output = Budget(db, settings).summary()
        elif args.command == "status":
            output = RunRepository(db).list()
        elif args.command == "enqueue":
            output = dict(
                run_id=RunRepository(db).enqueue(args.key, settings.snapshot()),
                status="QUEUED",
                worker_implemented=False,
            )
        else:
            settings.require_live()
            run_id = RunRepository(db).enqueue("probe-" + uuid4().hex, settings.snapshot())
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE runs SET status='RUNNING',stage='SEARCH',started_at=? WHERE id=?",
                    (utcnow(), run_id),
                )
            gateway = Gateway(settings, Budget(db, settings))
            try:
                result = DeepSeekSearch(gateway, SearchCache(db)).search(args.query, run_id)
            except Exception:
                with db.transaction() as conn:
                    conn.execute(
                        "UPDATE runs SET status='FAILED',finished_at=? WHERE id=?",
                        (utcnow(), run_id),
                    )
                raise
            finally:
                gateway.close()
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE runs SET status='PARTIAL',stage='SEARCH_ONLY',stats_json=?,finished_at=? WHERE id=?",
                    (json.dumps(dict(result_count=len(result["results"]))), utcnow(), run_id),
                )
            output = dict(run_id=run_id, scope="search_only", **result)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except ValidationError as exc:
        # Pydantic default errors include raw input; never echo those into operator logs.
        fields = [".".join(map(str, e["loc"])) for e in exc.errors()]
        print("配置校验失败：" + ", ".join(fields), file=sys.stderr)
    except (BudgetStopped, ProviderError) as exc:
        print(f"命令失败：{exc}", file=sys.stderr)
    except (ValueError, OSError, sqlite3.Error) as exc:
        # Provider errors contain category/status only, never response bodies or credentials.
        print(f"命令失败：{type(exc).__name__}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
