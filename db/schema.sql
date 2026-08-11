-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Organisations (one row per client)
CREATE TABLE IF NOT EXISTS business_profile (
    id SERIAL PRIMARY KEY,
    org_id UUID UNIQUE NOT NULL DEFAULT gen_random_uuid(),
    business_name VARCHAR(255) NOT NULL,
    industry VARCHAR(255),
    core_services TEXT,
    what_they_dont_do TEXT,
    team_size INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Raw indexed sources (historic backfill, no extraction)
CREATE TABLE IF NOT EXISTS sources (
    id SERIAL PRIMARY KEY,
    org_id UUID NOT NULL,
    source_type VARCHAR(50) NOT NULL, -- slack, gmail, calendar
    raw_content TEXT NOT NULL,
    source_metadata JSONB,
    timestamp TIMESTAMP,
    permalink TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Structured verified entities
CREATE TABLE IF NOT EXISTS entities (
    id SERIAL PRIMARY KEY,
    org_id UUID NOT NULL,
    entity_type VARCHAR(50) NOT NULL, -- client, project, decision, person, process, belief
    name VARCHAR(255),
    content TEXT NOT NULL,
    source_id INTEGER REFERENCES sources(id),
    approved_by VARCHAR(255),
    version INTEGER DEFAULT 1,
    superseded_at TIMESTAMP,
    embedding vector(1536),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Session and briefing logs
CREATE TABLE IF NOT EXISTS session_logs (
    id SERIAL PRIMARY KEY,
    org_id UUID NOT NULL,
    log_type VARCHAR(50), -- chat, briefing, agent_action
    content TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);