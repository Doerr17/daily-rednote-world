CREATE TABLE IF NOT EXISTS research_runs (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    topic_id INTEGER NOT NULL REFERENCES topic_candidates(id),
    provider TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'passed', 'failed')),
    research_date TEXT NOT NULL,
    output_dir TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS research_sources (
    id INTEGER PRIMARY KEY,
    research_run_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    source_key TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    title TEXT NOT NULL,
    publisher TEXT NOT NULL,
    source_type TEXT NOT NULL,
    reliability_tier TEXT NOT NULL,
    accessed_at TEXT NOT NULL,
    published_at TEXT,
    applicable_at TEXT,
    content_sha256 TEXT NOT NULL,
    cache_path TEXT NOT NULL,
    limitations TEXT NOT NULL,
    UNIQUE(research_run_id, source_key)
);

CREATE TABLE IF NOT EXISTS research_claims (
    id INTEGER PRIMARY KEY,
    research_run_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    claim_key TEXT NOT NULL,
    statement TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    importance TEXT NOT NULL,
    volatility TEXT NOT NULL,
    as_of TEXT,
    audit_status TEXT NOT NULL,
    UNIQUE(research_run_id, claim_key)
);

CREATE TABLE IF NOT EXISTS research_claim_sources (
    claim_id INTEGER NOT NULL REFERENCES research_claims(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES research_sources(id) ON DELETE CASCADE,
    locator TEXT NOT NULL,
    evidence_quote TEXT NOT NULL,
    relation TEXT NOT NULL,
    PRIMARY KEY (claim_id, source_id, locator)
);

CREATE TABLE IF NOT EXISTS coordinate_evidence (
    id INTEGER PRIMARY KEY,
    research_run_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES research_sources(id) ON DELETE CASCADE,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    locator TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_research_runs_topic ON research_runs(topic_id);
CREATE INDEX IF NOT EXISTS idx_research_runs_date ON research_runs(research_date DESC);

