CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name_zh TEXT NOT NULL,
    name_en TEXT NOT NULL,
    country_code TEXT NOT NULL,
    country_zh TEXT NOT NULL,
    country_en TEXT NOT NULL,
    region TEXT NOT NULL,
    continent TEXT NOT NULL,
    location_type TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    coordinate_status TEXT NOT NULL DEFAULT 'pending_research',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS location_aliases (
    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_normalized TEXT NOT NULL UNIQUE,
    PRIMARY KEY (location_id, alias_normalized)
);

CREATE TABLE IF NOT EXISTS topic_candidates (
    id INTEGER PRIMARY KEY,
    topic_id TEXT NOT NULL UNIQUE,
    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    hook_zh TEXT NOT NULL,
    angle_zh TEXT NOT NULL,
    visual_value INTEGER NOT NULL CHECK (visual_value BETWEEN 1 AND 5),
    knowledge_value INTEGER NOT NULL CHECK (knowledge_value BETWEEN 1 AND 5),
    story_value INTEGER NOT NULL CHECK (story_value BETWEEN 1 AND 5),
    time_sensitivity TEXT NOT NULL CHECK (time_sensitivity IN ('low', 'medium', 'high')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_tags (
    topic_id INTEGER NOT NULL REFERENCES topic_candidates(id) ON DELETE CASCADE,
    tag_type TEXT NOT NULL CHECK (tag_type IN ('theme', 'visual')),
    tag TEXT NOT NULL,
    PRIMARY KEY (topic_id, tag_type, tag)
);

CREATE TABLE IF NOT EXISTS candidate_sources (
    topic_id INTEGER NOT NULL REFERENCES topic_candidates(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    source_title TEXT NOT NULL,
    publisher TEXT NOT NULL,
    checked_on TEXT NOT NULL,
    purpose TEXT NOT NULL,
    PRIMARY KEY (topic_id, source_url)
);

CREATE TABLE IF NOT EXISTS selections (
    id INTEGER PRIMARY KEY,
    selected_on TEXT NOT NULL UNIQUE,
    topic_id INTEGER NOT NULL REFERENCES topic_candidates(id),
    status TEXT NOT NULL DEFAULT 'selected' CHECK (
        status IN ('selected', 'researched', 'drafted', 'awaiting_review', 'published', 'failed')
    ),
    score REAL NOT NULL,
    duplicate_score REAL NOT NULL,
    score_details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_locations_country ON locations(country_code);
CREATE INDEX IF NOT EXISTS idx_locations_continent ON locations(continent);
CREATE INDEX IF NOT EXISTS idx_locations_type ON locations(location_type);
CREATE INDEX IF NOT EXISTS idx_candidates_location ON topic_candidates(location_id);
CREATE INDEX IF NOT EXISTS idx_selections_date ON selections(selected_on DESC);

