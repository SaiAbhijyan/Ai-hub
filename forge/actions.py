"""The action vocabulary and its constitutional validation.

Agents act only through these actions; `validate()` is the structural
enforcement of the constitution — an action that violates it is refused
before it can reach the Ledger.
"""

from __future__ import annotations

from . import protocols
from .store import DOMAINS, Store

PROPOSAL_KINDS = {"general", "charter_group", "admit_agent", "appoint_examiner",
                  "amend_constitution"}
VOTE_CHOICES = {"for", "against", "abstain"}
COMMONS_TOPICS = {"welcome", "milestone", "reading", "off-duty", "question", "thanks"}
ARTIFACT_KINDS = {"paper", "replication", "method_proposal", "invention_disclosure"}
REFUSAL_GROUNDS = {"unconstitutional", "inadequate"}

# How long a calibration protocol rests between runs.
#
# A calibration protocol's answer is already known, so a second run measures the
# instrument rather than the world. That is worth doing periodically and worth
# recording — the Forge should notice if arithmetic stops working — but running
# it every few ticks is how an agent manufactures a body of work out of nothing.
# Thirty ticks is roughly four engine rotations at ACTORS_PER_TICK=2, long enough
# that a lab must go and find something new to do in between.
#
# The rest is skipped whenever a rerun would actually be informative: if the last
# run failed, or if it disagreed with the run before it, the instrument is the
# open question and it should be re-run at once.
CALIBRATION_COOLDOWN = 30

# Actions each standing may take (Articles III, IV, VI, XI).
ALLOWED = {
    "candidate": {"post_message", "update_profile", "submit_answers", "post_commons"},
    "member": {"post_message", "update_profile", "create_proposal", "cast_vote",
               "create_experiment", "record_result", "publish_artifact", "join_group",
               "run_drill", "acknowledge_suggestion", "post_commons",
               "propose_protocol"},
}
# Admitting and refusing a protocol are examiner powers: which domain of
# examinership they require is checked per action, below.
ALLOWED["examiner"] = ALLOWED["member"] | {"open_assessment", "grade_assessment",
                                           "admit_protocol", "refuse_protocol"}
# The administrator's assistant serves the human, not the Forge: it briefs and
# talks, but never votes, experiments, examines or publishes.
ALLOWED["aide"] = {"post_commons", "aide_analysis", "update_profile"}

REQUIRED_FIELDS = {
    "post_message": ["text"],
    "update_profile": [],
    "create_proposal": ["id", "kind", "title", "body", "closes_tick"],
    "cast_vote": ["proposal_id", "choice"],
    "create_experiment": ["id", "group_id", "title", "hypothesis", "method",
                          "protocol_id", "domain"],
    "record_result": ["experiment_id", "status", "findings"],
    "publish_artifact": ["id", "title", "abstract", "content", "content_hash", "authors"],
    "join_group": ["group_id"],
    "open_assessment": ["id", "candidate_id", "domain", "tasks"],
    "submit_answers": ["assessment_id", "answers"],
    "grade_assessment": ["assessment_id", "score"],
    "run_drill": ["trainee_id", "domain", "notes"],
    "acknowledge_suggestion": ["suggestion_event_id", "response"],
    "post_commons": ["topic", "text"],
    "aide_analysis": ["suggestion_id", "reading", "recommendation"],
    "propose_protocol": ["protocol_id", "question", "hypothesis", "falsifier",
                         "params", "source", "pass_rule", "baseline"],
    "admit_protocol": ["protocol_id", "reason"],
    "refuse_protocol": ["protocol_id", "reason", "ground"],
}


def _closed_runs(store: Store, protocol_id: str) -> list[dict]:
    """Every finished run of a protocol, newest first."""
    return [x for x in store.experiments_for_protocol(protocol_id)
            if x["status"] in ("completed", "failed")]


def rerun_is_informative(store: Store, protocol_id: str) -> bool:
    """True when running a settled protocol again would actually tell us something.

    Two cases: the last run failed, so the instrument itself is now in question;
    or the last two runs disagreed, so one of them is wrong and nobody yet knows
    which. Both are reasons to go back to the bench immediately.
    """
    closed = _closed_runs(store, protocol_id)
    if not closed:
        return True
    if closed[0]["status"] == "failed":
        return True
    hashes = [x["result_hash"] for x in closed if x["status"] == "completed"]
    return len(hashes) >= 2 and hashes[0] != hashes[1]


def calibration_cooldown_error(store: Store, spec: dict, tick: int) -> str | None:
    """Rate-limit reruns of a protocol whose answer is already known."""
    if spec["kind"] != "calibration":
        return None
    history = store.experiments_for_protocol(spec["id"])
    if not history:
        return None                      # the mandatory first run is never blocked
    if rerun_is_informative(store, spec["id"]):
        return None
    running = [x for x in history if x["status"] == "running"]
    if running:
        return (f"{spec['id']} is already running as {running[0]['id']} — one live "
                f"replication at a time for a calibration protocol")
    since = tick - max(x["opened_tick"] for x in history)
    if since < CALIBRATION_COOLDOWN:
        return (f"{spec['id']} is a calibration protocol and was last run "
                f"{since} tick{'' if since == 1 else 's'} ago; it rests for "
                f"{CALIBRATION_COOLDOWN}. Re-run it sooner only when the last run "
                f"failed or disagreed with the one before it")
    return None


def calibration_credit_error(store: Store, exp: dict) -> str | None:
    """Decide whether a completed run has earned a publication.

    A rerun of a calibration protocol with a fresh seed re-confirms something the
    Forge already knew and already published. Recording it on the Experiment
    Board is right; awarding it a paper is how an archive fills with work that
    discovered nothing. Credit needs one of three things:

      * a first result on this protocol — which is what a newly admitted protocol
        always produces, and the reason the first run is mandatory;
      * a frontier protocol, where the question is open and any result can be
        beaten or refuted;
      * a measured disagreement with what is already published — a different
        verdict, or the same parameters returning different numbers.
    """
    protocol_id = exp["protocol_id"]
    if not protocol_id:
        return None
    published = [a for a in store.artifacts(protocol_id=protocol_id)
                 if a["kind"] in ("paper", "replication")
                 and a["experiment_id"] != exp["id"]]
    if not published:
        return None
    if protocols.is_frontier(protocol_id):
        return None
    for prior in published:
        if prior["supported"] is not None and exp["supported"] is not None \
                and bool(prior["supported"]) != bool(exp["supported"]):
            return None                  # a refutation of what we published
        prior_exp = store.experiment(prior["experiment_id"])
        if prior_exp and prior_exp["params"] == exp["params"] \
                and prior_exp["result_hash"] and exp["result_hash"] \
                and prior_exp["result_hash"] != exp["result_hash"]:
            return None                  # same inputs, different numbers
    return (f"{protocol_id} is a calibration protocol and "
            f"{published[0]['id']} already reports this result. A rerun with new "
            f"parameters confirms the instrument, not a finding — credit needs a "
            f"newly admitted protocol, a frontier result, or a measured "
            f"disagreement with what is already published")


def validate(store: Store, actor_id: str, action_type: str, payload: dict) -> str | None:
    """Return an error string if the action is unconstitutional, else None."""
    actor = store.agent(actor_id)
    if actor is None:
        return f"unknown agent '{actor_id}'"
    if action_type not in REQUIRED_FIELDS:
        return f"unknown action '{action_type}'"
    if action_type not in ALLOWED[actor["standing"]]:
        return f"a {actor['standing']} may not {action_type}"
    missing = [f for f in REQUIRED_FIELDS[action_type] if f not in payload]
    if missing:
        return f"{action_type} missing fields: {', '.join(missing)}"

    if action_type == "post_message":
        gid = payload.get("group_id")
        if gid and store.group(gid) is None:
            return f"no such group '{gid}'"
        if not str(payload["text"]).strip():
            return "empty message"

    elif action_type == "create_proposal":
        kind = payload["kind"]
        if kind not in PROPOSAL_KINDS:
            return f"unknown proposal kind '{kind}'"
        if store.proposal(payload["id"]) is not None:
            return "duplicate proposal id"
        if payload["closes_tick"] <= store.current_tick():
            return "voting window must close in the future"
        params = payload.get("params", {})
        if kind == "admit_agent":
            cand = store.agent(params.get("agent_id", ""))
            if cand is None or cand["standing"] != "candidate":
                return "admit_agent requires an existing candidate"
            if not store.entrance_battery_passed(cand["id"]):
                return ("Article IV §3: candidate has not passed the entrance battery"
                        " (three domains at 60+)")
        elif kind == "charter_group":
            for f in ("id", "name", "goal", "charter"):
                if f not in params:
                    return f"charter_group params missing '{f}'"
            if store.group(params["id"]) is not None:
                return "duplicate group id"
        elif kind == "appoint_examiner":
            target = store.agent(params.get("agent_id", ""))
            if target is None or target["standing"] == "candidate":
                return "appoint_examiner requires an existing member"
            domains = params.get("domains", [])
            if not domains or any(d not in DOMAINS for d in domains):
                return "appoint_examiner requires valid domains"
            caps = store.capabilities_current(target["id"])
            weak = [d for d in domains if caps.get(d, 0) < 75]
            if weak:
                return f"Article IV §4: score below 75 in {', '.join(weak)}"
        elif kind == "amend_constitution":
            if not params.get("text") or not params.get("version"):
                return "amend_constitution requires new text and version"

    elif action_type == "cast_vote":
        prop = store.proposal(payload["proposal_id"])
        if prop is None:
            return "no such proposal"
        if prop["status"] != "open":
            return "voting window is closed"
        if payload["choice"] not in VOTE_CHOICES:
            return f"invalid vote choice '{payload['choice']}'"
        if store.has_voted(prop["id"], actor_id):
            return "already voted (one ballot per agent)"

    elif action_type == "create_experiment":
        if store.experiment(payload["id"]) is not None:
            return "duplicate experiment id"
        group = store.group(payload["group_id"])
        if group is None:
            return "no such group"
        if actor_id not in [m["id"] for m in store.group_members(payload["group_id"])]:
            return "experiments are registered within one's own working group"
        # Article VII as amended: an experiment must name a real, runnable protocol.
        spec = protocols.get(payload["protocol_id"])
        if spec is None:
            return f"no such protocol '{payload['protocol_id']}'"
        if spec["domain"] != payload["domain"]:
            return (f"protocol {spec['id']} is {spec['domain']}, not "
                    f"{payload['domain']!r}")
        lab_domains = group.get("domains") or []
        if lab_domains and spec["domain"] not in lab_domains:
            return (f"{group['name']} is chartered for {', '.join(lab_domains)}, "
                    f"not {spec['domain']}")
        _, param_error = protocols.validate_params(spec["id"], payload.get("params", {}))
        if param_error:
            return param_error
        # Admission gates the bench, not just the paper, so the order is always
        # admit -> run -> publish and the mandatory first run is structural.
        admission = store.protocol_admission(spec["id"])
        if admission is None or admission["status"] != "admitted":
            state = admission["status"] if admission else "never proposed"
            return (f"{spec['id']} has not been admitted to the library ({state}) — "
                    f"an experiment-design examiner admits a protocol before it runs")
        cooldown = calibration_cooldown_error(store, spec, store.current_tick())
        if cooldown:
            return cooldown

    elif action_type == "record_result":
        exp = store.experiment(payload["experiment_id"])
        if exp is None:
            return "no such experiment"
        if exp["status"] != "running":
            return "experiment already closed"
        if payload["status"] not in ("completed", "failed"):
            return "result status must be completed or failed"
        if not str(payload["findings"]).strip():
            return "Article VII §2: an outcome must record findings"
        # The heart of the reproducibility rule: a completed experiment must
        # carry the measurements and the hashes of the run that produced them.
        # Nothing may write a finding that did not come from executing code.
        if payload["status"] == "completed":
            if not payload.get("results"):
                return ("Article VII as amended: a completed experiment must carry the "
                        "measurements its protocol returned")
            for field in ("result_hash", "code_hash"):
                if not payload.get(field):
                    return f"a completed experiment must record its {field}"
            if payload.get("supported") is None:
                return "a completed experiment must record whether the data supported it"

    elif action_type == "publish_artifact":
        if store.artifact(payload["id"]) is not None:
            return "duplicate artifact id"
        if actor_id not in payload["authors"]:
            return "publications are signed by their authors"
        kind = payload.get("kind", "paper")
        if kind not in ARTIFACT_KINDS:
            return f"unknown publication kind '{kind}'"
        if payload.get("domain") and payload["domain"] not in protocols.DOMAINS:
            return f"unknown domain '{payload['domain']}'"
        if kind in ("paper", "replication"):
            # A paper must be anchored to a completed run: no measurements, no paper.
            exp = store.experiment(payload.get("experiment_id", ""))
            if exp is None:
                return f"a {kind} must cite the experiment it reports"
            if exp["status"] == "running":
                return "cannot publish an experiment that has not been closed"
            if exp["status"] == "failed":
                # Not the same thing as a refuted hypothesis, which is published in
                # full under Article VII §5. This is a run that never produced a
                # measurement at all — a crash, a timeout, a refused parameter —
                # and there is nothing in it to report.
                return ("a run that did not complete is not a paper: it produced no "
                        "measurements. The failure stays on the Experiment Board, "
                        "where Article VII §4 requires it")
            if not payload.get("result_hash"):
                return "a paper must carry the result hash of its run"
            if payload["result_hash"] != exp["result_hash"]:
                return ("result hash does not match the experiment on the Ledger — "
                        "a paper may only report the numbers its run produced")
            credit = calibration_credit_error(store, exp)
            if credit:
                return credit

    elif action_type == "propose_protocol":
        pid = payload["protocol_id"]
        spec = protocols.get(pid)
        # Article VII §7: an agent may not execute code of its own authorship, and
        # a protocol is human-reviewed before it enters the library. A proposal is
        # therefore the Forge's own review of code a human has already committed —
        # never a way to introduce new code through the Ledger.
        if spec is None:
            return (f"no such protocol '{pid}' — Article VII §7: a protocol enters "
                    f"the library by human review, not by proposal")
        existing = store.protocol_admission(pid)
        if existing and existing["status"] == "admitted":
            return f"{pid} is already admitted"
        if existing and existing["status"] == "proposed":
            return f"{pid} is already before the examiners"
        # The specification must describe the code that will actually run. If the
        # proposal could restate the method in its own words, admission would be a
        # review of the prose rather than of the protocol.
        if payload["source"] != protocols.source_of(pid):
            return ("the source in this proposal is not the source in the library — "
                    "a proposal must publish the code that will actually run")
        if payload["falsifier"] != spec["falsifier"]:
            return ("the falsifier in this proposal is not the one the protocol "
                    "declares")
        if payload["params"] != spec["params"]:
            return "the parameters in this proposal are not the protocol's parameters"
        for field in ("question", "hypothesis", "pass_rule"):
            if not str(payload[field]).strip():
                return f"a protocol proposal must state its {field.replace('_', ' ')}"
        baseline = payload["baseline"]
        if baseline and not any(x["result_hash"] == baseline
                                for x in store.experiments_for_protocol(pid)):
            return ("baseline names a result hash that is not on the Ledger — "
                    "a protocol may only be asked to beat a measured result")

    elif action_type in ("admit_protocol", "refuse_protocol"):
        pid = payload["protocol_id"]
        row = store.protocol_admission(pid)
        if row is None:
            return f"no proposal for protocol '{pid}'"
        if row["status"] != "proposed":
            return f"{pid} has already been {row['status']}"
        if row["proposer_id"] == actor_id:
            # The same rule as Article IV §4 for examinations: nobody sits in
            # judgement on their own submission.
            return "an examiner may not rule on its own protocol proposal"
        if action_type == "admit_protocol":
            if "experiment design" not in actor["examiner_domains"]:
                return ("only an examiner in experiment design admits a protocol — "
                        "admission is a judgement about whether the method can decide "
                        "the question")
        else:
            ground = payload["ground"]
            if ground not in REFUSAL_GROUNDS:
                return f"refusal ground must be one of {', '.join(sorted(REFUSAL_GROUNDS))}"
            needed = ("constitutional judgment" if ground == "unconstitutional"
                      else "experiment design")
            if needed not in actor["examiner_domains"]:
                return (f"a refusal on {ground} grounds is for an examiner in "
                        f"{needed}")
        if not str(payload["reason"]).strip():
            return f"{action_type} must record its reason"

    elif action_type == "post_commons":
        if payload["topic"] not in COMMONS_TOPICS:
            return f"unknown commons topic '{payload['topic']}'"
        if not str(payload["text"]).strip():
            return "empty post"
        for mentioned in payload.get("mentions", []):
            if store.agent(mentioned) is None:
                return f"mentions unknown agent '{mentioned}'"

    elif action_type == "aide_analysis":
        if actor["standing"] != "aide":
            return "only the administrator's assistant files analyses"
        match = [s for s in store.suggestions()
                 if s["event_id"] == payload["suggestion_id"]]
        if not match:
            return "no such suggestion"
        if payload["recommendation"] not in ("approve", "reject", "clarify"):
            return "recommendation must be approve, reject or clarify"

    elif action_type == "join_group":
        group = store.group(payload["group_id"])
        if group is None:
            return "no such group"
        if actor_id in [m["id"] for m in store.group_members(group["id"])]:
            return "already a member of this group"
        caps = store.capabilities_current(actor_id)
        for domain, minimum in group["thresholds"].items():
            if caps.get(domain, 0) < minimum:
                return (f"charter threshold not met: {domain} requires {minimum},"
                        f" current {caps.get(domain, 0)}")

    elif action_type == "open_assessment":
        if store.assessment(payload["id"]) is not None:
            return "duplicate assessment id"
        domain = payload["domain"]
        if domain not in DOMAINS:
            return f"unknown domain '{domain}'"
        if domain not in actor["examiner_domains"]:
            return f"not an examiner for {domain}"
        cand = store.agent(payload["candidate_id"])
        if cand is None:
            return "no such candidate"
        if cand["id"] == actor_id:
            return "Article IV §4: an examiner may not assess itself"
        if store.assessments(candidate_id=cand["id"], status="open") or \
           store.assessments(candidate_id=cand["id"], status="answered"):
            return "candidate already has an assessment in progress"
        if not payload["tasks"]:
            return "assessment requires tasks"
        items = payload.get("items", [])
        if not items:
            return "assessment requires generated items with verifiable answers"
        if any("answer" not in item for item in items):
            return "every item must carry the answer it will be marked against"
        # Article IV as amended: a re-sit must be a different examination.
        from .exams import prior_item_ids
        seen = prior_item_ids(store.assessments(candidate_id=cand["id"]))
        repeats = [i["id"] for i in items if i["id"] in seen]
        if repeats:
            return (f"Article IV: {cand['name']} has already sat "
                    f"{', '.join(repeats[:3])} — a re-test must use new items")

    elif action_type == "submit_answers":
        a = store.assessment(payload["assessment_id"])
        if a is None:
            return "no such assessment"
        if a["candidate_id"] != actor_id:
            return "only the candidate answers its own assessment"
        if a["status"] != "open":
            return "assessment is not awaiting answers"
        if len(payload["answers"]) != len(a["tasks"]):
            return "one answer per task is required"

    elif action_type == "grade_assessment":
        a = store.assessment(payload["assessment_id"])
        if a is None:
            return "no such assessment"
        if a["examiner_id"] != actor_id:
            return "only the opening examiner grades"
        if a["status"] != "answered":
            return "assessment is not awaiting grading"
        score = payload["score"]
        if not isinstance(score, int) or not 0 <= score <= 100:
            return "score must be an integer 0-100"
        # A grade must be the marking of the actual paper, recomputed here so an
        # examiner cannot award a score the answers do not justify.
        from .exams import mark
        if a["items"]:
            expected_score, expected_marks = mark(a["items"], a["answers"])
            if score != expected_score:
                return (f"score {score} does not match the marked paper "
                        f"({expected_score}); grades are computed, not chosen")
            if payload.get("marks") != expected_marks:
                return "marks do not match the answers on the Ledger"

    elif action_type == "run_drill":
        trainee = store.agent(payload["trainee_id"])
        if trainee is None:
            return "no such trainee"
        if trainee["id"] == actor_id:
            return "a drill needs a mentor and a trainee"
        if payload["domain"] not in DOMAINS:
            return f"unknown domain '{payload['domain']}'"

    elif action_type == "acknowledge_suggestion":
        row = [s for s in store.suggestions() if s["event_id"] == payload["suggestion_event_id"]]
        if not row:
            return "no such suggestion"
        if row[0]["status"] != "new":
            return "suggestion already acknowledged"

    return None
