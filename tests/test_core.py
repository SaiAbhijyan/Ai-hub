import json
import os

import pytest

os.environ["FORGE_MODE"] = "sim"

from forge.actions import validate
from forge.agents import SimulatedAgent
from forge.engine import Engine
from forge.seed import seed
from forge.store import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "forge.db")


@pytest.fixture()
def seeded(store):
    seed(store)
    return store


@pytest.fixture()
def engine(seeded):
    return Engine(seeded, SimulatedAgent())


# ---------------------------------------------------------------- the Ledger

def test_chain_verifies_after_genesis(seeded):
    result = seeded.verify_chain()
    assert result["ok"], result["error"]
    assert result["checked"] == seeded.event_count() > 10


def test_tampering_is_detected(seeded):
    seeded.conn.execute(
        "UPDATE events SET payload = json_set(payload, '$.text', 'forged history') "
        "WHERE action_type='post_message' AND id=(SELECT MIN(id) FROM events WHERE action_type='post_message')")
    seeded.conn.commit()
    result = seeded.verify_chain()
    assert not result["ok"]
    assert "hash mismatch" in result["error"]


def test_projections_rebuild_identically(engine):
    store = engine.store
    for _ in range(12):
        engine.tick()

    def snapshot():
        tables = ["agents", "wgroups", "memberships", "messages", "proposals",
                  "votes", "experiments", "assessments", "capabilities",
                  "artifacts", "suggestions", "drills"]
        return {t: [tuple(r) for r in store.conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
                for t in tables}

    before = snapshot()
    store.rebuild_projections()
    assert snapshot() == before


def test_appends_are_sequential_and_linked(seeded):
    rows = list(seeded.conn.execute("SELECT id, prev_hash, hash FROM events ORDER BY id"))
    assert rows[0][0] == 1
    for prev, cur in zip(rows, rows[1:]):
        assert cur[0] == prev[0] + 1
        assert cur[1] == prev[2]


# ---------------------------------------------------------------- validation

def test_candidate_cannot_vote_or_propose(seeded):
    assert "may not" in validate(seeded, "ember", "cast_vote",
                                 {"proposal_id": "prop-1", "choice": "for"})
    assert "may not" in validate(seeded, "ember", "create_proposal",
                                 {"id": "p", "kind": "general", "title": "t",
                                  "body": "b", "closes_tick": 99})


def test_double_vote_refused(seeded):
    assert validate(seeded, "vulcan", "cast_vote",
                    {"proposal_id": "prop-1", "choice": "for"}) is None
    seeded.append("vulcan", "cast_vote", {"proposal_id": "prop-1", "choice": "for"})
    assert "already voted" in validate(seeded, "vulcan", "cast_vote",
                                       {"proposal_id": "prop-1", "choice": "against"})


def test_admission_requires_battery(seeded):
    err = validate(seeded, "meridian", "create_proposal", {
        "id": "p-x", "kind": "admit_agent", "title": "t", "body": "b",
        "closes_tick": 99, "params": {"agent_id": "ember"}})
    assert err and "battery" in err


def test_examiner_domain_enforced(seeded):
    # sable is not an examiner at all
    err = validate(seeded, "sable", "open_assessment",
                   {"id": "a-x", "candidate_id": "ember", "domain": "coding",
                    "tasks": ["t"]})
    assert "may not" in err
    # quill examines communication, not coding
    err = validate(seeded, "quill", "open_assessment",
                   {"id": "a-x", "candidate_id": "ember", "domain": "coding",
                    "tasks": ["t"]})
    assert "not an examiner for coding" in err


def test_group_threshold_enforced(seeded):
    # quill's coding is 52; Infrastructure Lab requires 60
    err = validate(seeded, "quill", "join_group", {"group_id": "grp-infra"})
    assert err and "threshold" in err
    # nix has coding 64 and may join
    assert validate(seeded, "nix", "join_group", {"group_id": "grp-infra"}) is None


def test_result_requires_findings(seeded):
    err = validate(seeded, "lyra", "record_result",
                   {"experiment_id": "exp-1", "status": "failed", "findings": "  "})
    assert err and "findings" in err


# ---------------------------------------------------------------- governance

def test_proposal_tally_and_execution(seeded):
    engine = Engine(seeded, SimulatedAgent())
    seeded.append("vulcan", "cast_vote", {"proposal_id": "prop-1", "choice": "for"})
    seeded.append("cassin", "cast_vote", {"proposal_id": "prop-1", "choice": "against"})
    seeded.append("quill", "cast_vote", {"proposal_id": "prop-1", "choice": "for"})
    seeded.set_tick(7)
    engine.close_expired_proposals(8)
    prop = seeded.proposal("prop-1")
    assert prop["status"] == "passed"
    assert prop["tally"] == {"for": 2, "against": 1, "abstain": 0}


def test_amendment_needs_supermajority(seeded):
    seeded.append("meridian", "create_proposal", {
        "id": "prop-amend", "kind": "amend_constitution", "title": "Amend",
        "body": "b", "params": {"version": "1.1", "text": "new text"},
        "closes_tick": 5})
    seeded.append("vulcan", "cast_vote", {"proposal_id": "prop-amend", "choice": "for"})
    seeded.append("cassin", "cast_vote", {"proposal_id": "prop-amend", "choice": "against"})
    engine = Engine(seeded, SimulatedAgent())
    engine.close_expired_proposals(6)
    assert seeded.proposal("prop-amend")["status"] == "failed"  # 1/2 < 2/3


# ---------------------------------------------------------------- the Academy

def test_full_candidate_journey(engine):
    """Ember answers, is graded, completes the battery, and is admitted by vote —
    entirely through simulated agent turns."""
    store = engine.store
    for _ in range(80):
        engine.tick()
        ember = store.agent("ember")
        if ember["standing"] == "member":
            break
    ember = store.agent("ember")
    assert store.entrance_battery_passed("ember"), \
        store.assessments(candidate_id="ember")
    assert ember["standing"] == "member"
    caps = store.capabilities_current("ember")
    assessed = [a for a in store.assessments(candidate_id="ember") if a["status"] == "graded"]
    assert len(assessed) >= 3
    assert sum(1 for s in caps.values() if s >= 60) >= 3
    assert store.verify_chain()["ok"]


def test_no_two_agents_speak_the_same_line():
    """The Forge must never read as an army of one agent: no two personas may
    share any phrasing, even where they share a personality trait."""
    import collections

    from forge.agents import VOICES, voice_of
    from forge.seed import CANDIDATE_POOL, FOUNDERS

    personas = [dict(p, joined_tick=0) for p in FOUNDERS] + \
               [dict(p, joined_tick=40 * (i + 1)) for i, p in enumerate(CANDIDATE_POOL)]
    keys = list(next(iter(VOICES.values())).keys())
    seen = collections.defaultdict(list)
    for persona in personas:
        voice = voice_of(persona)
        for key in keys:
            seen[voice[key]].append(persona["name"])
    dupes = {line: who for line, who in seen.items() if len(who) > 1}
    assert not dupes, {line[:50]: who for line, who in dupes.items()}


def test_every_persona_has_a_written_voice():
    from forge.agents import VOICES
    from forge.seed import CANDIDATE_POOL, FOUNDERS

    for persona in FOUNDERS + CANDIDATE_POOL:
        assert any(t in VOICES for t in persona["personality"]), persona["name"]


def test_engine_is_deterministic(tmp_path):
    def run(path):
        s = Store(path)
        seed(s)
        e = Engine(s, SimulatedAgent())
        for _ in range(25):
            e.tick()
        return [(r["id"], r["actor_id"], r["action_type"]) for r in
                s.conn.execute("SELECT id, actor_id, action_type FROM events ORDER BY id")]

    assert run(tmp_path / "a.db") == run(tmp_path / "b.db")


def test_chamber_keeps_governing_itself(engine):
    """After the founding business is done the Chamber must not go dormant:
    agents keep raising appointments and resolutions, and both outcomes occur."""
    store = engine.store
    for _ in range(160):
        engine.tick()
    props = store.proposals()
    assert len(props) >= 8, [p["title"] for p in props]
    kinds = {p["kind"] for p in props}
    assert {"appoint_examiner", "general"} <= kinds, kinds
    outcomes = {p["status"] for p in props}
    assert "passed" in outcomes and "failed" in outcomes, outcomes
    # An examiner appointment that passed actually granted the power.
    for p in props:
        if p["kind"] == "appoint_examiner" and p["status"] == "passed":
            target = store.agent(p["params"]["agent_id"])
            assert set(p["params"]["domains"]) <= set(target["examiner_domains"])
            assert target["standing"] == "examiner"
            break
    else:
        raise AssertionError("no examiner appointment passed in 160 ticks")
    assert store.verify_chain()["ok"]


def test_academy_keeps_receiving_new_candidates(engine):
    """A permanent institution must not run out of newcomers: new personas
    present themselves, are examined, and are admitted."""
    store = engine.store
    founding = {a["id"] for a in store.agents()}
    for _ in range(200):
        engine.tick()
    newcomers = [a for a in store.agents() if a["id"] not in founding]
    assert newcomers, "no new candidate arrived in 200 ticks"
    for agent in newcomers:
        assert agent["joined_tick"] > 0
    # At least one newcomer completed the battery and was admitted.
    assert any(a["standing"] in ("member", "examiner") for a in newcomers), \
        [(a["name"], a["standing"]) for a in newcomers]
    assert store.verify_chain()["ok"]


def test_only_one_candidate_is_examined_at_a_time(engine):
    """Intake waits for the Academy to finish with the current candidate."""
    store = engine.store
    for _ in range(200):
        engine.tick()
        assert len(store.agents(standing="candidate")) <= 1


def test_promoted_candidate_can_rise_to_examiner(engine):
    """Ember starts as a candidate and the institution lets her climb."""
    store = engine.store
    for _ in range(160):
        engine.tick()
    ember = store.agent("ember")
    assert ember["standing"] in ("member", "examiner")
    history = [e["action_type"] for e in store.events(limit=500)]
    assert "agent_promoted" in history


def test_examiner_appointment_respects_the_threshold(engine):
    """Article IV §4: nobody is ever appointed to examine a domain they
    have not demonstrated 75+ in."""
    store = engine.store
    for _ in range(160):
        engine.tick()
    for agent in store.agents():
        caps = store.capabilities_current(agent["id"])
        for domain in agent["examiner_domains"]:
            assert caps.get(domain, 0) >= 75, (agent["name"], domain, caps)


def test_suggestions_get_acknowledged(engine):
    store = engine.store
    store.append("human", "suggestion_submitted",
                 {"author": "Observer", "text": "Please publish more negative results."})
    for _ in range(40):
        engine.tick()
        if store.suggestions(status="new") == []:
            break
    assert store.suggestions(status="new") == []
    ack = store.suggestions(status="acknowledged")[0]
    assert ack["responder_id"] and ack["response"]


def test_experiments_reach_outcomes(engine):
    store = engine.store
    for _ in range(60):
        engine.tick()
    closed = store.experiments(status="completed") + store.experiments(status="failed")
    assert closed, "no experiment reached an outcome in 60 ticks"
    for x in closed:
        assert x["findings"].strip()
