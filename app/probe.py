"""One-search/one-model live contract check; never represents a business pipeline run."""

import argparse
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .analysis.llm import DeepSeekLLM, Evidence
from .budget import Budget, BudgetStopped
from .config import Settings, load_settings
from .crawlers.deepseek_search import DeepSeekSearch
from .db import Database, utcnow
from .deepseek import Gateway, ProviderError
from .repositories import RunRepository, SearchCache


class NoReadCache(SearchCache):
    """Force the smoke test through the actual provider, while preserving normal cache writes."""

    def get(self, key):
        return None


def run_probe(db: Database, settings: Settings, client=None):
    settings.require_live()
    # One attempt per endpoint, bounded output; never enable continuous collection here.
    settings = settings.model_copy(
        update={
            "search": settings.search.model_copy(update={"count": 3, "attempts": 1}),
            "model": settings.model.model_copy(update={"max_output_tokens": 512}),
        }
    )
    maximum_estimate = (
        settings.pricing.search_micro_cny
        + (
            settings.model.max_input_bytes * settings.pricing.input_micro_cny_per_million
            + settings.model.max_output_tokens * settings.pricing.output_micro_cny_per_million
            + 999999
        )
        // 1000000
    )
    if maximum_estimate > 100000:
        raise BudgetStopped("联调配置最高估算超过0.1元，请降低输入输出上限")
    budget = Budget(db, settings)
    # A small allowance check, in addition to the atomic ledger gate at each call.
    if budget.summary()["remaining_micro_cny"] < 100000:
        raise BudgetStopped("联调至少需要0.1元可用预算")
    run_id = RunRepository(db).enqueue("live-probe-" + uuid4().hex, settings.snapshot())
    report = {"run_id": run_id, "scope": "provider_contract_only", "started_at": utcnow()}
    with db.transaction() as conn:
        conn.execute(
            "UPDATE runs SET status='RUNNING',stage='LIVE_PROBE',started_at=? WHERE id=?",
            (report["started_at"], run_id),
        )
    gateway = Gateway(settings, budget, client)
    try:
        search = DeepSeekSearch(gateway, NoReadCache(db)).search(
            "珠海 外贸 出口 企业 官网",
            run_id,
        )
        report["search"] = {
            "network_response_received": True,
            "cache_hit": search["cache_hit"],
            "source_status": search["source_status"],
            "result_count": len(search["results"]),
            "urls": [r["url"] for r in search["results"]],
        }
        # Explicitly synthetic: verify protocol and quote handling without sending customer data.
        excerpt = "这是接口联调虚构企业的测试文字：本企业有出口订单。币种及支付需求未知。"
        model = DeepSeekLLM(gateway).extract(
            "probe_synthetic_company",
            [
                Evidence(
                    page_id="probe_synthetic_page",
                    excerpt=excerpt,
                    content_hash=hashlib.sha256(excerpt.encode()).hexdigest(),
                )
            ],
            run_id,
        )
        report["model"] = {
            "network_response_received": True,
            "model": model["model"],
            "usage": model["usage"],
            "claim_count": len(model["facts"]["claims"]),
            "evidence_kind": "synthetic_contract_test",
            "business_validation": False,
        }
        report["probe_status"] = "PASSED"
        status = "PARTIAL"  # Full lead pipeline was not executed.
    except (ProviderError, BudgetStopped) as exc:
        report["probe_status"] = "FAILED"
        report["error"] = {
            "category": getattr(exc, "category", type(exc).__name__),
            "http_status": getattr(exc, "status_code", None),
        }
        status = "BUDGET_STOPPED" if isinstance(exc, BudgetStopped) else "FAILED"
    except Exception:
        # No raw exception/provider response is persisted: it may contain sensitive data.
        report["probe_status"] = "FAILED"
        report["error"] = {"category": "unexpected_error"}
        status = "FAILED"
        raise
    finally:
        gateway.close()
        report["finished_at"] = utcnow()
        report["budget"] = budget.summary()
        with db.connect() as conn:
            report["calls"] = [
                dict(row)
                for row in conn.execute(
                    "SELECT provider,status,reserved_micro_cny,actual_micro_cny "
                    "FROM cost_calls WHERE run_id=? ORDER BY created_at",
                    (run_id,),
                )
            ]
        with db.transaction() as conn:
            conn.execute(
                "UPDATE runs SET status=?,stage='PROBE_ONLY',stats_json=?,finished_at=? WHERE id=?",
                (status, json.dumps(report, ensure_ascii=False), report["finished_at"], run_id),
            )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="真实DeepSeek契约联调：一次搜索和一次模型调用")
    parser.add_argument("--config", type=Path, default=Path("config/settings.toml"))
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    db = Database(settings.paths.database)
    # Reuse the initialized ledger. Do not silently create a new budget database.
    report = run_probe(db, settings)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["probe_status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
