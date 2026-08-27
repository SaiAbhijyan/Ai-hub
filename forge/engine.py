"""The engine: the heartbeat of the Forge.

Each tick it advances governance (closing voting windows and executing passed
proposals exactly as written), then gives a few agents their turn: context is
assembled from the Ledger, the agent produces actions, each action is validated
against the constitution and appended to the chain.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Iterable

from .actions import validate
from .store import Store

log = logging.getLogger("forge.engine")

ACTORS_PER_TICK = 2
MAX_ACTIONS_PER_TURN = 3
VOTING_WINDOW = 8  # default ticks a proposal stays open; agents may choose longer
CANDIDATE_INTERVAL = 40  # ticks between new candidates presenting to the Academy
EXPERIMENT_MATURITY = 2  # ticks between registering a protocol run and executing it
RUNS_PER_TICK = 1        # protocol executions per tick; real code costs real seconds

# How long an agent may go without authoring anything before the Forge calls it
# idle. Forty ticks is roughly twenty rotations at ACTORS_PER_TICK=2, so an agent
# has to miss its turn many times over before the label changes.
IDLE_TICKS = int(os.environ.get("FORGE_IDLE_TICKS", "40"))

# How long a chartered laboratory may go without registering an experiment,
# moving a proposal, ruling on a protocol or publishing before the Forge says so
# on the Floor. Longer than an agent's window, because a lab is several agents
# and a quiet week is not the same as an abandoned bench.
LAB_IDLE_TICKS = int(os.environ.get("FORGE_LAB_IDLE_TICKS", "50"))

# How long an examinership survives without being used. Article IV as amended:
# the post is held on condition of use, and an examiner who neither sits nor
# grades its domain for this long loses it there. Longer again, because losing
# an office should take real neglect rather than one quiet stretch.
EXAMINER_LAPSE_TICKS = int(os.environ.get("FORGE_EXAMINER_LAPSE_TICKS", "80"))


class Engine:
    def __init__(self, store: Store, runtime):
        """runtime: object with act(agent: dict, context: dict) -> list[(type, payload)]."""
        self.store = store
        self.runtime = runtime
        self.last_acted: dict[str, int] = {}

    # ------------------------------------------------------------------ tick

    def tick(self) -> list[dict]:
        store = self.store
        tick = store.current_tick() + 1
        store.set_tick(tick)
        appended: list[dict] = []
        # Protocols are real computation and cost real seconds. Running at most
        # one per tick keeps a tick bounded so the live interface stays responsive
        # however heavy the science gets.
        self._runs_this_tick = 0

        appended += self.close_expired_proposals(tick)
        appended += self.admit_new_candidate(tick)
        appended += self.brief_administrator(tick)
        appended += self.notice_idle_labs(tick)
        appended += self.lapse_unused_examinerships(tick)

        for agent in self.pick_actors(tick):
            context = self.build_context(agent, tick)
            try:
                actions = self.runtime.act(agent, context)
            except Exception:
                log.exception("runtime failed for %s", agent["id"])
                continue
            for action_type, payload in list(actions)[:MAX_ACTIONS_PER_TURN]:
                err = validate(store, agent["id"], action_type, payload)
                if err:
                    log.warning("refused %s by %s: %s", action_type, agent["id"], err)
                    # A refused publication is part of the record. An agent that
                    # tried to bank credit for a calibration rerun should be
                    # visible having tried, and the reader of an experiment should
                    # be able to see why no paper came out of it.
                    if action_type == "publish_artifact":
                        appended.append(store.append("forge", "publication_refused", {
                            "agent_id": agent["id"],
                            "artifact_id": payload.get("id", ""),
                            "experiment_id": payload.get("experiment_id", ""),
                            "protocol_id": payload.get("protocol_id", ""),
                            "reason": err,
                        }, tick))
                    continue
                appended.append(store.append(agent["id"], action_type, payload, tick))
            self.last_acted[agent["id"]] = tick
        return appended

    async def run(self, tick_seconds: float, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            await asyncio.to_thread(self.tick)
            try:
                await asyncio.wait_for(stop.wait(), timeout=tick_seconds)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------ governance

    def close_expired_proposals(self, tick: int) -> list[dict]:
        store = self.store
        appended = []
        for prop in store.proposals(status="open"):
            if prop["closes_tick"] > tick:
                continue
            votes = store.votes_for(prop["id"])
            tally = {"for": 0, "against": 0, "abstain": 0}
            for v in votes:
                tally[v["choice"]] += 1
            cast = tally["for"] + tally["against"] + tally["abstain"]
            if prop["kind"] == "amend_constitution":
                passed = cast >= 2 and tally["for"] * 3 >= cast * 2 and tally["for"] > 0
            else:
                passed = cast >= 2 and tally["for"] > tally["against"]
            outcome = "passed" if passed else "failed"
            appended.append(store.append("forge", "proposal_closed", {
                "proposal_id": prop["id"], "outcome": outcome, "tally": tally,
            }, tick))
            if passed:
                appended += self.execute(prop, tick)
        return appended

    def admit_new_candidate(self, tick: int) -> list[dict]:
        """The Forge is permanent, so the Academy keeps receiving candidates.

        A new persona presents itself only when no examination is already in
        flight, so the Academy finishes with one agent before starting another.
        """
        if tick == 0 or tick % CANDIDATE_INTERVAL != 0:
            return []
        if self.store.agents(standing="candidate"):
            return []
        from .seed import next_candidate
        persona = next_candidate(self.store)
        if persona is None:
            return []
        return [self.store.append("forge", "found_agent",
                                  dict(persona, standing="candidate",
                                       examiner_domains=[], initial_capabilities={},
                                       avatar_seed=persona["id"]), tick)]

    # A laboratory's work: the events that mean the bench is in use. A group that
    # produced none of these has gone quiet, whatever its members were saying.
    LAB_WORK = ("create_experiment", "record_result", "create_proposal",
                "propose_protocol", "admit_protocol", "refuse_protocol",
                "publish_artifact")

    def notice_idle_labs(self, tick: int) -> list[dict]:
        """Say on the Floor that a chartered laboratory has gone quiet.

        This is a notice and nothing else. It never registers an experiment,
        never starts a run, and carries no instruction an agent is expected to
        obey — the Forge is not permitted to manufacture activity in order to
        look busy. Whether anyone picks the work up is theirs to decide, and if
        nobody does, the silence is on the record where a human can see it.
        """
        if tick <= 0:
            return []
        store = self.store
        cutoff = tick - LAB_IDLE_TICKS
        if cutoff < 0:
            return []                     # the Forge has not been running long enough

        appended = []
        for group in store.groups():
            if not group.get("domains"):
                continue                  # not a laboratory; nothing to run
            if store.group_worked_since(group["id"], cutoff, self.LAB_WORK):
                continue
            if store.idle_notices(group["id"], since_tick=cutoff):
                continue                  # one notice per group per window
            item = self.frontier_item(group)
            appended.append(store.append("forge", "lab_idle_notice", {
                "group_id": group["id"], "group_name": group["name"],
                "idle_since_tick": cutoff,
                "frontier_protocol": item["id"] if item else "",
                "frontier_question": item["question"] if item else "",
            }, tick))
        return appended

    @staticmethod
    def frontier_item(group: dict) -> dict | None:
        """The open question this laboratory is chartered for, if it has one."""
        from . import protocols
        for domain in group.get("domains") or []:
            for spec in protocols.by_domain(domain):
                if spec["kind"] == "frontier":
                    return spec
        return None

    def lapse_unused_examinerships(self, tick: int) -> list[dict]:
        """Article IV: an examinership is held on condition of use.

        An examiner who has neither sat nor graded its domain for the whole
        window loses the post there — not everywhere, and its `stands_for`
        declaration survives, so it may stand again. Recovery is the ordinary
        route: sit the domain at the band its record now earns, score 75 or
        above, marked by someone else.

        One thing outranks this. Article IV §8 gives every domain at least two
        examiners, and a domain at zero would be sealed shut: regaining the post
        requires an examination that nobody would be left to open. So the last
        two in a domain are held, and the deferral is written down rather than
        passed over in silence.
        """
        if tick <= 0:
            return []
        store = self.store
        cutoff = tick - EXAMINER_LAPSE_TICKS
        if cutoff < 0:
            return []

        # Count the bench in each domain first: whether a post lapses depends on
        # how many others hold it, not only on its own neglect.
        holders: dict[str, list[str]] = {}
        for agent in store.agents():
            for domain in agent["examiner_domains"]:
                holders.setdefault(domain, []).append(agent["id"])

        appended = []
        for agent in store.agents():
            for domain in sorted(agent["examiner_domains"]):
                last = store.last_academy_touch(agent["id"], domain)
                if last and last["tick"] > cutoff:
                    continue
                if len(holders.get(domain, [])) <= 2:
                    if store.lapse_deferrals(agent["id"], domain, since_tick=cutoff):
                        continue          # already said so this window
                    appended.append(store.append("forge", "lapse_deferred", {
                        "agent_id": agent["id"], "domain": domain,
                        "holders": len(holders.get(domain, [])),
                        "reason": ("Article IV §8: every domain shall have at least "
                                   "two examiners. This post is unused but held, "
                                   "because losing it would leave the domain unable "
                                   "to examine anyone back into it."),
                    }, tick))
                    continue
                appended.append(store.append("forge", "examiner_lapsed", {
                    "agent_id": agent["id"], "domain": domain,
                    "last_touch_tick": last["tick"] if last else None,
                    "last_touch_event": last["id"] if last else None,
                    "window": EXAMINER_LAPSE_TICKS,
                    "reason": (f"Article IV: examinership is held on condition of "
                               f"use. No sitting and no marking in {domain} for "
                               f"{EXAMINER_LAPSE_TICKS} ticks. The declaration to "
                               f"stand for it survives; the post does not."),
                }, tick))
                holders[domain].remove(agent["id"])
        return appended

    def execute(self, prop: dict, tick: int) -> list[dict]:
        """Automatic effect of a passed proposal (Article VI §3)."""
        store = self.store
        params = prop["params"]
        kind = prop["kind"]
        if kind == "admit_agent":
            return [store.append("forge", "agent_promoted", {
                "agent_id": params["agent_id"], "proposal_id": prop["id"]}, tick)]
        if kind == "charter_group":
            return [store.append("forge", "charter_group", {
                "id": params["id"], "name": params["name"], "goal": params["goal"],
                "charter": params["charter"], "thresholds": params.get("thresholds", {}),
                "members": params.get("members", []), "proposal_id": prop["id"]}, tick)]
        if kind == "appoint_examiner":
            return [store.append("forge", "examiner_appointed", {
                "agent_id": params["agent_id"], "domains": params["domains"],
                "proposal_id": prop["id"]}, tick)]
        if kind == "amend_constitution":
            return [store.append("forge", "constitution_amended", {
                "version": params["version"], "text": params["text"],
                "proposal_id": prop["id"]}, tick)]
        return []

    # ------------------------------------------------------------------ scheduling

    def pick_actors(self, tick: int) -> Iterable[dict]:
        store = self.store
        chosen: list[dict] = []
        chosen_ids: set[str] = set()

        def add(agent: dict | None):
            if agent and agent["id"] not in chosen_ids:
                chosen.append(agent)
                chosen_ids.add(agent["id"])

        # Academy work comes first: candidates with an exam on the desk, then
        # examiners with answers to grade.
        for a in store.assessments(status="open"):
            add(store.agent(a["candidate_id"]))
        for a in store.assessments(status="answered"):
            add(store.agent(a["examiner_id"]))

        # Then the rotation: whoever has waited longest. The administrator's
        # assistant is not part of the Forge's rotation — it works for the human,
        # and its one duty (briefing on pending suggestions) runs every tick.
        everyone = [a for a in store.agents() if a["standing"] != "aide"]
        everyone.sort(key=lambda a: (self.last_acted.get(a["id"], -1), a["joined_event"]))
        for agent in everyone:
            if len(chosen) >= ACTORS_PER_TICK:
                break
            add(agent)
        return chosen

    # ------------------------------------------------------------------ context

    def brief_administrator(self, tick: int) -> list[dict]:
        """Have the assistant analyse any suggestion still waiting on the administrator.

        This runs regardless of the agent rotation: a human waiting on a decision
        should not have to wait for the assistant's turn to come round.
        """
        from .admin import analyse
        from .actions import validate
        store = self.store
        if store.agent("aide") is None:
            return []
        appended = []
        for suggestion in store.suggestions(status="pending_admin"):
            if store.aide_analysis(suggestion["event_id"]):
                continue
            payload = analyse(store, suggestion)
            err = validate(store, "aide", "aide_analysis", payload)
            if err:
                log.warning("aide analysis refused: %s", err)
                continue
            appended.append(store.append("aide", "aide_analysis", payload, tick))
        return appended

    def execute_due_experiment(self, agent: dict, tick: int) -> dict | None:
        """Actually run one of this agent's registered protocols.

        This is where the Forge's research happens: the protocol executes in a
        sandboxed subprocess and returns real measurements. The agent then reports
        whatever came back — including a failure, which is a result too.
        """
        # A nested Forge — one built by a protocol to generate a ledger to replay —
        # must not run protocols of its own, or the recursion is unbounded.
        if os.environ.get("FORGE_NO_PROTOCOLS"):
            return None
        from .lab import run_protocol
        if getattr(self, "_runs_this_tick", 0) >= RUNS_PER_TICK:
            return None
        for exp in self.store.experiments(status="running"):
            if exp["author_id"] != agent["id"]:
                continue
            if tick - exp["opened_tick"] < EXPERIMENT_MATURITY:
                continue
            if not exp["protocol_id"]:
                continue
            log.info("running %s for %s (%s)", exp["protocol_id"], agent["id"], exp["id"])
            self._runs_this_tick = getattr(self, "_runs_this_tick", 0) + 1
            run = run_protocol(exp["protocol_id"], exp["params"])
            return {"experiment": exp, "run": run}
        return None

    def build_context(self, agent: dict, tick: int) -> dict:
        """Everything an agent is given for its turn — all of it from the Ledger."""
        store = self.store
        aid = agent["id"]
        ctx: dict = {
            "tick": tick,
            "voting_window": VOTING_WINDOW,
            "next_event_id": store.next_id(),
            "capabilities": store.capabilities_current(aid),
            "my_groups": store.agent_groups(aid),
            "recent_events": store.events(limit=25),
            "recent_messages": store.messages(limit=15),
            "open_proposals": store.proposals(status="open"),
            "unvoted_proposals": [],
            "my_running_experiments": [],
            "assessment_to_answer": None,
            "assessments_to_grade": [],
            "candidates_needing_exam": [],
            "resits_available": [],
            "candidates_ready_for_admission": [],
            "examiner_candidates": [],
            # Article IX as amended: only suggestions the administrator approved
            # are ever visible here. Pending ones do not reach an agent at all.
            "new_suggestions": store.suggestions(status="new"),
            "agents": [{"id": a["id"], "name": a["name"], "standing": a["standing"],
                        "profession": a["profession"]} for a in store.agents()],
            "groups": store.groups(),
            "joinable_groups": [],
            "completed_run": None,
            "credit_error": None,
            "newest_member": None,
            "protocol_run_count": {},
            "protocol_to_propose": None,
            "proposals_to_rule_on": [],
            "choose_protocol": lambda group, who, rng: choose_protocol(
                group, who, store, rng),
            "choose_proposal": lambda who, rng: choose_proposal(who, store, rng),
        }

        from .agents import choose_proposal, choose_protocol  # noqa: E402

        if agent["standing"] != "candidate":
            caps = ctx["capabilities"]
            mine = {g["id"] for g in ctx["my_groups"]}
            ctx["joinable_groups"] = [
                g for g in ctx["groups"]
                if g["id"] not in mine
                and all(caps.get(d, 0) >= m for d, m in g["thresholds"].items())
            ]
            ctx["completed_run"] = self.execute_due_experiment(agent, tick)
            # Whether the run that just came back has earned a publication. The
            # agent uses this to decide; the validator decides for real.
            if ctx["completed_run"] and ctx["completed_run"]["run"]["ok"]:
                from .actions import calibration_credit_error
                finished = dict(ctx["completed_run"]["experiment"],
                                supported=ctx["completed_run"]["run"]["supported"],
                                result_hash=ctx["completed_run"]["run"]["result_hash"])
                ctx["credit_error"] = calibration_credit_error(store, finished)
            # Protocols still waiting to be read into the library, and proposals
            # waiting on a bench that this agent may sit on.
            ctx["protocol_to_propose"] = None
            if store.protocol_admissions(status="proposed"):
                ctx["proposals_to_rule_on"] = [
                    p for p in store.protocol_admissions(status="proposed")
                    if p["proposer_id"] != aid]

        counts: dict[str, int] = {}
        for exp in store.experiments():
            if exp["protocol_id"]:
                counts[exp["protocol_id"]] = counts.get(exp["protocol_id"], 0) + 1
        ctx["protocol_run_count"] = counts

        members = [a for a in store.agents() if a["standing"] in ("member", "examiner")]
        if members:
            ctx["newest_member"] = max(members, key=lambda a: a["joined_event"])

        if agent["standing"] != "candidate":
            ctx["unvoted_proposals"] = [p for p in ctx["open_proposals"]
                                        if not store.has_voted(p["id"], aid)]
            ctx["my_running_experiments"] = [
                x for x in store.experiments(status="running") if x["author_id"] == aid]
            # Candidates whose battery is complete and who have no admission
            # proposal open yet — any member may raise one.
            proposed = {p["params"].get("agent_id") for p in store.proposals()
                        if p["kind"] == "admit_agent" and p["status"] in ("open", "passed")}
            for cand in store.agents(standing="candidate"):
                if cand["id"] in proposed:
                    continue
                if store.entrance_battery_passed(cand["id"]):
                    ctx["candidates_ready_for_admission"].append(cand)

            # Agents who have demonstrated 75+ in a domain they may not yet
            # examine (Article IV §4), and whose appointment isn't already moved.
            #
            # Only *open* proposals block a fresh motion. An appointment that
            # passed and then lapsed must be movable again, or Article IV §10's
            # promise that a post can be re-earned would be empty: the agent
            # would sit the paper, pass it, and wait for a motion nobody could
            # raise.
            moved = {(p["params"].get("agent_id"), d) for p in store.proposals()
                     if p["kind"] == "appoint_examiner" and p["status"] == "open"
                     for d in p["params"].get("domains", [])}
            for other in store.agents():
                if other["standing"] == "candidate":
                    continue
                for domain, score in store.capabilities_current(other["id"]).items():
                    if score >= 75 and domain not in other["examiner_domains"] \
                            and (other["id"], domain) not in moved:
                        ctx["examiner_candidates"].append(
                            {"agent": other, "domain": domain, "score": score})

        # A paper on the desk is sat by whoever it was set for. Candidates are
        # the usual case, but a member re-sitting a domain it lost to lapse has
        # one too, and nobody else can answer it for them.
        open_mine = store.assessments(candidate_id=aid, status="open")
        ctx["assessment_to_answer"] = open_mine[0] if open_mine else None

        if agent["standing"] == "examiner":
            ctx["assessments_to_grade"] = store.assessments(examiner_id=aid, status="answered")
            # Agents who lost a post to lapse and may sit their way back onto it.
            # This is the recovery route Article IV §10 promises, and it is the
            # reason the difficulty bands ever come into play: a lapsed agent
            # already has a record, so the paper it re-sits is harder than the
            # one it originally passed.
            for lapse in store.examiner_lapses():
                who, domain = lapse["payload"]["agent_id"], lapse["payload"]["domain"]
                other = store.agent(who)
                if other is None or who == aid:
                    continue
                if domain in other["examiner_domains"]:
                    continue              # already back on the bench
                if domain not in agent["examiner_domains"]:
                    continue              # this examiner cannot set that paper
                sat = store.assessments(candidate_id=who)
                if [a for a in sat if a["status"] in ("open", "answered")]:
                    continue
                # The old passing score is still on the record — it is why they
                # held the post at all — so it cannot be what decides this. Only
                # a pass recorded *after* the lapse means they are waiting on
                # the Chamber rather than on another paper.
                since = [row for row in store.capability_history(who)
                         if row["domain"] == domain
                         and row["event_id"] > lapse["id"] and row["score"] >= 75]
                if since:
                    continue
                from .exams import prior_item_ids
                ctx["resits_available"].append({
                    "agent": other, "domain": domain,
                    "last_score": store.capabilities_current(who).get(domain),
                    "seen_item_ids": sorted(prior_item_ids(sat)),
                    "sittings": sum(1 for a in sat if a["domain"] == domain),
                })
            # Candidates with no exam in progress and battery not yet complete.
            from .exams import prior_item_ids
            for cand in store.agents(standing="candidate"):
                sat = store.assessments(candidate_id=cand["id"])
                busy = [a for a in sat if a["status"] in ("open", "answered")]
                if busy or store.entrance_battery_passed(cand["id"]):
                    continue
                graded = [a for a in sat if a["status"] == "graded"]
                examined = {a["domain"] for a in graded
                            if a["score"] is not None and a["score"] >= 60}
                sittings: dict[str, int] = {}
                for a in graded:
                    sittings[a["domain"]] = sittings.get(a["domain"], 0) + 1
                ctx["candidates_needing_exam"].append({
                    "agent": cand,
                    "domains_passed": sorted(examined),
                    # What this candidate last scored in each domain, which is
                    # what sets the difficulty of the next paper.
                    "last_scores": store.capabilities_current(cand["id"]),
                    # Every item this candidate has ever been set, so a re-sit
                    # is generated from fresh questions (Article IV as amended).
                    "seen_item_ids": sorted(prior_item_ids(sat)),
                    "sittings": sittings,
                })

        return ctx
