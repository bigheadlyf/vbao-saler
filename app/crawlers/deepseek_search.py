"""Discovery only: snippets and summaries never become verified business evidence."""

from datetime import datetime
import time
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ..deepseek import Gateway, ProviderError
from ..repositories import SearchCache, fingerprint


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    url: str
    snippet: str = ""
    summary: str = ""
    content: str = ""
    date: datetime | None = None


class SearchResponse(BaseModel):
    results: list[SearchResult] = Field(max_length=50)


def plan_queries(settings):
    result = []
    for city in settings.cities:
        for keyword in settings.search.keywords:
            query = f"{city} {keyword}".strip()
            if not query or len(query) > 128:
                raise ValueError("搜索词必须为1—128字符")
            if query not in result:
                result.append(query)
    return result[: settings.search.daily_query_limit]


class DeepSeekSearch:
    def __init__(self, gateway: Gateway, cache: SearchCache):
        self.gateway, self.cache = gateway, cache
        self.last_request_at = 0.0

    def search(self, query: str, run_id: str):
        settings = self.gateway.settings
        if not query.strip() or len(query) > 128:
            raise ValueError("搜索词必须为1—128字符")
        params = dict(
            query=query, count=settings.search.count, need_content=settings.search.need_content
        )
        key = fingerprint(dict(provider="deepseek-search-v1", params=params))
        cached = self.cache.get(key)
        if cached is not None:
            return dict(cache_hit=True, **cached)
        wait = settings.search.interval_seconds - (time.monotonic() - self.last_request_at)
        if wait > 0:
            time.sleep(wait)
        self.last_request_at = time.monotonic()

        def cost(data):
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError("缺少results数组")
            return settings.pricing.search_micro_cny if data["results"] else 0

        data = self.gateway.post(
            "/v1/tools/search",
            params,
            run_id,
            "deepseek.search",
            settings.pricing.search_micro_cny,
            cost,
        )
        try:
            parsed = SearchResponse.model_validate(data)
        except ValidationError:
            raise ProviderError("invalid_search_results") from None
        result = dict(
            results=parsed.model_dump(mode="json")["results"],
            source_status="AVAILABLE" if parsed.results else "EMPTY",
        )
        # Empty results are not long-lived cached: do not conceal a transient coverage gap.
        if parsed.results and settings.search.cache_seconds:
            self.cache.put(key, params, result, settings.search.cache_seconds)
        return dict(cache_hit=False, **result)
