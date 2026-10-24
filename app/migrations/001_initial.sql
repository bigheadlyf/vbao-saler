CREATE TABLE runs (
 id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
 trigger TEXT NOT NULL, scheduled_for TEXT, status TEXT NOT NULL DEFAULT 'QUEUED'
 CHECK(status IN ('QUEUED','RUNNING','SUCCEEDED','PARTIAL','FAILED','BUDGET_STOPPED','INTERRUPTED')),
 stage TEXT, config_json TEXT NOT NULL, stats_json TEXT NOT NULL DEFAULT '{}',
 error_json TEXT NOT NULL DEFAULT '{}', export_status TEXT NOT NULL DEFAULT 'UNPUBLISHED',
 report_path TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE TABLE enterprises (
 id TEXT PRIMARY KEY, legal_name TEXT NOT NULL, identity_region TEXT, identity_type TEXT,
 identity_value TEXT, city TEXT, region_status TEXT NOT NULL DEFAULT 'UNKNOWN', website TEXT,
 excluded_reason TEXT, first_delivered_run_id TEXT REFERENCES runs(id), last_delivered_hash TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK(identity_value IS NULL OR (identity_region IS NOT NULL AND identity_type IS NOT NULL)),
 UNIQUE(identity_region,identity_type,identity_value)
);
CREATE TABLE pages (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, url TEXT NOT NULL, canonical_url TEXT NOT NULL,
 title TEXT, published_at TEXT, fetched_at TEXT NOT NULL, content_hash TEXT NOT NULL,
 excerpt TEXT NOT NULL, fetch_status TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
 UNIQUE(canonical_url,content_hash)
);
CREATE TABLE enterprise_pages (
 enterprise_id TEXT NOT NULL REFERENCES enterprises(id), page_id TEXT NOT NULL REFERENCES pages(id),
 relation_type TEXT NOT NULL, attribution_status TEXT NOT NULL, evidence_json TEXT NOT NULL,
 PRIMARY KEY(enterprise_id,page_id)
);
CREATE TABLE contacts (
 id TEXT PRIMARY KEY, enterprise_id TEXT NOT NULL REFERENCES enterprises(id), type TEXT NOT NULL,
 value TEXT NOT NULL, normalized_value TEXT NOT NULL, attribution_status TEXT NOT NULL,
 evidence_json TEXT NOT NULL, last_checked_at TEXT NOT NULL,
 UNIQUE(enterprise_id,type,normalized_value)
);
CREATE TABLE analyses (
 id TEXT PRIMARY KEY, enterprise_id TEXT NOT NULL REFERENCES enterprises(id), llm_input_hash TEXT NOT NULL,
 facts_json TEXT NOT NULL, assessment_json TEXT NOT NULL, evidence_refs_json TEXT NOT NULL,
 model_version TEXT NOT NULL, prompt_version TEXT NOT NULL, rule_version TEXT NOT NULL,
 product_version TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE run_items (
 run_id TEXT NOT NULL REFERENCES runs(id), enterprise_id TEXT NOT NULL REFERENCES enterprises(id),
 analysis_id TEXT NOT NULL REFERENCES analyses(id), change_type TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 PRIMARY KEY(run_id,enterprise_id)
);
CREATE TABLE cost_calls (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), provider TEXT NOT NULL,
 billing_month TEXT NOT NULL, billing_day TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('RESERVED','SETTLED','UNKNOWN','RELEASED','OVERAGE')),
 reserved_micro_cny INTEGER NOT NULL CHECK(reserved_micro_cny>=0),
 actual_micro_cny INTEGER CHECK(actual_micro_cny>=0), pricing_snapshot_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE search_cache (
 cache_key TEXT PRIMARY KEY, query TEXT NOT NULL, params_json TEXT NOT NULL, provider_version TEXT NOT NULL,
 result_json TEXT NOT NULL, engine_errors_json TEXT NOT NULL DEFAULT '[]', fetched_at TEXT NOT NULL,
 expires_at TEXT NOT NULL
);
CREATE INDEX runs_status_created ON runs(status,created_at);
CREATE INDEX enterprise_pages_page ON enterprise_pages(page_id);
CREATE INDEX contacts_enterprise ON contacts(enterprise_id);
CREATE INDEX analyses_enterprise_created ON analyses(enterprise_id,created_at);
CREATE INDEX cost_calls_month_status ON cost_calls(billing_month,status);
CREATE INDEX cost_calls_day_provider ON cost_calls(billing_day,provider);
CREATE INDEX pages_url_hash ON pages(canonical_url,content_hash);
