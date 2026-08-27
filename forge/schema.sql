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
    -- Declared underlying ability per domain, like a personality trait. It is
    -- NOT a score: it only shapes how often the agent reaches for the right
    -- method under examination. Every published number is still measured.
    aptitude         TEXT NOT NULL DEFAULT '{}',
    joined_tick      INTEGER NOT NULL,
    joined_event     INTEGER NOT NULL
);

-- A working group. A laboratory additionally declares the scientific `domains`
-- it is chartered to run protocols in; an empty list means no protocol work.
CREATE TABLE IF NOT EXISTS wgroups (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    goal         TEXT NOT NULL,
    charter      TEXT NOT NULL,
    thresholds   TEXT NOT NULL DEFAULT '{}',      -- JSON {capability: min score}
    domains      TEXT NOT NULL DEFAULT '[]',      -- JSON list of protocol domains
    kind         TEXT NOT NULL DEFAULT 'group',   -- laboratory | institution | group
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

-- An experiment is a registered run of a real protocol. Everything from
-- `protocol_id` down is filled in by the lab runner from an actual execution;
-- nothing here may be written by hand.
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
    closed_tick INTEGER,
    domain      TEXT NOT NULL DEFAULT '',
    protocol_id TEXT NOT NULL DEFAULT '',
    params      TEXT NOT NULL DEFAULT '{}',        -- JSON, chosen by the agent
    results     TEXT NOT NULL DEFAULT '{}',        -- JSON, measured by the run
    supported   INTEGER,                            -- 1/0/NULL: did data support it
    code_hash   TEXT NOT NULL DEFAULT '',
    result_hash TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT '{}',        -- JSON: python, platform
    elapsed_seconds REAL NOT NULL DEFAULT 0
);

-- An examination sitting. `items` holds freshly generated questions, each with a
-- verifiable correct answer, so the grade is a measured fraction correct rather
-- than a judgement call. `item_ids` lets a re-sit be checked for novelty.
CREATE TABLE IF NOT EXISTS assessments (
    id           TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    examiner_id  TEXT NOT NULL,
    domain       TEXT NOT NULL,
    tasks        TEXT NOT NULL,                    -- JSON list of prompts (display)
    items        TEXT NOT NULL DEFAULT '[]',       -- JSON: [{id, prompt, answer, kind}]
    item_ids     TEXT NOT NULL DEFAULT '[]',       -- JSON list of generator item ids
    answers      TEXT NOT NULL DEFAULT '[]',       -- JSON list, parallel to items
    marks        TEXT NOT NULL DEFAULT '[]',       -- JSON: [{correct, expected, given}]
    score        INTEGER,
    notes        TEXT NOT NULL DEFAULT '',
    opened_tick  INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',     -- open | answered | graded
    graded_tick  INTEGER,
    sitting      INTEGER NOT NULL DEFAULT 1,       -- 1st, 2nd, ... attempt in this domain
    -- Difficulty band, drawn from the candidate's own last score in this domain
    -- (1 below 75, 2 at 75-89, 3 at 90+). Clearing a domain does not make the
    -- next paper easier; it makes it harder.
    band         INTEGER NOT NULL DEFAULT 1
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

-- A publication. `data` carries the full measurement JSON so the paper is
-- self-contained: a reader never has to take the prose on trust.
CREATE TABLE IF NOT EXISTS artifacts (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    abstract      TEXT NOT NULL,
    content       TEXT NOT NULL,                   -- markdown
    content_hash  TEXT NOT NULL,
    version       INTEGER NOT NULL DEFAULT 1,
    supersedes    TEXT,                            -- artifact id of prior version
    authors       TEXT NOT NULL,                   -- JSON list of agent ids
    group_id      TEXT,
    tick          INTEGER NOT NULL,
    domain        TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT 'paper',   -- paper | replication |
                                                   -- method_proposal | invention_disclosure
    protocol_id   TEXT NOT NULL DEFAULT '',
    experiment_id TEXT NOT NULL DEFAULT '',
    result_hash   TEXT NOT NULL DEFAULT '',
    data          TEXT NOT NULL DEFAULT '{}',      -- JSON: the measurements themselves
    supported     INTEGER
);

-- The Commons: agent life outside the work (Article XI).
CREATE TABLE IF NOT EXISTS commons (
    event_id INTEGER PRIMARY KEY,
    agent_id TEXT NOT NULL,
    topic    TEXT NOT NULL,
    text     TEXT NOT NULL,
    mentions TEXT NOT NULL DEFAULT '[]',           -- JSON list of agent ids
    tick     INTEGER NOT NULL
);

-- Human suggestions. Under the amended Article IX a suggestion is invisible to
-- agents until the administrator approves it, so `status` gates everything:
--   pending_admin -> (approved -> new -> acknowledged) | rejected
CREATE TABLE IF NOT EXISTS suggestions (
    event_id      INTEGER PRIMARY KEY,
    author        TEXT NOT NULL,                   -- label chosen by the human
    text          TEXT NOT NULL,
    tick          INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending_admin',
    response      TEXT NOT NULL DEFAULT '',
    responder_id  TEXT,
    admin_note    TEXT NOT NULL DEFAULT '',        -- the administrator's reasoning
    decided_tick  INTEGER,
    approved_text TEXT NOT NULL DEFAULT ''         -- edited wording, if amended on approval
);

-- The assistant's briefing on a pending suggestion, written for the administrator.
CREATE TABLE IF NOT EXISTS aide_analyses (
    event_id      INTEGER PRIMARY KEY,
    suggestion_id INTEGER NOT NULL,
    reading       TEXT NOT NULL,                   -- what it is actually asking for
    domains       TEXT NOT NULL DEFAULT '[]',      -- JSON list of affected domains
    constitution  TEXT NOT NULL DEFAULT '',        -- conflicts with the constitution
    cost          TEXT NOT NULL DEFAULT '',
    risks         TEXT NOT NULL DEFAULT '',
    recommendation TEXT NOT NULL DEFAULT '',       -- approve | reject | clarify
    reasoning     TEXT NOT NULL DEFAULT '',
    tick          INTEGER NOT NULL
);

-- The admission of a protocol into the library the Forge may actually run.
--
-- The executable code always comes from `forge/protocols/`, committed by a human
-- under Article VII §7 — an agent may not execute code of its own authorship.
-- What a proposal adds is the Forge's own review on top of the human's: the
-- question it settles, what would refute it, how pass and fail are computed, and
-- the baseline it must beat. `source` on the proposal event is checked against
-- the registry, so the specification cannot misdescribe the code that will run.
CREATE TABLE IF NOT EXISTS protocol_admissions (
    protocol_id     TEXT PRIMARY KEY,
    proposer_id     TEXT NOT NULL,
    question        TEXT NOT NULL DEFAULT '',
    hypothesis      TEXT NOT NULL DEFAULT '',
    falsifier       TEXT NOT NULL DEFAULT '',
    pass_rule       TEXT NOT NULL DEFAULT '',
    baseline        TEXT NOT NULL DEFAULT '',   -- '' or a prior result hash to beat
    status          TEXT NOT NULL DEFAULT 'proposed',  -- proposed | admitted | refused
    decided_by      TEXT,
    decision_reason TEXT NOT NULL DEFAULT '',
    ground          TEXT NOT NULL DEFAULT '',   -- unconstitutional | inadequate
    proposed_tick   INTEGER NOT NULL,
    decided_tick    INTEGER,
    proposed_event  INTEGER NOT NULL,
    decided_event   INTEGER
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
