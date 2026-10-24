import json
import httpx
from app.probe import run_probe
from app.repositories import SearchCache, fingerprint


def test_probe_forces_network_and_keeps_contract_separate_from_business(setup):
    db, settings, _ = setup
    params = dict(query="珠海 外贸 出口 企业 官网", count=3, need_content=False)
    SearchCache(db).put(
        fingerprint(dict(provider="deepseek-search-v1", params=params)),
        params,
        {"results": [], "source_status": "EMPTY"},
        86400,
    )
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200, json={"results": [{"title": "真实请求替身", "url": "https://example.com"}]}
            )
        assert "虚构企业" in json.loads(request.content)["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"claims":[],"unknowns":["需求未知"]}'},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_probe(db, settings, client)
    assert calls == ["/v1/tools/search", "/chat/completions"]
    assert report["probe_status"] == "PASSED"
    assert not report["model"]["business_validation"]
    assert report["search"]["cache_hit"] is False
    assert sum(c["actual_micro_cny"] for c in report["calls"]) == 5600
    with db.connect() as conn:
        assert (
            conn.execute("SELECT status FROM runs WHERE id=?", (report["run_id"],)).fetchone()[0]
            == "PARTIAL"
        )


def test_probe_auth_failure_is_recorded_without_retry_or_key(setup):
    db, settings, _ = setup
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="private"))
    ) as client:
        report = run_probe(db, settings, client)
    assert report["error"]["http_status"] == 401
    assert report["probe_status"] == "FAILED"
    assert len(report["calls"]) == 1
    assert "unit-test-credential" not in json.dumps(report)
