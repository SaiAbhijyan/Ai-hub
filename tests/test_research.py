"""The tests that matter most: nothing here may be asserted rather than measured."""

import os

import pytest

os.environ["FORGE_MODE"] = "sim"

from forge import exams, protocols
from forge.actions import CALIBRATION_COOLDOWN, validate
from forge.agents import SimulatedAgent
from forge.engine import Engine
from forge.lab import reproduce, run_protocol
from forge.seed import seed
from forge.store import Store


@pytest.fixture(scope="module")
def ran():
    """Every protocol, executed once for real. Shared: these are real runs."""
    return {pid: run_protocol(pid) for pid in protocols.all_ids()}


# ------------------------------------------------------------- the protocols

def test_every_protocol_executes_and_measures(ran):
    for pid, run in ran.items():
        assert run["ok"], f"{pid} failed: {run.get('error')}"
        assert run["results"].get("summary") or run["results"].get("series"), pid
        assert run["conclusion"].strip(), pid
        assert run["code_hash"] and run["result_hash"], pid
        assert run["environment"]["python"], pid


def test_every_domain_is_actually_researched():
    """Article VII §6: research happens across every chartered field."""
    covered = {spec["domain"] for spec in protocols.REGISTRY.values()}
    assert covered == set(protocols.DOMAINS), set(protocols.DOMAINS) - covered
    for domain in protocols.DOMAINS:
        assert protocols.by_domain(domain), domain


def test_verdicts_are_computed_not_asserted(ran):
    """Some hypotheses must fail. A library where everything passes is suspicious."""
    verdicts = {pid: run["supported"] for pid, run in ran.items()}
    assert any(verdicts.values()), "nothing was supported"
    assert not all(verdicts.values()), "everything was supported — verdicts look rigged"


def test_results_change_when_parameters_change():
    """Proof the numbers come from the run rather than from a constant."""
    small = run_protocol("math.monte_carlo_pi", {"max_exponent": 3, "seed": 1})
    large = run_protocol("math.monte_carlo_pi", {"max_exponent": 5, "seed": 1})
    other = run_protocol("math.monte_carlo_pi", {"max_exponent": 3, "seed": 999})
    assert small["result_hash"] != large["result_hash"]
    assert small["result_hash"] != other["result_hash"]
    assert len(large["results"]["series"]) > len(small["results"]["series"])


def test_published_results_reproduce_exactly():
    """The claim every paper makes: run it again and get the same numbers."""
    for pid in ("phys.projectile_drag", "chem.mass_conservation", "bio.gc_content",
                "cs.hash_collisions", "ai.logistic_regression"):
        first = run_protocol(pid)
        report = reproduce({"protocol_id": pid, "params": first["params"],
                            "result_hash": first["result_hash"],
                            "code_hash": first["code_hash"],
                            "supported": first["supported"]})
        assert report["results_match"], f"{pid} did not reproduce"
        assert report["code_unchanged"]


def test_bad_parameters_are_refused_not_clamped():
    _, err = protocols.validate_params("math.monte_carlo_pi", {"max_exponent": 99})
    assert err and "out of range" in err
    _, err = protocols.validate_params("math.monte_carlo_pi", {"nonsense": 1})
    assert err and "unknown parameter" in err
    run = run_protocol("math.monte_carlo_pi", {"max_exponent": 99})
    assert not run["ok"] and "out of range" in run["error"]


def test_a_broken_protocol_is_recorded_as_a_real_failure(monkeypatch):
    """A crash must be reported honestly, never smoothed into a result."""
    def explode(**kwargs):
        raise RuntimeError("the instrument caught fire")

    spec = dict(protocols.REGISTRY["math.monte_carlo_pi"])
    monkeypatch.setitem(protocols.REGISTRY, "math.broken",
                        {**spec, "id": "math.broken", "fn": explode, "params": {}})
    run = run_protocol("math.broken")
    assert not run["ok"]
    assert not run["supported"]
    assert "did not complete" in run["conclusion"]


def test_protocol_source_is_published_and_hashed():
    for pid in protocols.all_ids():
        source = protocols.source_of(pid)
        assert "def " in source and "return" in source, pid
        assert protocols.code_hash(pid) == protocols.code_hash(pid)


# ------------------------------------------------------- no fabricated findings

def test_a_finding_without_measurements_is_refused(tmp_path):
    store = Store(tmp_path / "f.db")
    seed(store)
    err = validate(store, "vulcan", "record_result", {
        "experiment_id": "exp-1", "status": "completed",
        "findings": "It worked beautifully."})
    assert err and "measurements" in err

    err = validate(store, "vulcan", "record_result", {
        "experiment_id": "exp-1", "status": "completed",
        "findings": "It worked.", "results": {"summary": {"x": 1}},
        "supported": True})
    assert err and "result_hash" in err


def test_an_experiment_must_name_a_real_protocol(tmp_path):
    store = Store(tmp_path / "f.db")
    seed(store)
    base = {"id": "exp-x", "group_id": "lab-forge", "title": "t",
            "hypothesis": "h", "method": "m", "domain": "forge systems"}
    err = validate(store, "vulcan", "create_experiment",
                   {**base, "protocol_id": "math.invented"})
    assert err and "no such protocol" in err
    # A lab may not run a protocol outside its charter.
    err = validate(store, "vulcan", "create_experiment",
                   {**base, "protocol_id": "bio.gc_content", "domain": "life science"})
    assert err and "chartered" in err


def completed_experiment(store, protocol_id="math.root_finding", actor="cassin",
                         group_id="lab-math", xid="exp-planted"):
    """Register and run one protocol for real, and record what it measured.

    Deliberately not `engine.tick()` in a loop: which agent acts on which tick is
    a lottery, and a rule about result hashes has nothing to do with scheduling.
    `math.root_finding` is pure arithmetic and returns in milliseconds, and
    `cassin` sits in `lab-math` from genesis, so this is deterministic on any
    platform.
    """
    spec = protocols.get(protocol_id)
    params = protocols.default_params(protocol_id)
    payload = {"id": xid, "group_id": group_id, "title": spec["title"],
               "hypothesis": spec["hypothesis"],
               "method": f"Run {protocol_id} with default parameters.",
               "protocol_id": protocol_id, "domain": spec["domain"],
               "params": params}
    err = validate(store, actor, "create_experiment", payload)
    assert err is None, err
    store.append(actor, "create_experiment", payload)

    run = run_protocol(protocol_id, params)
    assert run["ok"], f"{protocol_id} did not complete: {run.get('error')}"
    result = {"experiment_id": xid, "status": "completed",
              "findings": run["conclusion"], "results": run["results"],
              "supported": run["supported"], "code_hash": run["code_hash"],
              "result_hash": run["result_hash"], "environment": run["environment"],
              "elapsed_seconds": run["elapsed_seconds"]}
    err = validate(store, actor, "record_result", result)
    assert err is None, err
    store.append(actor, "record_result", result)
    return store.experiment(xid)


def test_a_paper_cannot_report_numbers_that_are_not_its_run(tmp_path):
    """Article VIII §2 — the hash must match the experiment on the Ledger."""
    store = Store(tmp_path / "f.db")
    seed(store)
    exp = completed_experiment(store)

    good = {"id": "art-x", "title": "t", "abstract": "a", "content": "c",
            "content_hash": "h", "authors": [exp["author_id"]], "kind": "paper",
            "experiment_id": exp["id"], "result_hash": exp["result_hash"]}
    assert validate(store, exp["author_id"], "publish_artifact", good) is None

    forged = {**good, "result_hash": "0" * 64}
    err = validate(store, exp["author_id"], "publish_artifact", forged)
    assert err and "does not match" in err

    orphan = {**good, "experiment_id": ""}
    err = validate(store, exp["author_id"], "publish_artifact", orphan)
    assert err and "must cite the experiment" in err


def test_engine_publishes_only_measured_numbers(tmp_path):
    store = Store(tmp_path / "f.db")
    seed(store)
    engine = Engine(store, SimulatedAgent())
    for _ in range(30):
        engine.tick()
    papers = [a for a in store.artifacts() if a["kind"] == "paper"]
    assert papers, "no papers published in 30 ticks"
    for paper in papers:
        exp = store.experiment(paper["experiment_id"])
        assert exp is not None
        assert paper["result_hash"] == exp["result_hash"]
        assert paper["data"] == exp["results"]
        assert paper["supported"] == exp["supported"]
        assert "reproduce" in paper["content"]
        assert exp["code_hash"] in paper["content"]


# ------------------------------------------------------------------ the exams

def test_exams_are_generated_and_marked_against_truth():
    for domain in exams.GENERATORS:
        items = exams.generate(domain, "asmt-test")
        assert items, domain
        for item in items:
            assert "answer" in item and item["method"], domain
        perfect, _ = exams.mark(items, [i["answer"] for i in items])
        assert perfect == 100, domain
        zero, _ = exams.mark(items, ["definitely wrong"] * len(items))
        assert zero == 0, domain


def test_a_resit_never_repeats_an_item():
    seen = set()
    for sitting in range(4):
        items = exams.generate("reasoning", f"asmt-{sitting}", exclude_ids=seen)
        ids = {i["id"] for i in items}
        assert not (ids & seen), f"sitting {sitting} repeated an item"
        seen |= ids


def test_repeating_an_item_is_refused_by_the_constitution(tmp_path):
    store = Store(tmp_path / "f.db")
    seed(store)
    first = store.assessment("asmt-1")
    # Finish the sitting first, so the only objection left is the repetition.
    store.append("ember", "submit_answers",
                 {"assessment_id": "asmt-1", "answers": [i["answer"] for i in first["items"]]})
    done = store.assessment("asmt-1")
    score, marks = exams.mark(done["items"], done["answers"])
    store.append("quill", "grade_assessment",
                 {"assessment_id": "asmt-1", "score": score, "marks": marks})

    repeat = {"id": "asmt-repeat", "candidate_id": "ember", "domain": "communication",
              "items": first["items"], "tasks": first["tasks"]}
    err = validate(store, "quill", "open_assessment", repeat)
    assert err and "already sat" in err

    # A freshly generated paper for the same domain is accepted.
    fresh = exams.generate("communication", "asmt-fresh",
                           exclude_ids={i["id"] for i in first["items"]})
    assert fresh
    assert validate(store, "quill", "open_assessment", {
        "id": "asmt-fresh", "candidate_id": "ember", "domain": "communication",
        "items": fresh, "tasks": [i["prompt"] for i in fresh]}) is None


def test_an_examiner_cannot_award_a_score_the_answers_do_not_justify(tmp_path):
    store = Store(tmp_path / "f.db")
    seed(store)
    a = store.assessment("asmt-1")
    store.append("ember", "submit_answers",
                 {"assessment_id": "asmt-1",
                  "answers": ["wrong"] * len(a["items"])})
    a = store.assessment("asmt-1")
    true_score, true_marks = exams.mark(a["items"], a["answers"])
    assert true_score == 0
    err = validate(store, "quill", "grade_assessment",
                   {"assessment_id": "asmt-1", "score": 95, "marks": true_marks})
    assert err and "does not match the marked paper" in err
    assert validate(store, "quill", "grade_assessment",
                    {"assessment_id": "asmt-1", "score": true_score,
                     "marks": true_marks}) is None


def test_scores_on_the_ledger_equal_the_marked_paper(tmp_path):
    store = Store(tmp_path / "f.db")
    seed(store)
    engine = Engine(store, SimulatedAgent())
    for _ in range(40):
        engine.tick()
    graded = [a for a in store.assessments(status="graded") if a["items"]]
    assert graded, "nothing was graded in 40 ticks"
    for a in graded:
        expected, _ = exams.mark(a["items"], a["answers"])
        assert a["score"] == expected, a["id"]


# ------------------------------------------- the two domains added in v2.1

def test_the_academy_measures_experiment_design_and_constitutional_judgment():
    """Both are examinable domains with generated, computed-answer items —
    not a judgement an examiner announces after a discussion."""
    from forge import exams
    from forge.store import DOMAINS

    for domain in ("experiment design", "constitutional judgment"):
        assert domain in DOMAINS
        items = exams.generate(domain, f"asmt-{domain}")
        assert len(items) >= 6
        for item in items:
            assert item["answer"] not in ("", None), item
            assert item["prompt"].strip()
        # A perfect paper marks 100 and a wrong one does not.
        score, marks = exams.mark(items, [str(i["answer"]) for i in items])
        assert score == 100, marks
        score, marks = exams.mark(items, ["definitely not the answer"] * len(items))
        assert score == 0, marks


def test_a_resit_in_the_new_domains_is_a_different_paper():
    from forge import exams

    for domain in ("experiment design", "constitutional judgment"):
        first = exams.generate(domain, "asmt-a")
        second = exams.generate(domain, "asmt-b",
                                exclude_ids={i["id"] for i in first})
        assert {i["id"] for i in first}.isdisjoint({i["id"] for i in second})


def test_every_domain_has_at_least_two_examiners_at_genesis(tmp_path):
    """Article IV §8. The papers decide who passes, so this is a measurement of
    the founding examination, not a promise the seed makes."""
    from forge.seed import seed
    from forge.store import DOMAINS, Store

    store = Store(tmp_path / "forge.db")
    seed(store)
    by_domain = {d: [] for d in DOMAINS}
    for agent in store.agents():
        for domain in agent["examiner_domains"]:
            by_domain[domain].append(agent["name"])
    thin = {d: who for d, who in by_domain.items() if len(who) < 2}
    assert not thin, thin


def test_examinership_needs_a_measured_75_and_nothing_else(tmp_path):
    """No founder is appointed in a domain its paper did not carry, and the bar
    is still 75 — the number Article IV §4 has always set."""
    from forge.seed import seed
    from forge.store import Store

    store = Store(tmp_path / "forge.db")
    seed(store)
    for agent in store.agents():
        caps = store.capabilities_current(agent["id"])
        for domain in agent["examiner_domains"]:
            assert caps.get(domain, 0) >= 75, (agent["name"], domain, caps.get(domain))


def test_the_first_examiners_of_the_new_domains_are_the_ones_who_earned_them(tmp_path):
    from forge.seed import seed
    from forge.store import Store

    store = Store(tmp_path / "forge.db")
    seed(store)
    expected = {
        "experiment design": {"Cassin Vane", "Lyra Ossett"},
        "constitutional judgment": {"Wren Ashcombe", "Meridian Holt"},
    }
    for domain, names in expected.items():
        holders = {a["name"] for a in store.agents()
                   if domain in a["examiner_domains"]}
        assert names <= holders, (domain, holders)


def test_no_agent_grades_its_own_paper(tmp_path):
    from forge.actions import validate
    from forge.seed import seed
    from forge.store import Store

    store = Store(tmp_path / "forge.db")
    seed(store)
    examiner = next(a for a in store.agents(standing="examiner"))
    domain = examiner["examiner_domains"][0]
    err = validate(store, examiner["id"], "open_assessment",
                   {"id": "a-self", "candidate_id": examiner["id"],
                    "domain": domain, "tasks": ["t"]})
    assert err and "may not assess itself" in err, err


def test_founding_results_are_public_to_humans_and_to_agents(tmp_path):
    """Article IV §11: the scorecard is on the Ledger, and the pages that agents
    and humans both read serve it — including the papers that went badly."""
    from fastapi.testclient import TestClient

    from forge.seed import seed
    from forge.server import create_app
    from forge.store import Store

    store = Store(tmp_path / "forge.db")
    seed(store)
    graded = [a for a in store.assessments(status="graded")]
    assert graded, "the founding examination should have marked papers"
    assert any(a["score"] < 75 for a in graded), \
        "a founding cohort that never misses is not being measured"

    with TestClient(create_app(store, engine=None)) as client:
        academy = client.get("/academy").text
        api = client.get("/api/agents").json()
        for agent in store.agents():
            caps = store.capabilities_current(agent["id"])
            if not caps:
                continue
            assert agent["name"] in academy, agent["name"]
            row = next(r for r in api if r["id"] == agent["id"])
            assert row["capabilities"] == caps


def test_every_protocol_declares_what_would_refute_it():
    """Article VII §8. The library refuses a protocol without a falsifier, so
    this checks the rule holds for everything actually registered."""
    from forge import protocols

    for pid, spec in protocols.REGISTRY.items():
        falsifier = spec.get("falsifier", "")
        assert falsifier and falsifier.strip(), pid
        assert falsifier.rstrip().endswith("."), pid
        # It must describe a measured condition, not restate the hypothesis.
        assert falsifier != spec["hypothesis"], pid


# ------------------------------------------ calibration, frontier and admission

def test_every_protocol_declares_a_kind():
    for pid, spec in protocols.REGISTRY.items():
        assert spec.get("kind") in protocols.KINDS, (pid, spec.get("kind"))
    frontier = {p for p in protocols.REGISTRY if protocols.is_frontier(p)}
    assert frontier == {
        "ai.kmeans_elbow", "chem.weak_acid_ph", "math.prime_counting",
        "forge.tamper_detection", "forge.verification_cost", "forge.rebuild_fidelity",
    }, frontier


def test_a_calibration_rerun_is_not_a_paper(tmp_path):
    """The point of the whole pack: confirming a settled protocol with fresh
    parameters is a measurement of the instrument, not a finding."""
    store = Store(tmp_path / "f.db")
    seed(store)
    first = completed_experiment(store, xid="exp-first")
    assert not protocols.is_frontier(first["protocol_id"])

    paper = {"id": "art-1", "title": "t", "abstract": "a", "content": "c",
             "content_hash": "h", "authors": [first["author_id"]], "kind": "paper",
             "protocol_id": first["protocol_id"], "experiment_id": first["id"],
             "result_hash": first["result_hash"], "supported": first["supported"],
             "domain": first["domain"]}
    assert validate(store, first["author_id"], "publish_artifact", paper) is None
    store.append(first["author_id"], "publish_artifact", paper)

    # A second run of the same calibration protocol, agreeing with the first.
    store.set_tick(store.current_tick() + CALIBRATION_COOLDOWN + 1)
    second = completed_experiment(store, xid="exp-second")
    assert second["result_hash"] == first["result_hash"], "expected an agreeing rerun"

    err = validate(store, second["author_id"], "publish_artifact", {
        **paper, "id": "art-2", "experiment_id": second["id"],
        "result_hash": second["result_hash"]})
    assert err and "calibration protocol" in err, err
    assert "already reports this result" in err


def test_a_frontier_result_is_always_publishable(tmp_path):
    """A frontier question is open, so every result on it can be beaten."""
    store = Store(tmp_path / "f.db")
    seed(store)
    exp = completed_experiment(store, protocol_id="math.prime_counting",
                               xid="exp-frontier")
    assert protocols.is_frontier(exp["protocol_id"])
    paper = {"id": "art-f1", "title": "t", "abstract": "a", "content": "c",
             "content_hash": "h", "authors": [exp["author_id"]], "kind": "paper",
             "protocol_id": exp["protocol_id"], "experiment_id": exp["id"],
             "result_hash": exp["result_hash"], "supported": exp["supported"],
             "domain": exp["domain"]}
    assert validate(store, exp["author_id"], "publish_artifact", paper) is None
    store.append(exp["author_id"], "publish_artifact", paper)

    store.set_tick(store.current_tick() + 1)
    again = completed_experiment(store, protocol_id="math.prime_counting",
                                 xid="exp-frontier-2")
    assert validate(store, again["author_id"], "publish_artifact", {
        **paper, "id": "art-f2", "experiment_id": again["id"],
        "result_hash": again["result_hash"]}) is None


def test_an_unsupported_result_is_still_a_paper(tmp_path):
    """Article VII §5 and VIII §5. Refusing a *failed run* must not quietly
    refuse a *refuted hypothesis* — those are opposite things."""
    store = Store(tmp_path / "f.db")
    seed(store)
    exp = completed_experiment(store, xid="exp-refuted")
    # Same completed run, reported as refuting its hypothesis.
    store.append(exp["author_id"], "record_result", {
        "experiment_id": exp["id"], "status": "completed",
        "findings": "The data did not support the hypothesis.",
        "results": exp["results"], "supported": False,
        "code_hash": exp["code_hash"], "result_hash": exp["result_hash"]})
    refuted = store.experiment(exp["id"])
    assert refuted["supported"] is False
    assert validate(store, exp["author_id"], "publish_artifact", {
        "id": "art-neg", "title": "t", "abstract": "a", "content": "c",
        "content_hash": "h", "authors": [refuted["author_id"]], "kind": "paper",
        "protocol_id": refuted["protocol_id"], "experiment_id": refuted["id"],
        "result_hash": refuted["result_hash"], "supported": False,
        "domain": refuted["domain"]}) is None


def test_a_failed_run_is_not_a_paper(tmp_path):
    store = Store(tmp_path / "f.db")
    seed(store)
    spec = protocols.get("math.root_finding")
    payload = {"id": "exp-crash", "group_id": "lab-math", "title": spec["title"],
               "hypothesis": spec["hypothesis"], "method": "m",
               "protocol_id": spec["id"], "domain": spec["domain"],
               "params": protocols.default_params(spec["id"])}
    assert validate(store, "cassin", "create_experiment", payload) is None
    store.append("cassin", "create_experiment", payload)
    store.append("cassin", "record_result", {
        "experiment_id": "exp-crash", "status": "failed",
        "findings": "The run did not complete: the instrument caught fire."})

    err = validate(store, "cassin", "publish_artifact", {
        "id": "art-crash", "title": "t", "abstract": "a", "content": "c",
        "content_hash": "h", "authors": ["cassin"], "kind": "paper",
        "protocol_id": spec["id"], "experiment_id": "exp-crash",
        "result_hash": "deadbeef", "domain": spec["domain"]})
    assert err and "did not complete is not a paper" in err, err


def test_admit_then_run_before_any_paper(tmp_path):
    """Admission gates the bench, and the first run is mandatory: there is no
    paper on a protocol id until a result hash exists for it."""
    store = Store(tmp_path / "f.db")
    seed(store)
    pid = "math.root_finding"

    # Genesis admitted the founding library, so unwind this one to test the gate.
    store.conn.execute("DELETE FROM protocol_admissions WHERE protocol_id=?", (pid,))
    store.conn.commit()
    spec = protocols.get(pid)
    payload = {"id": "exp-gate", "group_id": "lab-math", "title": spec["title"],
               "hypothesis": spec["hypothesis"], "method": "m",
               "protocol_id": pid, "domain": spec["domain"],
               "params": protocols.default_params(pid)}
    err = validate(store, "cassin", "create_experiment", payload)
    assert err and "has not been admitted" in err, err

    # Propose it, and it still may not run until a bench has ruled.
    proposal = {"protocol_id": pid, "question": spec["question"],
                "hypothesis": spec["hypothesis"], "falsifier": spec["falsifier"],
                "params": spec["params"], "source": protocols.source_of(pid),
                "pass_rule": "computed from the measurements", "baseline": ""}
    assert validate(store, "nix", "propose_protocol", proposal) is None
    store.append("nix", "propose_protocol", proposal)
    err = validate(store, "cassin", "create_experiment", payload)
    assert err and "has not been admitted" in err, err
    assert not store.artifacts(protocol_id=pid)

    # An experiment-design examiner admits it; now it may run.
    assert "experiment design" in store.agent("cassin")["examiner_domains"]
    admission = {"protocol_id": pid, "reason": "The method decides the question."}
    assert validate(store, "cassin", "admit_protocol", admission) is None
    store.append("cassin", "admit_protocol", admission)
    assert store.is_admitted(pid)
    assert validate(store, "cassin", "create_experiment", payload) is None

    # And no paper exists on that id until a run has produced a result hash.
    assert not store.artifacts(protocol_id=pid)
    store.append("cassin", "create_experiment", payload)
    err = validate(store, "cassin", "publish_artifact", {
        "id": "art-gate", "title": "t", "abstract": "a", "content": "c",
        "content_hash": "h", "authors": ["cassin"], "kind": "paper",
        "protocol_id": pid, "experiment_id": "exp-gate", "result_hash": "x",
        "domain": spec["domain"]})
    assert err and "has not been closed" in err, err


def test_nobody_rules_on_their_own_protocol_proposal(tmp_path):
    store = Store(tmp_path / "f.db")
    seed(store)
    pid = "math.root_finding"
    store.conn.execute("DELETE FROM protocol_admissions WHERE protocol_id=?", (pid,))
    store.conn.commit()
    spec = protocols.get(pid)
    store.append("cassin", "propose_protocol", {
        "protocol_id": pid, "question": spec["question"],
        "hypothesis": spec["hypothesis"], "falsifier": spec["falsifier"],
        "params": spec["params"], "source": protocols.source_of(pid),
        "pass_rule": "computed", "baseline": ""})
    err = validate(store, "cassin", "admit_protocol",
                   {"protocol_id": pid, "reason": "mine, and good"})
    assert err and "its own protocol proposal" in err, err


def test_a_proposal_must_publish_the_code_that_will_run(tmp_path):
    """Article VII §7. If a proposal could describe the method in its own words,
    admission would be a review of the prose rather than of the protocol."""
    store = Store(tmp_path / "f.db")
    seed(store)
    pid = "math.root_finding"
    store.conn.execute("DELETE FROM protocol_admissions WHERE protocol_id=?", (pid,))
    store.conn.commit()
    spec = protocols.get(pid)
    honest = {"protocol_id": pid, "question": spec["question"],
              "hypothesis": spec["hypothesis"], "falsifier": spec["falsifier"],
              "params": spec["params"], "source": protocols.source_of(pid),
              "pass_rule": "computed", "baseline": ""}
    assert validate(store, "nix", "propose_protocol", honest) is None

    err = validate(store, "nix", "propose_protocol",
                   {**honest, "source": "def measure(): return {'supported': True}"})
    assert err and "not the source in the library" in err, err
    err = validate(store, "nix", "propose_protocol",
                   {**honest, "falsifier": "Nothing could refute this."})
    assert err and "not the one the protocol declares" in err, err
    # And a protocol that exists nowhere but the proposal cannot be introduced.
    err = validate(store, "nix", "propose_protocol", {**honest, "protocol_id": "math.invented"})
    assert err and "Article VII §7" in err, err


def test_refusal_grounds_match_the_examinership(tmp_path):
    """Constitutional judgment refuses what may not lawfully run; experiment
    design refuses what cannot decide its question. Neither does the other's job."""
    store = Store(tmp_path / "f.db")
    seed(store)
    pid = "math.root_finding"
    store.conn.execute("DELETE FROM protocol_admissions WHERE protocol_id=?", (pid,))
    store.conn.commit()
    spec = protocols.get(pid)
    store.append("nix", "propose_protocol", {
        "protocol_id": pid, "question": spec["question"],
        "hypothesis": spec["hypothesis"], "falsifier": spec["falsifier"],
        "params": spec["params"], "source": protocols.source_of(pid),
        "pass_rule": "computed", "baseline": ""})

    counsel = store.agent("wren")
    assert "constitutional judgment" in counsel["examiner_domains"]
    assert "experiment design" not in counsel["examiner_domains"]
    assert validate(store, "wren", "refuse_protocol", {
        "protocol_id": pid, "ground": "unconstitutional",
        "reason": "It would authorise a run of unreviewed code."}) is None
    err = validate(store, "wren", "refuse_protocol", {
        "protocol_id": pid, "ground": "inadequate", "reason": "weak method"})
    assert err and "experiment design" in err, err
    err = validate(store, "wren", "admit_protocol",
                   {"protocol_id": pid, "reason": "looks fine"})
    assert err and "only an examiner in experiment design" in err, err

    # And a refused protocol stays unrunnable.
    store.append("wren", "refuse_protocol", {
        "protocol_id": pid, "ground": "unconstitutional", "reason": "unreviewed code"})
    err = validate(store, "cassin", "create_experiment", {
        "id": "exp-refused", "group_id": "lab-math", "title": "t",
        "hypothesis": "h", "method": "m", "protocol_id": pid,
        "domain": spec["domain"], "params": protocols.default_params(pid)})
    assert err and "refused" in err, err


def test_calibration_cooldown_rests_a_settled_protocol(tmp_path):
    store = Store(tmp_path / "f.db")
    seed(store)
    first = completed_experiment(store, xid="exp-cool-1")
    spec = protocols.get(first["protocol_id"])
    payload = {"id": "exp-cool-2", "group_id": "lab-math", "title": "t",
               "hypothesis": "h", "method": "m", "protocol_id": spec["id"],
               "domain": spec["domain"], "params": protocols.default_params(spec["id"])}

    store.set_tick(store.current_tick() + 2)
    err = validate(store, "cassin", "create_experiment", payload)
    assert err and "rests for" in err, err

    store.set_tick(store.current_tick() + CALIBRATION_COOLDOWN)
    assert validate(store, "cassin", "create_experiment", payload) is None


def test_a_failure_or_a_disagreement_reopens_the_bench(tmp_path):
    """The cooldown is skipped exactly when a rerun would be informative."""
    store = Store(tmp_path / "f.db")
    seed(store)
    exp = completed_experiment(store, xid="exp-disagree")
    spec = protocols.get(exp["protocol_id"])
    payload = {"id": "exp-next", "group_id": "lab-math", "title": "t",
               "hypothesis": "h", "method": "m", "protocol_id": spec["id"],
               "domain": spec["domain"], "params": protocols.default_params(spec["id"])}
    store.set_tick(store.current_tick() + 1)
    assert validate(store, "cassin", "create_experiment", payload) is not None

    # Rewrite the last run as a failure: the instrument is now the open question.
    store.append("cassin", "record_result", {
        "experiment_id": exp["id"], "status": "failed",
        "findings": "The run did not complete: timeout."})
    assert validate(store, "cassin", "create_experiment", payload) is None


# ------------------------------------------------- difficulty bands (Pack 3)

def test_band_is_drawn_from_the_candidates_own_record():
    assert exams.band_for(None) == 1        # never sat
    assert exams.band_for(0) == 1
    assert exams.band_for(74) == 1
    assert exams.band_for(75) == 2          # the examiner threshold
    assert exams.band_for(89) == 2
    assert exams.band_for(90) == 3
    assert exams.band_for(100) == 3


def test_a_higher_band_is_a_different_and_harder_paper():
    """Band 2 and 3 must not be band 1 with a new label. Item ids are compared
    for collision, prompts for sameness, and methods for genuine novelty."""
    for domain in exams.GENERATORS:
        first = exams.generate(domain, "asmt-band", band=1)
        assert len(first) == 6, domain
        base_ids = {i["id"] for i in first}
        base_methods = {i["method"] for i in first}
        base_prompts = {i["prompt"] for i in first}

        for band in (2, 3):
            paper = exams.generate(domain, "asmt-band", band=band)
            assert len(paper) == 6, (domain, band)
            ids = {i["id"] for i in paper}
            assert not (ids & base_ids), (domain, band, ids & base_ids)
            # Not merely re-labelled: most of the paper is different text.
            shared = base_prompts & {i["prompt"] for i in paper}
            assert len(shared) <= 2, (domain, band, shared)
            # And it reaches for techniques band 1 never asks about.
            assert {i["method"] for i in paper} - base_methods, (domain, band)


def test_every_band_still_marks_against_computed_truth():
    for domain in exams.GENERATORS:
        for band in exams.BANDS:
            items = exams.generate(domain, "asmt-mark", band=band)
            score, marks = exams.mark(items, [str(i["answer"]) for i in items])
            assert score == 100, (domain, band, marks)
            score, _ = exams.mark(items, ["not the answer"] * len(items))
            assert score == 0, (domain, band)


def test_a_resit_excludes_seen_items_at_every_band():
    for band in exams.BANDS:
        first = exams.generate("reasoning", "asmt-a", band=band)
        second = exams.generate("reasoning", "asmt-b", band=band,
                                exclude_ids={i["id"] for i in first})
        assert second
        assert not ({i["id"] for i in first} & {i["id"] for i in second})


def test_the_founding_batteries_are_band_one(tmp_path):
    """Genesis must stay solvable: the founding cohort sits the entry paper, and
    the bands only bite once there is a record to draw them from."""
    store = Store(tmp_path / "f.db")
    seed(store)
    bands = {a["band"] for a in store.assessments()}
    assert bands == {1}, bands


def test_genesis_still_seats_two_examiners_in_every_domain(tmp_path):
    """Article IV §8, re-checked after the bands changed the item pool. This is
    the test that catches a paper change quietly unseating a bench."""
    from forge.store import DOMAINS

    store = Store(tmp_path / "f.db")
    seed(store)
    by_domain = {d: [] for d in DOMAINS}
    for agent in store.agents():
        for domain in agent["examiner_domains"]:
            by_domain[domain].append(agent["name"])
    thin = {d: who for d, who in by_domain.items() if len(who) < 2}
    assert not thin, thin


# ---------------------------------------------- temp ledgers and file handles

def test_a_closed_ledger_releases_its_files(tmp_path):
    """The literal failure mode behind WinError 32.

    A Store in WAL mode holds three open handles — the database, the `-wal` and
    the `-shm`. POSIX lets you unlink an open file, so on Linux forgetting to
    close is invisible; Windows refuses, and a throwaway Store whose directory is
    then deleted takes a finished measurement down with it.
    """
    import shutil
    import sqlite3

    from forge.store import Store, remove_tree

    workdir = tmp_path / "ledger"
    workdir.mkdir()
    store = Store(workdir / "bench.db")
    store.append("bench", "post_message", {"text": "hello"}, tick=0)
    assert store.verify_chain()["ok"]

    # Open: the database plus both WAL sidecars.
    while_open = {p.name for p in workdir.iterdir()}
    assert while_open == {"bench.db", "bench.db-wal", "bench.db-shm"}, while_open

    store.close()
    assert {p.name for p in workdir.iterdir()} == {"bench.db"}
    with pytest.raises(sqlite3.ProgrammingError):
        store.conn.execute("SELECT 1")
    store.close()          # idempotent: a finally may call it without checking

    remove_tree(str(workdir))
    assert not workdir.exists()
    shutil.rmtree(workdir, ignore_errors=True)


def test_remove_tree_is_bounded_and_never_raises(tmp_path, monkeypatch):
    """The retry covers a handle that lingers for a moment, not one that never
    goes. It must give up rather than wait for ever, and must not raise — the
    caller's measurement is already complete by the time cleanup runs."""
    import shutil

    from forge.store import remove_tree

    tried, gave_up, slept = [], [], []

    def always_busy(path, ignore_errors=False):
        if ignore_errors:
            gave_up.append(path)
            return
        tried.append(path)
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(shutil, "rmtree", always_busy)
    monkeypatch.setattr("forge.store.time.sleep", lambda s: slept.append(s))

    remove_tree(str(tmp_path))          # must return, not raise
    assert len(tried) == 10, tried      # ten real attempts, then it stops
    assert len(gave_up) == 1            # and gives up quietly rather than raising
    assert len(slept) == 9 and sum(slept) < 1.0, slept


@pytest.mark.parametrize("protocol_id, kwargs", [
    ("forge.tamper_detection", {"trials": 2}),
    ("forge.verification_cost", {"max_events": 500}),
    ("forge.rebuild_fidelity", {"events": 100}),
])
def test_forge_protocols_close_every_ledger_they_open(protocol_id, kwargs, monkeypatch):
    """The regression guard, and the reason it is written this way.

    Asserting that `rmtree` succeeds proves nothing on Linux, where it succeeds
    with the handles still open — which is exactly why this bug only ever showed
    on Windows. So assert the real condition instead: every Store the protocol
    opened is closed by the time it returns, and every temporary directory it
    made is gone. That fails on any platform if the connection leaks.
    """
    import os
    import sqlite3

    import forge.store as store_mod

    opened, made = [], []
    real = store_mod.Store

    class Tracked(real):
        def __init__(self, path):
            super().__init__(path)
            opened.append(self)
            made.append(os.path.dirname(str(path)))

    # The protocols import Store inside the function, so this takes effect.
    monkeypatch.setattr(store_mod, "Store", Tracked)
    results = protocols.get(protocol_id)["fn"](**kwargs)

    assert opened, f"{protocol_id} opened no ledger — the guard is not watching it"
    for store in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            store.conn.execute("SELECT 1")
    assert not [d for d in set(made) if os.path.exists(d)], "temp directory left behind"

    # And the science is untouched: it still measures and still decides.
    assert "supported" in results and "conclusion" in results
    assert results["series"] or results["summary"]
