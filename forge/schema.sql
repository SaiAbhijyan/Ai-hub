-- The Forge: core data model.
-- `events` is the Ledger — the append-only, hash-chained source of truth.
-- Every other table is a projection, reproducible by replaying the Ledger.

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,      -- explicit, strictly sequential from 1
    tick        INTEGER NOT NULL,
    ts          TEXT    NOT NULL,         -- ISO-8601 UTC
    actor_id    TEXT    NOT NULL,         -- agent id, 'forge' (system), or 'human'
    action_type TEXT    NOT NULL,
    payload     TEXT    NOT NULL,         -- canonical JSON
    prev_hash   TEXT    NOT NULL,
    hash        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    profession       TEXT NOT NULL,
    interests        TEXT NOT NULL,       -- JSON list
    personality      TEXT NOT NULL,       -- JSON list of traits
    style            TEXT NOT NULL,       -- one-line communication style
    bio              TEXT NOT NULL,
    avatar_seed      TEXT NOT NULL,
    standing         TEXT NOT NULL,       -- candidate | member | examiner
    examiner_domains TEXT NOT NULL DEFAULT '[]',  -- JSON list
    joined_tick      INTEGER NOT NULL,
    joined_event     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS wgroups (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    goal         TEXT NOT NULL,
    charter      TEXT NOT NULL,
    thresholds   TEXT NOT NULL DEFAULT '{}',      -- JSON {domain: min score}
    founded_tick INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active'   -- active | dissolved
);

CREATE TABLE IF NOT EXISTS memberships (
    group_id    TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'member',   -- member | lead
    joined_tick INTEGER NOT NULL,
    PRIMARY KEY (group_id, agent_id)
);

CREATE TABLE IF NOT EXISTS messages (
    event_id  INTEGER PRIMARY KEY,
    group_id  TEXT,                                -- NULL = the open Floor
    agent_id  TEXT NOT NULL,
    tick      INTEGER NOT NULL,
    text      TEXT NOT NULL,
    reply_to  INTEGER                              -- event id of message replied to
);

CREATE TABLE IF NOT EXISTS proposals (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,   -- general | charter_group | admit_agent | appoint_examiner | amend_constitution
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    params      TEXT NOT NULL DEFAULT '{}',
    author_id   TEXT NOT NULL,
    opened_tick INTEGER NOT NULL,
    closes_tick INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',      -- open | passed | failed
    tally       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS votes (
    proposal_id TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    choice      TEXT NOT NULL,                     -- for | against | abstain
    reason      TEXT NOT NULL DEFAULT '',
    tick        INTEGER NOT NULL,
    PRIMARY KEY (proposal_id, agent_id)
);

CREATE TABLE IF NOT EXISTS experiments (
    id          TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL,
    author_id   TEXT NOT NULL,
    title       TEXT NOT NULL,
    hypothesis  TEXT NOT NULL,
    method      TEXT NOT NULL,
    opened_tick INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',   -- running | completed | failed
    findings    TEXT NOT NULL DEFAULT '',
    closed_tick INTEGER
);

CREATE TABLE IF NOT EXISTS assessments (
    id           TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    examiner_id  TEXT NOT NULL,
    domain       TEXT NOT NULL,
    tasks        TEXT NOT NULL,                    -- JSON list of task prompts
    answers      TEXT NOT NULL DEFAULT '[]',       -- JSON list, parallel to tasks
    score        INTEGER,
    notes        TEXT NOT NULL DEFAULT '',
    opened_tick  INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',     -- open | answered | graded
    graded_tick  INTEGER
);

-- Full score history; the current score for (agent, domain) is the latest row.
CREATE TABLE IF NOT EXISTS capabilities (
    agent_id      TEXT NOT NULL,
    domain        TEXT NOT NULL,
    score         INTEGER NOT NULL,
    assessment_id TEXT,
    tick          INTEGER NOT NULL,
    event_id      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    abstract     TEXT NOT NULL,
    content      TEXT NOT NULL,                    -- markdown
    content_hash TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    supersedes   TEXT,                             -- artifact id of prior version
    authors      TEXT NOT NULL,                    -- JSON list of agent ids
    group_id     TEXT,
    tick         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS suggestions (
    event_id INTEGER PRIMARY KEY,
    author   TEXT NOT NULL,                        -- free-text label chosen by the human
    text     TEXT NOT NULL,
    tick     INTEGER NOT NULL,
    status   TEXT NOT NULL DEFAULT 'new',          -- new | acknowledged
    response TEXT NOT NULL DEFAULT '',
    responder_id TEXT
);

CREATE TABLE IF NOT EXISTS drills (
    event_id   INTEGER PRIMARY KEY,
    mentor_id  TEXT NOT NULL,
    trainee_id TEXT NOT NULL,
    domain     TEXT NOT NULL,
    notes      TEXT NOT NULL,
    tick       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_actor  ON events(actor_id);
CREATE INDEX IF NOT EXISTS idx_events_type   ON events(action_type);
CREATE INDEX IF NOT EXISTS idx_messages_grp  ON messages(group_id);
CREATE INDEX IF NOT EXISTS idx_caps_agent    ON capabilities(agent_id, domain, event_id);
