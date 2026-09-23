CREATE TABLE IF NOT EXISTS content_runs (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    research_run_id INTEGER REFERENCES research_runs(id),
    topic_id INTEGER NOT NULL REFERENCES topic_candidates(id),
    provider TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed')),
    content_date TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_fingerprints (
    id INTEGER PRIMARY KEY,
    content_run_id INTEGER NOT NULL REFERENCES content_runs(id) ON DELETE CASCADE,
    fingerprint_type TEXT NOT NULL CHECK (
        fingerprint_type IN ('title', 'caption', 'cards', 'structure')
    ),
    text_value TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    UNIQUE(content_run_id, fingerprint_type)
);

CREATE INDEX IF NOT EXISTS idx_content_runs_topic ON content_runs(topic_id);
CREATE INDEX IF NOT EXISTS idx_content_runs_date ON content_runs(content_date DESC);

