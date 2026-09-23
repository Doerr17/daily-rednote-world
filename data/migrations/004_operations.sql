CREATE TABLE IF NOT EXISTS daily_runs (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    run_date TEXT NOT NULL UNIQUE,
    topic_id INTEGER REFERENCES topic_candidates(id),
    provider TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'selected', 'researched', 'drafted', 'assets_ready', 'rendered',
            'qa_passed', 'awaiting_review', 'published', 'failed'
        )
    ),
    workspace_dir TEXT NOT NULL,
    final_dir TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_checkpoints (
    id INTEGER PRIMARY KEY,
    daily_run_id INTEGER NOT NULL REFERENCES daily_runs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (
        stage IN (
            'selected', 'researched', 'drafted', 'assets_ready', 'rendered',
            'qa_passed', 'awaiting_review'
        )
    ),
    artifact_dir TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    UNIQUE(daily_run_id, stage)
);

CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY,
    daily_run_id INTEGER NOT NULL UNIQUE REFERENCES daily_runs(id),
    published_at TEXT NOT NULL,
    platform_post_url TEXT,
    confirmed_by_human INTEGER NOT NULL CHECK (confirmed_by_human = 1),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY,
    publication_id INTEGER NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
    as_of TEXT NOT NULL,
    views INTEGER NOT NULL CHECK (views >= 0),
    likes INTEGER NOT NULL CHECK (likes >= 0),
    saves INTEGER NOT NULL CHECK (saves >= 0),
    comments INTEGER NOT NULL CHECK (comments >= 0),
    followers_gained INTEGER NOT NULL CHECK (followers_gained >= 0),
    recorded_at TEXT NOT NULL,
    UNIQUE(publication_id, as_of)
);

CREATE INDEX IF NOT EXISTS idx_daily_runs_status ON daily_runs(status, run_date DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_publication ON metrics(publication_id, as_of DESC);
