"""Fixed-endpoint HTTP transport with per-attempt reservations and bounded retries."""

import time
import httpx
from .budget import Budget
from .config import Settings

BASE_URL = "https://api.deepseek.com"


class ProviderError(RuntimeError):
    def __init__(self, category: str, status_code: int | None = None):
        self.category, self.status_code = category, status_code
        super().__init__(f"DeepSeek {category}" + (f" (HTTP {status_code})" if status_code else ""))


class Gateway:
    def __init__(self, settings: Settings, budget: Budget, client: httpx.Client | None = None):
        self.settings, self.budget = settings, budget
        self.client = client or httpx.Client(
            timeout=settings.search.timeout_seconds,
            follow_redirects=False,
        )
        self.owned = client is None

    def close(self):
        if self.owned:
            self.client.close()

    def post(self, path, payload, run_id, provider, maximum, calculate_cost):
        if path not in ("/v1/tools/search", "/chat/completions"):
            raise ValueError("不允许的DeepSeek端点")
        key = self.settings.require_live()
        for attempt in range(self.settings.search.attempts):
            call_id = self.budget.reserve(run_id, provider, maximum)
            try:
                response = self.client.post(
                    BASE_URL + path,
                    json=payload,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                )
            except httpx.HTTPError:
                # Client-side timeout/disconnect does not prove the server did not bill.
                self.budget.settle(call_id)
                error = ProviderError("transport_unknown")
            else:
                if response.status_code != 200:
                    # Search documented non-200 responses are nonbillable. Model is conservative.
                    self.budget.settle(call_id, 0 if provider == "deepseek.search" else None)
                    category = {
                        401: "unauthorized",
                        402: "balance_insufficient",
                        429: "rate_limited",
                    }.get(response.status_code, "http_failure")
                    error = ProviderError(category, response.status_code)
                    if response.status_code not in (408, 429, 500, 502, 503, 504):
                        raise error
                else:
                    try:
                        data = response.json()
                        if not isinstance(data, dict):
                            raise ValueError("响应必须为JSON对象")
                        actual = calculate_cost(data)
                    except (ValueError, TypeError, KeyError, IndexError):
                        self.budget.settle(call_id)
                        raise ProviderError("invalid_response") from None
                    self.budget.settle(call_id, actual)
                    return data
            if attempt + 1 < self.settings.search.attempts:
                time.sleep(min(2**attempt, 4))
        raise error
