"""Strict, non-secret TOML settings; credentials are never part of snapshots."""

from __future__ import annotations

import json
import os
import tomllib
from datetime import date
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Paths(StrictModel):
    database: Path = Path("data/agent.db")
    reports: Path = Path("data/reports")
    backups: Path = Path("data/backups")


class Search(StrictModel):
    count: int = Field(default=5, ge=1, le=50)
    need_content: bool = False
    daily_query_limit: int = Field(default=20, ge=1, le=50)
    cache_seconds: int = Field(default=86400, ge=0, le=86400)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    attempts: int = Field(default=2, ge=1, le=3)
    interval_seconds: float = Field(default=1, ge=0.1, le=60)
    keywords: list[str] = Field(default_factory=lambda: ["外贸 出口 企业 联系我们"])


class Model(StrictModel):
    name: Literal["deepseek-flash"] = "deepseek-flash"
    thinking: Literal["disabled", "enabled"] = "disabled"
    max_input_bytes: int = Field(default=24000, ge=100, le=100000)
    max_output_tokens: int = Field(default=2048, ge=128, le=8192)


class Pricing(StrictModel):
    verified_on: date | None = None
    search_micro_cny: int = Field(default=5000, ge=5000)
    input_micro_cny_per_million: int = Field(default=2000000, ge=2000000)
    output_micro_cny_per_million: int = Field(default=8000000, ge=8000000)
    source: Literal["https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"] = (
        "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
    )


class Retention(StrictModel):
    evidence_days: int | None = Field(default=None, ge=1)
    contacts_days: int | None = Field(default=None, ge=1)
    reports_days: int | None = Field(default=None, ge=1)
    logs_days: int | None = Field(default=None, ge=1)


class Product(StrictModel):
    description: str = "聚合跨境支付、多渠道、多币种；具体准入条件待确认"
    known_conditions: dict[str, Literal["supported", "unsupported", "unknown"]] = Field(
        default_factory=dict
    )


class Exclusion(StrictModel):
    name: str
    identity_type: str | None = None
    identity_value: str | None = None
    verified: bool = False
    reason: Literal["existing_customer", "refused", "unfit", "manual"] = "existing_customer"


class Settings(StrictModel):
    config_version: str = "1"
    cities: list[str] = Field(default_factory=lambda: ["珠海"], min_length=1)
    timezone: str = "Asia/Hong_Kong"
    monthly_budget_micro_cny: int = Field(default=500000000, gt=0, le=500000000)
    live_enabled: bool = False
    source_terms_confirmed: bool = False
    allowed_domains: list[str] = Field(default_factory=list)
    paths: Paths = Field(default_factory=Paths)
    search: Search = Field(default_factory=Search)
    model: Model = Field(default_factory=Model)
    pricing: Pricing = Field(default_factory=Pricing)
    retention: Retention = Field(default_factory=Retention)
    product: Product = Field(default_factory=Product)
    exclusions: list[Exclusion] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_timezone(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("未知业务时区") from exc
        if any(not city.strip() for city in self.cities):
            raise ValueError("城市不能为空")
        return self

    def snapshot(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)

    def require_live(self, *, continuous: bool = False) -> str:
        if not self.live_enabled:
            raise ValueError("真实调用未启用：live_enabled=false")
        if not self.source_terms_confirmed or self.pricing.verified_on is None:
            raise ValueError("真实调用需要确认来源条件及价格核对日期")
        if continuous and (
            not all(self.retention.model_dump().values()) or not self.allowed_domains
        ):
            raise ValueError("持续采集需要保存期限及允许域名")
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise ValueError("缺少DEEPSEEK_API_KEY环境变量")
        return key


def load_settings(path: Path | str = "config/settings.toml") -> Settings:
    path = Path(path).resolve()
    with path.open("rb") as handle:
        settings = Settings.model_validate(tomllib.load(handle))
    # Runtime paths are relative to the config directory, independent of the shell cwd.
    paths = {
        name: value if value.is_absolute() else (path.parent / value).resolve()
        for name, value in settings.paths.model_dump().items()
    }
    return settings.model_copy(update={"paths": Paths(**paths)})
