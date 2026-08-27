"""The tests that matter most: nothing here may be asserted rather than measured."""

import os

import pytest

os.environ["FORGE_MODE"] = "sim"

from forge import exams, protocols
from forge.actions import validate
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


def test_a_paper_cannot_report_numbers_that_are_not_its_run(tmp_path):
    """Article VIII §2 — the hash must match the experiment on the Ledger."""
    store = Store(tmp_path / "f.db")
    seed(store)
    engine = Engine(store, SimulatedAgent())
    for _ in range(12):
        engine.tick()
    done = [x for x in store.experiments() if x["status"] == "completed"]
    assert done, "no experiment completed in 12 ticks"
    exp = done[0]

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
