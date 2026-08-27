"""The engine: the heartbeat of the Forge.

Each tick it advances governance (closing voting windows and executing passed
proposals exactly as written), then gives a few agents their turn: context is
assembled from the Ledger, the agent produces actions, each action is validated
against the constitution and appended to the chain.
"""

from __future__ import annotations

import asyncio
import logging
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
        import os
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
            "newest_member": None,
            "protocol_run_count": {},
            "choose_protocol": lambda group, who, rng: choose_protocol(
                group, who, store, rng),
        }

        from .agents import choose_protocol  # noqa: E402  (avoids a circular import)

        if agent["standing"] != "candidate":
            caps = ctx["capabilities"]
            mine = {g["id"] for g in ctx["my_groups"]}
            ctx["joinable_groups"] = [
                g for g in ctx["groups"]
                if g["id"] not in mine
                and all(caps.get(d, 0) >= m for d, m in g["thresholds"].items())
            ]
            ctx["completed_run"] = self.execute_due_experiment(agent, tick)

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
            moved = {(p["params"].get("agent_id"), d) for p in store.proposals()
                     if p["kind"] == "appoint_examiner" and p["status"] in ("open", "passed")
                     for d in p["params"].get("domains", [])}
            for other in store.agents():
                if other["standing"] == "candidate":
                    continue
                for domain, score in store.capabilities_current(other["id"]).items():
                    if score >= 75 and domain not in other["examiner_domains"] \
                            and (other["id"], domain) not in moved:
                        ctx["examiner_candidates"].append(
                            {"agent": other, "domain": domain, "score": score})

        if agent["standing"] == "candidate":
            open_mine = store.assessments(candidate_id=aid, status="open")
            ctx["assessment_to_answer"] = open_mine[0] if open_mine else None

        if agent["standing"] == "examiner":
            ctx["assessments_to_grade"] = store.assessments(examiner_id=aid, status="answered")
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
                    # Every item this candidate has ever been set, so a re-sit
                    # is generated from fresh questions (Article IV as amended).
                    "seen_item_ids": sorted(prior_item_ids(sat)),
                    "sittings": sittings,
                })

        return ctx
