import json
import httpx
import pytest
from app.analysis.llm import DeepSeekLLM, Evidence
from app.budget import Budget
from app.crawlers.deepseek_search import DeepSeekSearch, plan_queries
from app.deepseek import ProviderError
from app.repositories import SearchCache


def test_search_contract_cache_and_cost(setup, gateway_factory):
    db, settings, run_id = setup
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
        body = json.loads(request.content)
        assert body == {"query": "珠海 site:example.com 出口", "count": 5, "need_content": False}
        return httpx.Response(
            200,
            json={"results": [dict(title="企业", url="https://example.com", summary="发现摘要")]},
        )

    search = DeepSeekSearch(gateway_factory(handler), SearchCache(db))
    result = search.search("珠海 site:example.com 出口", run_id)
    assert result["source_status"] == "AVAILABLE" and not result["cache_hit"]
    assert search.search("珠海 site:example.com 出口", run_id)["cache_hit"]
    assert len(requests) == 1
    assert Budget(db, settings).summary()["spent_micro_cny"] == 5000


def test_empty_search_does_not_charge(setup, gateway_factory):
    db, settings, run_id = setup
    search = DeepSeekSearch(
        gateway_factory(lambda _: httpx.Response(200, json={"results": []})), SearchCache(db)
    )
    assert search.search("珠海", run_id)["source_status"] == "EMPTY"
    assert Budget(db, settings).summary()["spent_micro_cny"] == 0


@pytest.mark.parametrize(
    "code,category", [(401, "unauthorized"), (402, "balance_insufficient"), (429, "rate_limited")]
)
def test_status_errors_are_sanitized(setup, gateway_factory, code, category):
    db, settings, run_id = setup
    search = DeepSeekSearch(
        gateway_factory(lambda _: httpx.Response(code, text="sensitive provider body")),
        SearchCache(db),
    )
    with pytest.raises(ProviderError) as exc:
        search.search("珠海", run_id)
    assert exc.value.category == category
    assert "sensitive" not in str(exc.value)
    assert Budget(db, settings).summary()["reserved_micro_cny"] == 0


def test_timeout_unknown_and_retry_new_reservation(setup, gateway_factory, monkeypatch):
    db, settings, run_id = setup
    monkeypatch.setattr("app.deepseek.time.sleep", lambda _: None)
    settings = settings.model_copy(
        update={"search": settings.search.model_copy(update={"attempts": 2})}
    )
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("secret body", request=request)
        return httpx.Response(200, json={"results": []})

    search = DeepSeekSearch(gateway_factory(handler, settings), SearchCache(db))
    search.search("珠海", run_id)
    assert len(attempts) == 2
    assert Budget(db, settings).summary()["unknown_micro_cny"] == 5000
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM cost_calls").fetchone()[0] == 2


@pytest.mark.parametrize("data", [{"unexpected": []}, {"results": ["invalid"]}])
def test_malformed_search_never_cached(setup, gateway_factory, data):
    db, _, run_id = setup
    search = DeepSeekSearch(
        gateway_factory(lambda _: httpx.Response(200, json=data)), SearchCache(db)
    )
    with pytest.raises(ProviderError):
        search.search("珠海", run_id)
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM search_cache").fetchone()[0] == 0


def model_response(facts, **kwargs):
    return {
        "model": "deepseek-flash",
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(facts)}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        **kwargs,
    }


def test_llm_contract_and_evidence_validation(setup, gateway_factory):
    db, settings, run_id = setup
    facts = {
        "claims": [
            {"kind": "outbound", "text": "存在出口业务", "page_id": "p1", "quote": "出口订单"}
        ],
        "unknowns": ["币种未知"],
    }

    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-flash"
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json=model_response(facts))

    result = DeepSeekLLM(gateway_factory(handler)).extract(
        "e1", [Evidence(page_id="p1", excerpt="本企业完成出口订单", content_hash="abc")], run_id
    )
    assert result["facts"] == facts
    assert Budget(db, settings).summary()["spent_micro_cny"] == 600


@pytest.mark.parametrize(
    "claim",
    [
        {"kind": "outbound", "text": "出口", "page_id": "forged", "quote": "出口订单"},
        {"kind": "outbound", "text": "出口", "page_id": "p1", "quote": "伪造摘录"},
    ],
)
def test_llm_invalid_references_are_charged_but_rejected(setup, gateway_factory, claim):
    db, settings, run_id = setup
    gateway = gateway_factory(
        lambda _: httpx.Response(200, json=model_response({"claims": [claim], "unknowns": []}))
    )
    with pytest.raises(ProviderError, match="invalid_model_facts"):
        DeepSeekLLM(gateway).extract(
            "e1", [Evidence(page_id="p1", excerpt="出口订单", content_hash="abc")], run_id
        )
    assert Budget(db, settings).summary()["spent_micro_cny"] == 600


def test_planner_preserves_operators(setup):
    _, settings, _ = setup
    settings = settings.model_copy(
        update={
            "search": settings.search.model_copy(
                update={"keywords": ['site:example.com "出口订单"', 'site:example.com "出口订单"']}
            )
        }
    )
    assert plan_queries(settings) == ['珠海 site:example.com "出口订单"']


def test_non_object_response_reserves_unknown_cost(setup, gateway_factory):
    db, settings, run_id = setup
    search = DeepSeekSearch(
        gateway_factory(lambda _: httpx.Response(200, json=[])), SearchCache(db)
    )
    with pytest.raises(ProviderError, match="invalid_response"):
        search.search("珠海", run_id)
    assert Budget(db, settings).summary()["unknown_micro_cny"] == 5000


def test_model_missing_usage_keeps_reservation(setup, gateway_factory):
    db, settings, run_id = setup
    response = model_response({"claims": [], "unknowns": ["未知"]}, usage={})
    llm = DeepSeekLLM(gateway_factory(lambda _: httpx.Response(200, json=response)))
    llm.extract("e1", [Evidence(page_id="p1", excerpt="业务介绍", content_hash="abc")], run_id)
    assert Budget(db, settings).summary()["unknown_micro_cny"] > 0
