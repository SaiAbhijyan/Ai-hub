import io
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
    """Who may examine is read off the Ledger, not assumed.

    Since the Founding Convocation, no agent's examinerships are written into
    the seed — they are earned on a marked paper — so this test asks the store
    who actually holds what and then checks the rule against real standing.
    """
    target = next(a["id"] for a in seeded.agents(standing="candidate"))

    # An agent that holds no examinership at all is refused on standing.
    not_examiner = next(a["id"] for a in seeded.agents()
                        if a["standing"] not in ("examiner", "member"))
    err = validate(seeded, not_examiner, "open_assessment",
                   {"id": "a-x", "candidate_id": target, "domain": "coding",
                    "tasks": ["t"]})
    assert "may not" in err, err

    # An examiner may not stray outside the domains it was appointed in.
    wrong = next((a for a in seeded.agents(standing="examiner")
                  if "coding" not in a["examiner_domains"]), None)
    assert wrong is not None, "every examiner holds coding — the seating is too broad"
    err = validate(seeded, wrong["id"], "open_assessment",
                   {"id": "a-x", "candidate_id": target, "domain": "coding",
                    "tasks": ["t"]})
    assert "not an examiner for coding" in err, err


def test_group_threshold_enforced(seeded):
    """A charter threshold binds on the measured score, whatever it turns out
    to be. The scores are earned at genesis now, so the test finds one agent
    below the bar and one above rather than naming numbers that will drift."""
    lab = seeded.group("lab-forge")
    domain, minimum = next(iter(lab["thresholds"].items()))
    members = {m["id"] for m in seeded.group_members("lab-forge")}
    below = above = None
    for a in seeded.agents():
        if a["id"] in members or a["standing"] not in ("member", "examiner"):
            continue
        score = seeded.capabilities_current(a["id"]).get(domain)
        if score is None:
            continue
        if score < minimum and below is None:
            below = a["id"]
        if score >= minimum and above is None:
            above = a["id"]
    assert below, f"no member scores under {minimum} in {domain}"
    err = validate(seeded, below, "join_group", {"group_id": "lab-forge"})
    assert err and "threshold" in err, err
    if above:
        assert validate(seeded, above, "join_group", {"group_id": "lab-forge"}) is None


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
    agents keep raising appointments and resolutions, and both outcomes occur.

    260 ticks, not 160: the Founding Convocation now occupies genesis, so the
    Chamber's own business starts later than it did before founders were
    examined. A defeat first appears around tick 135-200 depending on the run.
    """
    store = engine.store
    for _ in range(260):
        engine.tick()
    props = store.proposals()
    assert len(props) >= 8, [p["title"] for p in props]
    kinds = {p["kind"] for p in props}
    assert {"appoint_examiner", "general"} <= kinds, kinds
    outcomes = {p["status"] for p in props}
    assert "passed" in outcomes and "failed" in outcomes, outcomes
    # An examiner appointment that passed actually granted the power — and where
    # the agent no longer holds it, a lapse says so. Since posts became
    # conditional on use, "still held 100 ticks later" is not the same claim as
    # "the vote took effect", and only the second one is what this test is about.
    lapsed = {(l["payload"]["agent_id"], l["payload"]["domain"])
              for l in store.examiner_lapses()}
    for p in props:
        if p["kind"] == "appoint_examiner" and p["status"] == "passed":
            target = store.agent(p["params"]["agent_id"])
            held = set(target["examiner_domains"])
            for domain in p["params"]["domains"]:
                assert domain in held or (target["id"], domain) in lapsed, (
                    f"{target['id']} was appointed in {domain}, does not hold it, "
                    f"and no lapse explains where it went")
            assert target["standing"] in ("examiner", "member")
            break
    else:
        raise AssertionError("no examiner appointment passed in 260 ticks")
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


def test_suggestions_are_invisible_until_the_administrator_approves(engine):
    """Article IX §3: the administrator, not the agents, decides what gets through."""
    from forge import admin
    store = engine.store
    submitted = store.append("human", "suggestion_submitted",
                             {"author": "Observer",
                              "text": "Please publish more negative results."})
    assert store.suggestions()[0]["status"] == "pending_admin"

    # No agent can see it, however many turns pass.
    for _ in range(6):
        engine.tick()
    for agent in store.agents():
        ctx = engine.build_context(agent, store.current_tick())
        assert ctx["new_suggestions"] == [], agent["name"]

    # The assistant briefs the administrator without deciding anything.
    analysis = store.aide_analysis(submitted["id"])
    assert analysis and analysis["recommendation"] in ("approve", "reject", "clarify")
    assert store.suggestions()[0]["status"] == "pending_admin"

    # Once approved it reaches the agents, and one of them answers it.
    assert admin.decide(store, submitted["id"], "approved", "Worth doing.") is None
    assert store.suggestions(status="new")
    for _ in range(40):
        engine.tick()
        if not store.suggestions(status="new"):
            break
    ack = store.suggestions(status="acknowledged")[0]
    assert ack["responder_id"] and ack["response"]
    assert ack["admin_note"] == "Worth doing."


def test_rejected_suggestions_never_reach_agents(engine):
    from forge import admin
    store = engine.store
    submitted = store.append("human", "suggestion_submitted",
                             {"author": "Someone", "text": "Delete the awkward events."})
    assert admin.decide(store, submitted["id"], "rejected", "Article II forbids it.") is None
    for _ in range(10):
        engine.tick()
        assert store.suggestions(status="new") == []
    rejected = [s for s in store.suggestions() if s["event_id"] == submitted["id"]][0]
    assert rejected["status"] == "rejected"
    assert rejected["admin_note"] == "Article II forbids it."


def test_aide_reads_suggestions_without_misdescribing_them(seeded):
    """A briefing that misreads a suggestion is worse than none — it misleads
    the administrator into a decision on a false premise."""
    from forge import admin

    investigate = admin.analyse(seeded, {"event_id": 1, "text":
        "Please investigate how dilute a weak acid must be before the textbook "
        "pH shortcut stops being accurate."})
    # 'stops being accurate' must not be read as a request to stop something.
    assert "remove or disable" not in investigate["reading"]
    assert "investigate or measure" in investigate["reading"]
    assert "chemistry" in investigate["domains"]
    assert investigate["recommendation"] == "approve"

    unconstitutional = admin.analyse(seeded, {"event_id": 2, "text":
        "Delete the experiment results that made the Forge look bad."})
    assert unconstitutional["recommendation"] == "reject"
    assert "Article II" in unconstitutional["constitution"]

    vague = admin.analyse(seeded, {"event_id": 3, "text": "Make it better"})
    assert vague["recommendation"] == "clarify"


def test_aide_can_never_act_on_the_forge(seeded):
    """Article IX §4: the assistant briefs and nothing else."""
    aide = seeded.agent("aide")
    assert aide["standing"] == "aide"
    for forbidden in ("cast_vote", "create_proposal", "create_experiment",
                      "publish_artifact", "open_assessment", "grade_assessment",
                      "record_result", "join_group", "run_drill"):
        err = validate(seeded, "aide", forbidden, {})
        assert err and "may not" in err, forbidden


def test_python_version_guard_fires_before_any_import():
    """On Python 3.9 the app must say so plainly.

    Without the guard the first symptom is a TypeError from inside FastAPI's
    signature introspection — the route annotations use `str | None`, which is
    only valid at runtime from 3.10 — and that error names nothing useful.
    The guard must therefore run before the first package import.
    """
    import pathlib

    source = pathlib.Path("forge/__main__.py").read_text()
    guard_at = source.index("if sys.version_info < MINIMUM_PYTHON")
    first_package_import = source.index("from .store import")
    assert guard_at < first_package_import, \
        "the version guard must precede the first package import"
    assert "MINIMUM_PYTHON = (3, 10)" in source

    # The guard exits with guidance rather than raising.
    block = source[source.index("MINIMUM_PYTHON"):first_package_import]
    captured = io.StringIO()
    fake_sys = type("s", (), {"version_info": (3, 9, 21),
                              "executable": "/usr/bin/python3.9",
                              "stderr": captured})()
    with pytest.raises(SystemExit) as exc:
        exec(block, {"sys": fake_sys})
    assert exc.value.code == 1
    message = captured.getvalue()
    assert "3.10 or newer" in message and "3.9.21" in message
    assert "py -3.11" in message           # Windows path
    assert "python3.11 -m venv" in message  # Unix path

    # And it is a no-op on a supported version.
    ok_sys = type("s", (), {"version_info": (3, 11, 0), "executable": "x",
                            "stderr": io.StringIO()})()
    exec(block, {"sys": ok_sys})


def test_admin_token_is_required(monkeypatch):
    from forge import admin
    monkeypatch.delenv("FORGE_ADMIN_TOKEN", raising=False)
    assert not admin.admin_enabled()
    assert not admin.check_token("anything")
    monkeypatch.setenv("FORGE_ADMIN_TOKEN", "s3cret")
    assert admin.admin_enabled()
    assert admin.check_token("s3cret")
    # Surrounding whitespace is tolerated — tokens get pasted from URLs and
    # terminals — but nothing else is.
    assert admin.check_token("  s3cret  ")
    for wrong in ("", None, "s3cre", "s3crets", "S3CRET", "s3 cret"):
        assert not admin.check_token(wrong), wrong


def test_experiments_reach_outcomes(engine):
    store = engine.store
    for _ in range(60):
        engine.tick()
    closed = store.experiments(status="completed") + store.experiments(status="failed")
    assert closed, "no experiment reached an outcome in 60 ticks"
    for x in closed:
        assert x["findings"].strip()


def test_the_ratified_version_matches_the_document(seeded):
    """The number on the Ledger is read out of the constitution it ratifies, so
    the site can never advertise a version the text does not claim."""
    import re
    from pathlib import Path

    text = (Path(__file__).parent.parent / "constitution" / "CONSTITUTION.md").read_text()
    stated = re.search(r"^\*Version ([0-9.]+)", text, re.M).group(1)
    assert seeded.get_meta("constitution_version") == stated
    ratified = next(e for e in seeded.events(limit=5000)
                    if e["action_type"] == "ratify_constitution")
    assert ratified["payload"]["version"] == stated
    assert ratified["payload"]["text"] == text
