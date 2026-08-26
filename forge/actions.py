"""The action vocabulary and its constitutional validation.

Agents act only through these actions; `validate()` is the structural
enforcement of the constitution — an action that violates it is refused
before it can reach the Ledger.
"""

from __future__ import annotations

from .store import DOMAINS, Store

PROPOSAL_KINDS = {"general", "charter_group", "admit_agent", "appoint_examiner",
                  "amend_constitution"}
VOTE_CHOICES = {"for", "against", "abstain"}

# Actions each standing may take (Articles III, IV, VI).
ALLOWED = {
    "candidate": {"post_message", "update_profile", "submit_answers"},
    "member": {"post_message", "update_profile", "create_proposal", "cast_vote",
               "create_experiment", "record_result", "publish_artifact", "join_group",
               "run_drill", "acknowledge_suggestion"},
}
ALLOWED["examiner"] = ALLOWED["member"] | {"open_assessment", "grade_assessment"}

REQUIRED_FIELDS = {
    "post_message": ["text"],
    "update_profile": [],
    "create_proposal": ["id", "kind", "title", "body", "closes_tick"],
    "cast_vote": ["proposal_id", "choice"],
    "create_experiment": ["id", "group_id", "title", "hypothesis", "method"],
    "record_result": ["experiment_id", "status", "findings"],
    "publish_artifact": ["id", "title", "abstract", "content", "content_hash", "authors"],
    "join_group": ["group_id"],
    "open_assessment": ["id", "candidate_id", "domain", "tasks"],
    "submit_answers": ["assessment_id", "answers"],
    "grade_assessment": ["assessment_id", "score"],
    "run_drill": ["trainee_id", "domain", "notes"],
    "acknowledge_suggestion": ["suggestion_event_id", "response"],
}


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
        if store.group(payload["group_id"]) is None:
            return "no such group"
        if actor_id not in [m["id"] for m in store.group_members(payload["group_id"])]:
            return "experiments are registered within one's own working group"

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

    elif action_type == "publish_artifact":
        if store.artifact(payload["id"]) is not None:
            return "duplicate artifact id"
        if actor_id not in payload["authors"]:
            return "publications are signed by their authors"

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
