-- AgentD v0.1 — Initial Schema
-- All table/column names are locked per AGENTD_CONTRACT.md §2

-- Enable pgcrypto for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ─────────────────────────────────────────
-- 2.1 users
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    username      VARCHAR(64) UNIQUE NOT NULL,
    password_hash TEXT        NOT NULL,
    role          VARCHAR(16) NOT NULL DEFAULT 'user',   -- 'user' | 'admin'
    workspace     TEXT        NOT NULL,                  -- /workspaces/{id}/
    is_active     BOOLEAN     NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────
-- 2.2 sessions
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT        NOT NULL DEFAULT 'New Session',
    agent_id    TEXT        NOT NULL DEFAULT 'build',
    model_id    TEXT        NOT NULL,
    parent_id   UUID        REFERENCES sessions(id),    -- sub-session
    status      VARCHAR(16) NOT NULL DEFAULT 'idle',    -- idle|running|error
    token_usage JSONB       NOT NULL DEFAULT '{"input":0,"output":0,"total":0}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id    ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at DESC);

-- ─────────────────────────────────────────
-- 2.3 messages
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS messages (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        VARCHAR(16) NOT NULL,                   -- user | assistant | tool
    parts       JSONB       NOT NULL DEFAULT '[]',
    is_summary  BOOLEAN     NOT NULL DEFAULT false,     -- context compaction marker
    token_usage JSONB,
    seq         INTEGER     NOT NULL,                   -- monotonically increasing
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_seq ON messages(session_id, seq);

-- ─────────────────────────────────────────
-- 2.4 tool_calls (audit log)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tool_calls (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID        NOT NULL REFERENCES sessions(id),
    message_id   UUID        NOT NULL REFERENCES messages(id),
    tool_name    TEXT        NOT NULL,
    input        JSONB       NOT NULL,
    output       JSONB,
    is_error     BOOLEAN     NOT NULL DEFAULT false,
    status       VARCHAR(16) NOT NULL DEFAULT 'pending', -- pending|running|completed|error
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_session_id ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_message_id ON tool_calls(message_id);

-- ─────────────────────────────────────────
-- 2.5 permission_requests
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS permission_requests (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID        NOT NULL REFERENCES sessions(id),
    tool_call_id UUID        NOT NULL REFERENCES tool_calls(id),
    tool_name    TEXT        NOT NULL,
    input        JSONB       NOT NULL,
    status       VARCHAR(16) NOT NULL DEFAULT 'pending', -- pending|approved|denied
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_permission_requests_session_id ON permission_requests(session_id);

-- ─────────────────────────────────────────
-- 2.6 skills
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skills (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(128) UNIQUE NOT NULL,
    description TEXT         NOT NULL,
    content     TEXT         NOT NULL,   -- full SKILL.md text
    tags        TEXT[]       NOT NULL DEFAULT '{}',
    is_active   BOOLEAN      NOT NULL DEFAULT true,
    created_by  UUID         REFERENCES users(id),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name);
