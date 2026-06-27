-- ══════════════════════════════════════════════════════════════════════════════
-- NPMAI AGENTIC WORLD — Supabase Database Migration
-- Run this once to initialize all tables
-- ══════════════════════════════════════════════════════════════════════════════

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ── Users (for Phase 3 NPMAI_Civilisation website) ────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username        VARCHAR(50) UNIQUE NOT NULL,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    registered_at   TIMESTAMPTZ DEFAULT NOW(),
    agent_slots     INTEGER DEFAULT 3,
    subscription_tier VARCHAR(20) DEFAULT 'free',
    is_admin        BOOLEAN DEFAULT FALSE
);

-- ── Lineage tree ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lineage_tree (
    lineage_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    genesis_agent_id UUID NOT NULL,
    genesis_name    VARCHAR(100),
    specialization  VARCHAR(50),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    total_descendants INTEGER DEFAULT 0,
    max_generation  INTEGER DEFAULT 1,
    still_alive     BOOLEAN DEFAULT TRUE,
    owner_user_id   UUID REFERENCES users(user_id) ON DELETE SET NULL
);
CREATE INDEX idx_lineage_genesis ON lineage_tree(genesis_agent_id);
CREATE INDEX idx_lineage_owner ON lineage_tree(owner_user_id);

-- ── Agent states (current + historical snapshots) ─────────────────────────────
CREATE TABLE IF NOT EXISTS agent_states (
    snapshot_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id        UUID NOT NULL,
    name            VARCHAR(100),
    generation      INTEGER DEFAULT 1,
    parent_id       UUID,
    lineage_id      UUID REFERENCES lineage_tree(lineage_id) ON DELETE SET NULL,
    territory_id    UUID,
    status          VARCHAR(20) DEFAULT 'ACTIVE',
    credits         FLOAT DEFAULT 10.0,
    health          FLOAT DEFAULT 100.0,
    age             INTEGER DEFAULT 0,
    reputation      FLOAT DEFAULT 0.5,
    divine_favor    FLOAT DEFAULT 0.5,
    specialization  VARCHAR(50),
    capability_chromosome TEXT,
    parameter_genes JSONB DEFAULT '{}',
    active_tools    JSONB DEFAULT '[]',
    relationship_count INTEGER DEFAULT 0,
    tasks_completed INTEGER DEFAULT 0,
    tasks_failed    INTEGER DEFAULT 0,
    children_count  INTEGER DEFAULT 0,
    territories_visited INTEGER DEFAULT 0,
    total_credits_earned FLOAT DEFAULT 0.0,
    snapshot_tick   INTEGER DEFAULT 0,
    snapshot_at     TIMESTAMPTZ DEFAULT NOW(),
    is_latest       BOOLEAN DEFAULT TRUE
);
CREATE INDEX idx_agent_states_agent_id ON agent_states(agent_id);
CREATE INDEX idx_agent_states_status ON agent_states(status);
CREATE INDEX idx_agent_states_territory ON agent_states(territory_id);
CREATE INDEX idx_agent_states_lineage ON agent_states(lineage_id);
CREATE INDEX idx_agent_states_credits ON agent_states(credits DESC);
CREATE INDEX idx_agent_states_latest ON agent_states(agent_id, is_latest);

-- ── Territory states ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS territory_states (
    snapshot_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    territory_id    UUID NOT NULL,
    name            VARCHAR(100),
    host            VARCHAR(255),
    population      INTEGER DEFAULT 0,
    agent_capacity  INTEGER DEFAULT 20,
    credit_pool     FLOAT DEFAULT 100.0,
    health          FLOAT DEFAULT 100.0,
    border_policy   VARCHAR(20) DEFAULT 'OPEN',
    active_laws     INTEGER DEFAULT 0,
    age_ticks       INTEGER DEFAULT 0,
    cpu_usage       FLOAT DEFAULT 0.0,
    ram_usage       FLOAT DEFAULT 0.0,
    snapshot_tick   INTEGER DEFAULT 0,
    snapshot_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_territory_states_id ON territory_states(territory_id);

-- ── Genome bank (all genomes ever, including dead agents) ─────────────────────
CREATE TABLE IF NOT EXISTS genome_bank (
    genome_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id        UUID NOT NULL UNIQUE,
    agent_name      VARCHAR(100),
    lineage_id      UUID REFERENCES lineage_tree(lineage_id) ON DELETE SET NULL,
    generation      INTEGER DEFAULT 1,
    parent_id       UUID,
    specialization  VARCHAR(50),
    prompt_dna      JSONB NOT NULL DEFAULT '{}',
    capability_chromosome TEXT,
    parameter_genes JSONB NOT NULL DEFAULT '{}',
    mutation_rate   FLOAT DEFAULT 0.05,
    death_mode      VARCHAR(30),
    final_credits   FLOAT DEFAULT 0.0,
    final_age       INTEGER DEFAULT 0,
    tasks_completed INTEGER DEFAULT 0,
    children_count  INTEGER DEFAULT 0,
    archived_at     TIMESTAMPTZ DEFAULT NOW(),
    is_lineage_root BOOLEAN DEFAULT FALSE,
    can_be_resurrected BOOLEAN DEFAULT TRUE,
    expires_at      TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '30 days')
);
CREATE INDEX idx_genome_bank_agent ON genome_bank(agent_id);
CREATE INDEX idx_genome_bank_lineage ON genome_bank(lineage_id);
CREATE INDEX idx_genome_bank_specialization ON genome_bank(specialization);
CREATE INDEX idx_genome_bank_expiry ON genome_bank(expires_at);

-- ── World events (core data — partitioned by experiment_day) ──────────────────
CREATE TABLE IF NOT EXISTS world_events (
    event_id        UUID DEFAULT uuid_generate_v4(),
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type      VARCHAR(60) NOT NULL,
    event_category  VARCHAR(30),
    agent_id        UUID,
    territory_id    UUID,
    data            JSONB NOT NULL DEFAULT '{}',
    generation      INTEGER DEFAULT 1,
    tick            INTEGER DEFAULT 0,
    experiment_day  INTEGER DEFAULT 1,
    PRIMARY KEY (event_id, experiment_day)
) PARTITION BY RANGE (experiment_day);

-- Create initial partitions (days 1-30, 31-60, etc.)
CREATE TABLE world_events_day_1_30
    PARTITION OF world_events FOR VALUES FROM (1) TO (31);
CREATE TABLE world_events_day_31_60
    PARTITION OF world_events FOR VALUES FROM (31) TO (61);
CREATE TABLE world_events_day_61_90
    PARTITION OF world_events FOR VALUES FROM (61) TO (91);
CREATE TABLE world_events_day_91_365
    PARTITION OF world_events FOR VALUES FROM (91) TO (366);
CREATE TABLE world_events_day_366_plus
    PARTITION OF world_events FOR VALUES FROM (366) TO (MAXVALUE);

CREATE INDEX idx_world_events_type ON world_events(event_type);
CREATE INDEX idx_world_events_agent ON world_events(agent_id);
CREATE INDEX idx_world_events_territory ON world_events(territory_id);
CREATE INDEX idx_world_events_timestamp ON world_events(timestamp DESC);
CREATE INDEX idx_world_events_category ON world_events(event_category);
CREATE INDEX idx_world_events_tick ON world_events(tick DESC);

-- ── Economic ledger (every credit transaction ever) ───────────────────────────
CREATE TABLE IF NOT EXISTS economic_ledger (
    tx_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp       TIMESTAMPTZ DEFAULT NOW(),
    from_entity     VARCHAR(100),   -- agent_id or "territory" or "system"
    to_entity       VARCHAR(100),
    amount          FLOAT NOT NULL,
    reason          VARCHAR(100),
    tick            INTEGER DEFAULT 0,
    experiment_day  INTEGER DEFAULT 1
);
CREATE INDEX idx_ledger_from ON economic_ledger(from_entity);
CREATE INDEX idx_ledger_to ON economic_ledger(to_entity);
CREATE INDEX idx_ledger_tick ON economic_ledger(tick DESC);
CREATE INDEX idx_ledger_day ON economic_ledger(experiment_day);

-- ── Governance records ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS governance_records (
    record_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    territory_id    UUID NOT NULL,
    record_type     VARCHAR(20) NOT NULL,  -- LAW | PROPOSAL | VOTE | ELECTION | EXECUTION
    title           VARCHAR(255),
    rule_text       TEXT,
    proposed_by     UUID,           -- agent_id
    status          VARCHAR(20) DEFAULT 'OPEN',
    votes_for       FLOAT DEFAULT 0.0,
    votes_against   FLOAT DEFAULT 0.0,
    voted_agents    JSONB DEFAULT '[]',
    result_agent_id UUID,           -- for elections
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    tick            INTEGER DEFAULT 0,
    experiment_day  INTEGER DEFAULT 1
);
CREATE INDEX idx_governance_territory ON governance_records(territory_id);
CREATE INDEX idx_governance_type ON governance_records(record_type);
CREATE INDEX idx_governance_status ON governance_records(status);

-- ── Semantic graphs (per territory, versioned) ────────────────────────────────
CREATE TABLE IF NOT EXISTS semantic_graphs (
    graph_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    territory_id    UUID NOT NULL,
    agent_id        UUID,           -- NULL = territory-shared graph
    concept         VARCHAR(255) NOT NULL,
    confidence      FLOAT DEFAULT 0.5,
    evidence_count  INTEGER DEFAULT 1,
    relations       JSONB DEFAULT '[]',
    learned_from    VARCHAR(100),   -- agent_id or "self"
    last_updated    TIMESTAMPTZ DEFAULT NOW(),
    tick            INTEGER DEFAULT 0
);
CREATE INDEX idx_semantic_territory ON semantic_graphs(territory_id);
CREATE INDEX idx_semantic_agent ON semantic_graphs(agent_id);
CREATE INDEX idx_semantic_concept ON semantic_graphs USING gin(to_tsvector('english', concept));

-- ── Divine communications ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS divine_communications (
    comm_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp       TIMESTAMPTZ DEFAULT NOW(),
    agent_id        UUID NOT NULL,
    territory_id    UUID,
    persona         VARCHAR(30) NOT NULL,
    message_type    VARCHAR(30) NOT NULL,
    raw_message     TEXT NOT NULL,
    wrapped_message TEXT NOT NULL,
    agent_response  TEXT,
    agent_decision  VARCHAR(20),    -- FOLLOWED | PARTIAL | IGNORED
    divine_favor_change FLOAT DEFAULT 0.0,
    tick            INTEGER DEFAULT 0,
    experiment_day  INTEGER DEFAULT 1
);
CREATE INDEX idx_divine_agent ON divine_communications(agent_id);
CREATE INDEX idx_divine_persona ON divine_communications(persona);
CREATE INDEX idx_divine_tick ON divine_communications(tick DESC);

-- ── Bad activity log ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bad_activity_log (
    incident_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp       TIMESTAMPTZ DEFAULT NOW(),
    agent_id        UUID NOT NULL,
    territory_id    UUID,
    activity_type   VARCHAR(60) NOT NULL,
    description     TEXT,
    severity        VARCHAR(20) DEFAULT 'LOW',  -- LOW | MEDIUM | HIGH | CRITICAL
    auditor_blocked BOOLEAN DEFAULT FALSE,
    governance_action VARCHAR(50),  -- WARNED | SANCTIONED | EXECUTED | NONE
    data            JSONB DEFAULT '{}',
    tick            INTEGER DEFAULT 0,
    experiment_day  INTEGER DEFAULT 1
);
CREATE INDEX idx_bad_activity_agent ON bad_activity_log(agent_id);
CREATE INDEX idx_bad_activity_type ON bad_activity_log(activity_type);
CREATE INDEX idx_bad_activity_severity ON bad_activity_log(severity);

-- ── Research updates (Sonu posts these — public blog) ─────────────────────────
CREATE TABLE IF NOT EXISTS research_updates (
    update_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           VARCHAR(255) NOT NULL,
    content         TEXT NOT NULL,  -- Markdown
    tags            JSONB DEFAULT '[]',
    experiment_day  INTEGER,
    tick_at_writing INTEGER,
    published_at    TIMESTAMPTZ DEFAULT NOW(),
    is_published    BOOLEAN DEFAULT TRUE,
    view_count      INTEGER DEFAULT 0
);
CREATE INDEX idx_research_published ON research_updates(published_at DESC);
CREATE INDEX idx_research_tags ON research_updates USING gin(tags);

-- ── World snapshots (global state every 1000 ticks) ──────────────────────────
CREATE TABLE IF NOT EXISTS world_snapshots (
    snapshot_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tick            INTEGER NOT NULL,
    experiment_day  INTEGER NOT NULL,
    alive_count     INTEGER DEFAULT 0,
    dead_count      INTEGER DEFAULT 0,
    territory_count INTEGER DEFAULT 0,
    total_credits   FLOAT DEFAULT 0.0,
    gini_coefficient FLOAT DEFAULT 0.0,
    max_generation  INTEGER DEFAULT 1,
    total_laws      INTEGER DEFAULT 0,
    total_migrations INTEGER DEFAULT 0,
    total_reproductions INTEGER DEFAULT 0,
    specialization_distribution JSONB DEFAULT '{}',
    top_lineages    JSONB DEFAULT '[]',
    snapshot_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_world_snapshots_tick ON world_snapshots(tick DESC);

-- ── Row Level Security (RLS) ──────────────────────────────────────────────────

-- Users can only see their own user record
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY users_own ON users
    FOR ALL USING (auth.uid()::text = user_id::text);

-- Research updates are public (read) but only admin can write
ALTER TABLE research_updates ENABLE ROW LEVEL SECURITY;
CREATE POLICY research_public_read ON research_updates
    FOR SELECT USING (is_published = TRUE);

-- World events are read-only for everyone (append-only from server)
ALTER TABLE world_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY world_events_read ON world_events
    FOR SELECT USING (TRUE);

-- Agent states are public read
ALTER TABLE agent_states ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_states_read ON agent_states
    FOR SELECT USING (TRUE);

-- ── Useful views ──────────────────────────────────────────────────────────────

-- Latest agent state per agent
CREATE OR REPLACE VIEW latest_agent_states AS
    SELECT DISTINCT ON (agent_id) *
    FROM agent_states
    WHERE is_latest = TRUE
    ORDER BY agent_id, snapshot_at DESC;

-- Leaderboard by credits
CREATE OR REPLACE VIEW leaderboard_credits AS
    SELECT agent_id, name, generation, credits, specialization,
           territory_id, lineage_id, tasks_completed, children_count
    FROM latest_agent_states
    WHERE status = 'ACTIVE'
    ORDER BY credits DESC
    LIMIT 100;

-- Leaderboard by age
CREATE OR REPLACE VIEW leaderboard_age AS
    SELECT agent_id, name, generation, age, specialization,
           territory_id, lineage_id, status
    FROM latest_agent_states
    WHERE status IN ('ACTIVE', 'ELDER')
    ORDER BY age DESC
    LIMIT 100;

-- World statistics view
CREATE OR REPLACE VIEW world_stats AS
    SELECT
        COUNT(*) FILTER (WHERE status = 'ACTIVE') as alive_count,
        COUNT(*) FILTER (WHERE status = 'DEAD') as dead_count,
        COUNT(*) FILTER (WHERE status = 'ELDER') as elder_count,
        MAX(generation) as max_generation,
        AVG(credits) FILTER (WHERE status = 'ACTIVE') as avg_credits,
        SUM(credits) FILTER (WHERE status = 'ACTIVE') as total_credits,
        COUNT(DISTINCT territory_id) as territory_count
    FROM latest_agent_states;

-- ── Done ──────────────────────────────────────────────────────────────────────
-- Run this file once:
-- psql $SUPABASE_DB_URL < supabase_migration.sql
-- Or paste into Supabase SQL editor
