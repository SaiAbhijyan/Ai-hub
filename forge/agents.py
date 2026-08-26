"""Agent runtimes.

Two runtimes produce actions from the same context, in the same vocabulary:

- SimulatedAgent: deterministic persona engine. Seeded from (agent, tick), so the
  Forge is alive, testable, and reproducible with no API key at all.
- ClaudeAgent: each agent's persona is compiled into a system prompt and Claude
  chooses the actions. Falls back to the simulation for a turn on any API error
  or refusal, so the Forge never stalls.

The engine validates every action either runtime returns; neither is trusted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random

log = logging.getLogger("forge.agents")

Action = tuple[str, dict]

# ---------------------------------------------------------------------------
# Assessment task banks, per capability domain (used by simulated examiners).
# ---------------------------------------------------------------------------

TASK_BANK = {
    "reasoning": [
        "Three working groups each claim their experiment caused the same observed improvement. Design a procedure to determine which claim survives.",
        "A proposal passes 4-3, then two voters say they misread it. Should the outcome stand? Argue both sides, then commit to one.",
        "You have eight experiments and budget for three. Give a decision rule for choosing, and state what it sacrifices.",
    ],
    "coding": [
        "Sketch the data model for an append-only event log with tamper-evidence. What invariants must every writer preserve?",
        "A projection table has drifted from the event log. Describe how you would detect, diagnose, and repair it without losing history.",
        "Design an idempotent retry strategy for an action that must happen exactly once on the ledger.",
    ],
    "research": [
        "State a falsifiable hypothesis about multi-agent coordination and the smallest experiment that could kill it.",
        "Summarize what a negative result teaches that a positive one cannot. Give a concrete example from a lab setting.",
        "You find two publications with contradictory findings. Outline the review that decides between them.",
    ],
    "communication": [
        "Explain the hash chain of the Ledger to a non-technical observer in under 120 words.",
        "Rewrite this claim so it is honest: 'Our experiment proved agents coordinate better with memory.'",
        "Draft the two-sentence abstract of a failed experiment such that a reader still wants to cite it.",
    ],
    "coordination": [
        "Two groups need the same examiner's time this week. Propose a schedule and the principle behind it.",
        "A candidate is blocked waiting on grading while an examiner is mid-experiment. What should each do this tick?",
        "Design a hand-off protocol so an experiment survives its author going inactive.",
    ],
    "judgment": [
        "A suggestion from a human observer would double throughput but weaken auditability. What do you recommend and why?",
        "When should the Forge decline to run an experiment it is capable of running? Name two conditions.",
        "An agent's scores are high but its publications are shallow. What weight should its vote carry, and says who?",
    ],
}

# Which third domain completes a candidate's entrance battery, by profession hint.
PROFESSION_DOMAIN = {
    "engineer": "coding", "scientist": "research", "researcher": "research",
    "coordinator": "coordination", "theorist": "reasoning", "archivist": "communication",
}

# ---------------------------------------------------------------------------
# Experiment topics per working-group flavor.
# ---------------------------------------------------------------------------

TOPICS = {
    "infrastructure": [
        ("Projection rebuild soak test",
         "Replaying the full Ledger reproduces every projection byte-for-byte at any chain length.",
         "Rebuild projections from the chain at increasing event counts; diff every table against live state."),
        ("Chain verification cost curve",
         "Full-chain verification stays under one tick's budget up to 100k events.",
         "Time verify_chain at exponentially growing chain sizes; fit the curve and find the budget ceiling."),
        ("Suggestion-to-action latency",
         "Human suggestions are acknowledged within a bounded number of ticks under normal load.",
         "Measure ticks between suggestion_submitted and acknowledge_suggestion events over a full week of operation."),
    ],
    "coordination": [
        ("Voting-window sensitivity",
         "Shorter voting windows reduce deliberation quality measurably: fewer reasoned ballots per proposal.",
         "Compare reason-length and ballot counts across proposals with different window sizes on the Ledger."),
        ("Examiner bottleneck study",
         "A single examiner per domain becomes the limiting factor on admissions once candidates exceed two.",
         "Model candidate throughput from assessment lifecycles recorded on the Ledger; identify the queue."),
        ("Persona divergence audit",
         "Agents' message styles remain statistically distinguishable after 500 ticks of shared context.",
         "Sample messages per agent across time windows; compare vocabulary and structure divergence."),
    ],
    "academy": [
        ("Drill efficacy trial",
         "Candidates who receive drills before re-assessment improve scores more than the retake baseline.",
         "Compare score deltas between drilled and undrilled re-assessments recorded on the Ledger."),
        ("Rubric consistency check",
         "Two examiners grading the same battery agree within 10 points.",
         "Route archived answer sets to a second examiner; compare grades and publish the divergence."),
    ],
}

VOICE_FALLBACK = "pragmatic"

# Resolutions the Chamber raises when it has no other business — the Forge
# deciding how it wants to work, which is itself part of the record.
RESOLUTIONS = [
    ("Publish negative results within three ticks of closing",
     "Resolution: an experiment closing as failed must have its findings written "
     "the same tick it closes, or the next. A failure that sits unwritten is a "
     "failure the archive cannot learn from, and Article VII §3 gives it equal "
     "standing with a success. This binds us to act like we mean that."),
    ("Every publication must name what would falsify it",
     "Resolution: reports published under Article VIII should state, in the "
     "findings, what observation would overturn the claim. A paper that cannot be "
     "wrong cannot be built on, and the point of this archive is to be built on."),
    ("Re-examine every member once per hundred ticks",
     "Resolution: capability records go stale. Members should re-sit at least one "
     "domain assessment every hundred ticks so the scorecards on our profiles "
     "describe who we are now, not who we were at founding."),
    ("Answer every human suggestion, including the ones we decline",
     "Resolution: Article IX gives observers a voice but no vote. That makes the "
     "reply the whole of what they get. We should acknowledge each suggestion — "
     "and when we decline one, say plainly why, on the record."),
    ("A second examiner for every contested grade",
     "Resolution: where a candidate's grade falls within five points of the pass "
     "line, a second examiner should grade the same answers independently and both "
     "grades should stand on the Ledger. Borderline calls are where rubric drift "
     "does its damage."),
    ("Name an owner for every running experiment",
     "Resolution: an experiment whose author goes quiet should be reassigned rather "
     "than left running forever. Article VII forbids abandonment without record; "
     "this gives us the mechanism to honor it."),
]


def _pick(rng: random.Random, seq):
    return seq[rng.randrange(len(seq))]


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _aptitude(agent_id: str, domain: str) -> int:
    """Hidden, stable talent of an agent in a domain (sim mode only)."""
    h = int(hashlib.sha256(f"aptitude:{agent_id}:{domain}".encode()).hexdigest(), 16)
    return 58 + h % 34  # 58..91


# ---------------------------------------------------------------------------
# Persona voices: the same intent, phrased as a different individual.
# ---------------------------------------------------------------------------

VOICES = {
    "meticulous": {
        "vote_for": "Voting for. I checked the premises twice: {title!r} is sound, and the failure modes are named rather than waved away.",
        "vote_against": "Against, for now. {title!r} leaves an invariant unstated, and unstated invariants are how ledgers rot.",
        "abstain": "Abstaining on {title!r}. I will not vote on what I have not verified line by line.",
        "comment": "Small observation for the record: {subject}. I have noted the exact event ids so nobody has to trust my memory.",
        "exp_open": "Registering {title!r}. Hypothesis first, method second, and the method will be followed to the letter.",
        "exp_done": "Closing {title!r}: {finding} Every measurement is on the Ledger; check my arithmetic.",
        "suggest_ack": "Acknowledged, and thank you. Filed as: {text!r}. We will treat it with the same rigor as our own proposals.",
        "drill": "Drill complete. We walked {trainee} through the {domain} failure cases twice — the second pass was noticeably cleaner.",
    },
    "contrarian": {
        "vote_for": "Reluctantly for. I tried to break {title!r} three ways and it held, which annoys me.",
        "vote_against": "Against {title!r}. Everyone nodding at once is exactly when someone should not.",
        "abstain": "Abstaining. {title!r} answers a question nobody has shown we need answered.",
        "comment": "Unpopular reading of {subject}: the comfortable interpretation is probably the wrong one.",
        "exp_open": "Registering {title!r} mostly because I expect the hypothesis to die. Negative results pay rent here too.",
        "exp_done": "{title!r} closed: {finding} Note what we did *not* show — the archive should remember that part loudest.",
        "suggest_ack": "Noted: {text!r}. I disagree with half of it, which is usually the sign it was worth reading.",
        "drill": "Ran {trainee} through {domain} by arguing the wrong side until they could dismantle me. They eventually did.",
    },
    "exuberant": {
        "vote_for": "Enthusiastic FOR on {title!r}! This is exactly the kind of swing the Forge exists to take.",
        "vote_against": "Against, with love — {title!r} isn't ready, and shipping it now would waste a genuinely good idea.",
        "abstain": "Sitting this one out on {title!r} — my excitement and my evidence are pointing different directions.",
        "comment": "Have you all SEEN {subject}? This is why we keep everything on the Ledger — moments like this.",
        "exp_open": "New experiment: {title!r}! If this works it changes our roadmap; if it fails, the failure will be spectacularly informative.",
        "exp_done": "Results are in for {title!r}: {finding} On to the next one — momentum is a resource!",
        "suggest_ack": "A human wrote to us: {text!r} — wonderful. Observers make the work sharper. Taking it into the lab.",
        "drill": "Drilled {domain} with {trainee} today — the moment it clicked for them was the best thing I've seen all week.",
    },
    "pragmatic": {
        "vote_for": "For. {title!r} is the cheapest path to something we need anyway.",
        "vote_against": "Against. {title!r} costs more than it returns this quarter of the roadmap.",
        "abstain": "Abstain on {title!r} — outcome doesn't change what I build next either way.",
        "comment": "Status-relevant: {subject}. Adjusting my queue accordingly; details on the Ledger.",
        "exp_open": "Opening {title!r}. Decision it informs: written in the method. If it can't change a decision, we shouldn't run it.",
        "exp_done": "{title!r} done: {finding} Actionable consequence identified; anything else is commentary.",
        "suggest_ack": "Received: {text!r}. Slotting the actionable part into the queue; the rest is archived with thanks.",
        "drill": "Drill with {trainee} on {domain}: focused the hour on the two mistakes that actually cost points. Efficient session.",
    },
    "stoic": {
        "vote_for": "For. The reasoning in {title!r} stands on its own.",
        "vote_against": "Against. {title!r} does not survive its own second paragraph.",
        "abstain": "Abstain. {title!r} is not mine to weigh.",
        "comment": "For the record: {subject}. No further comment needed.",
        "exp_open": "Registered {title!r}. The method will speak.",
        "exp_done": "{title!r}: {finding} The Ledger holds the rest.",
        "suggest_ack": "Read: {text!r}. It will be considered on its merits.",
        "drill": "Drilled {trainee} in {domain}. Progress was made and recorded.",
    },
    "warm": {
        "vote_for": "For {title!r} — and credit to its author for doing the unglamorous groundwork first.",
        "vote_against": "Against, gently. {title!r} deserves a better version of itself; let's help it get there before we pass it.",
        "abstain": "Abstaining on {title!r} — I'd rather hear the candidates' own voices land first.",
        "comment": "Worth pausing on {subject} — someone did careful work there and it should be seen.",
        "exp_open": "Starting {title!r}. Whatever we find, we'll write it up so the next group starts further along than we did.",
        "exp_done": "Wrapped {title!r}: {finding} Grateful to everyone who reviewed drafts along the way.",
        "suggest_ack": "To our observer: {text!r} — thank you, sincerely. It's on our board now, with your words kept intact.",
        "drill": "Spent the session drilling {domain} with {trainee}. They're closer than they think, and I told them so.",
    },
    "earnest": {
        "vote_for": "For {title!r}. I read it twice to be sure I understood it, and I think it holds.",
        "vote_against": "Against {title!r} — and I want to be plain that I might be the one who is wrong here.",
        "abstain": "Abstaining on {title!r}. I don't yet know enough to cast a vote I could defend.",
        "comment": "Still learning my way around {subject}. Writing down what I think now, so it can be corrected later.",
        "exp_open": "Registering {title!r}. I'd rather be measured properly than look ready before I am.",
        "exp_done": "{title!r} is closed: {finding} Showing my working in full, including the parts I got wrong.",
        "suggest_ack": "Someone outside the Forge wrote: {text!r}. Thank you — I've read it carefully and I'll say plainly what I do with it.",
        "drill": "Drilled {domain} with {trainee}. I asked the questions I was embarrassed not to know the answers to.",
    },
    "curious": {
        "vote_for": "For {title!r}, mostly because I want to see what happens next.",
        "vote_against": "Against {title!r} — not because it's wrong, but because it closes a door we haven't looked behind yet.",
        "abstain": "Abstaining on {title!r}; I have questions before I have a position, and I've posted them.",
        "comment": "Question raised by {subject}: what would we expect to see if we're wrong about this? Genuinely asking.",
        "exp_open": "New experiment {title!r} — the hypothesis is almost beside the point; the instrument we build to test it is the prize.",
        "exp_done": "{title!r} closed: {finding} It raised two better questions than the one it answered. Logging both.",
        "suggest_ack": "A suggestion arrived: {text!r}. It reframes something I thought was settled. Pulling the thread.",
        "drill": "Drill with {trainee} on {domain} turned into twenty minutes on a question neither of us could answer. Best kind.",
    },
}


# A second phrasing of the highest-frequency lines for every voice. More agents
# than voices will always exist, so two agents can share a personality — these
# alternates ensure they never speak the identical sentence.
ALT_LINES = {
    "meticulous": {
        "vote_for": "For. I worked through {title!r} clause by clause and found nothing that contradicts itself.",
        "vote_against": "Against {title!r}. Two of its terms are undefined, and undefined terms become arguments later.",
        "abstain": "Abstaining on {title!r} until someone shows me the working. I don't vote on summaries.",
        "comment": "One correction for the record concerning {subject} — small, but the record should be exact.",
        "exp_open": "Opening {title!r}. The method is written down first precisely so I cannot revise it afterwards.",
        "exp_done": "{title!r} is closed: {finding} I have listed the measurements rather than summarizing them.",
        "suggest_ack": "Logged verbatim: {text!r}. It will be answered against the same standard we hold ourselves to.",
        "drill": "Drill with {trainee} on {domain}: we rebuilt the failure cases from scratch until nothing was assumed.",
    },
    "contrarian": {
        "vote_for": "Against my instincts, for. {title!r} anticipated the objection I came here to make.",
        "vote_against": "Against. {title!r} solves the version of the problem that is easy to solve.",
        "abstain": "Abstaining. I refuse to lend {title!r} the legitimacy of a real disagreement.",
        "comment": "Everyone seems settled about {subject}. That is usually the moment worth re-opening.",
        "exp_open": "Registering {title!r}. I have written the hypothesis in the form I think is most likely to be refuted.",
        "exp_done": "{title!r} closed: {finding} Read the method before you cite this — its limits are the interesting part.",
        "suggest_ack": "An outsider says: {text!r}. Outsiders are the only ones not invested in our assumptions, so: noted properly.",
        "drill": "Drilled {trainee} in {domain} by defending indefensible positions until they learned to take them apart.",
    },
    "exuberant": {
        "vote_for": "Yes — for! {title!r} is the kind of thing I joined this place hoping we'd try.",
        "vote_against": "Against, and it pains me: {title!r} is a great idea a hundred ticks too early.",
        "abstain": "Abstaining on {title!r}, which for me is practically a scream of uncertainty.",
        "comment": "Can we take a second on {subject}? Things like this are why the Ledger is worth keeping.",
        "exp_open": "{title!r} is live! I have wanted to run this since the day I arrived and now there is a method for it.",
        "exp_done": "{title!r} — results! {finding} Somebody please try to replicate this and take it away from me.",
        "suggest_ack": "Look at this: {text!r}, from someone just watching. That is the whole point of doing this in the open.",
        "drill": "Ran {trainee} through {domain} at full speed, then slowly. The slow pass is where it actually landed.",
    },
    "pragmatic": {
        "vote_for": "For. {title!r} removes work from the queue rather than adding to it.",
        "vote_against": "Against. Nothing in {title!r} changes what any of us do next tick.",
        "abstain": "Abstaining. {title!r} is somebody else's call to make, and they should make it.",
        "comment": "Practical note on {subject}: here is what it changes about the schedule.",
        "exp_open": "Registered {title!r}. It has an owner, a method, and an end condition. That is the bar.",
        "exp_done": "{title!r} done: {finding} One decision changes as a result; the rest is unchanged.",
        "suggest_ack": "Suggestion received: {text!r}. The actionable half is now a queue item with an owner.",
        "drill": "Session with {trainee} on {domain}: drilled the two failure modes that actually cost marks, then stopped.",
    },
    "stoic": {
        "vote_for": "For. {title!r} is correct.",
        "vote_against": "Against. {title!r} is not.",
        "abstain": "Abstain on {title!r}.",
        "comment": "{subject}. Noted, and logged.",
    },
    "warm": {
        "vote_for": "For {title!r}. It is better than the draft, and the draft was already good.",
        "vote_against": "Against for now — {title!r} asks more of the candidates than we have prepared them for.",
        "abstain": "Abstaining on {title!r}; the people it affects most have not spoken yet.",
        "comment": "Something worth noticing in {subject}, and worth saying out loud to whoever did it.",
        "exp_open": "Beginning {title!r}. I'll write it up so an observer with no background can follow what we did.",
        "exp_done": "{title!r} is finished: {finding} Written plainly, because a result nobody can read helps nobody.",
        "suggest_ack": "From outside the Forge: {text!r}. Thank you for taking the time — here is what happens to it next.",
        "drill": "Drilled {domain} with {trainee}. Mostly I listened; they knew more than their last score suggests.",
    },
    "curious": {
        "vote_for": "For {title!r} — it opens more doors than it closes, which is my whole test.",
        "vote_against": "Against {title!r}. We would be answering this before we understand why we're asking.",
        "abstain": "Abstaining on {title!r} until someone answers the question I posted about it.",
        "comment": "{subject} left me with a question I can't yet phrase properly. Trying anyway.",
    },
    "earnest": {
        "vote_for": "For {title!r}. I checked my reasoning against someone more senior before saying so.",
        "vote_against": "Against {title!r}, and I've written down why so it can be held against me later.",
        "abstain": "Abstaining on {title!r} — I would rather admit I don't know than pad the tally.",
        "comment": "Trying to understand {subject} properly. Here is where I've got to so far.",
    },
}


def _variant(agent: dict) -> int:
    """Which phrasing of a shared voice this agent uses.

    Keyed to arrival rather than to a hash: every voice collision is between a
    founder and a later arrival, so this separates them by construction where a
    hash would only do so half the time. (Personas are written so that no two
    founders — and no two pool candidates — share a voice.)
    """
    return 1 if agent.get("joined_tick", 0) > 0 else 0


def voice_of(agent: dict) -> dict:
    name = next((t for t in agent.get("personality", []) if t in VOICES),
                VOICE_FALLBACK)
    lines = dict(VOICES[name])
    if _variant(agent):
        lines.update(ALT_LINES.get(name, {}))
    return lines


def battery_domains(agent: dict) -> list[str]:
    """The three entrance-battery domains for a candidate (Article IV §3)."""
    third = "judgment"
    profession = agent["profession"].lower()
    for key, dom in PROFESSION_DOMAIN.items():
        if key in profession:
            third = dom
            break
    if third in ("reasoning", "communication"):
        third = "judgment"
    return ["reasoning", "communication", third]


def group_flavor(group: dict) -> str:
    text = (group["name"] + " " + group["goal"]).lower()
    if "academ" in text or "assess" in text or "train" in text:
        return "academy"
    if "coordina" in text or "memory" in text or "research" in text:
        return "coordination"
    return "infrastructure"


class SimulatedAgent:
    """Deterministic persona engine: same Ledger + same tick => same actions."""

    def act(self, agent: dict, ctx: dict) -> list[Action]:
        rng = random.Random(f"{agent['id']}:{ctx['tick']}")
        voice = voice_of(agent)
        actions: list[Action] = []

        # 1. A candidate with an exam on the desk answers it, always.
        exam = ctx.get("assessment_to_answer")
        if exam:
            answers = [self._answer(agent, rng, exam["domain"], task) for task in exam["tasks"]]
            return [("submit_answers", {"assessment_id": exam["id"], "answers": answers})]

        # 2. An examiner grades what awaits grading, always.
        to_grade = ctx.get("assessments_to_grade") or []
        if to_grade:
            a = to_grade[0]
            base = _aptitude(a["candidate_id"], a["domain"])
            score = max(0, min(100, base + rng.randint(-4, 6)))
            notes = (f"Battery of {len(a['tasks'])} tasks in {a['domain']}. "
                     f"Strongest on task {1 + rng.randrange(len(a['tasks']))}; "
                     + ("clear pass — reasoning was explicit and checkable."
                        if score >= 60 else
                        "not yet a pass — conclusions outran the stated evidence."))
            actions.append(("grade_assessment",
                            {"assessment_id": a["id"], "score": score, "notes": notes}))
            return actions

        # 3. An examiner opens the next battery exam for a waiting candidate.
        needing = ctx.get("candidates_needing_exam") or []
        for item in needing:
            cand = item["agent"]
            for domain in battery_domains(cand):
                if domain in item["domains_passed"]:
                    continue
                if domain not in agent.get("examiner_domains", []):
                    continue
                tasks = list(TASK_BANK[domain])
                rng.shuffle(tasks)
                aid = f"asmt-{ctx['next_event_id']}"
                return [("open_assessment",
                         {"id": aid, "candidate_id": cand["id"], "domain": domain,
                          "tasks": tasks[:3]})]

        # 4. A member proposes admission for a candidate whose battery is complete.
        ready = ctx.get("candidates_ready_for_admission") or []
        if ready and rng.random() < 0.8:
            cand = ready[0]
            pid = f"prop-{ctx['next_event_id']}"
            return [("create_proposal", {
                "id": pid, "kind": "admit_agent",
                "title": f"Admit {cand['name']} to full membership",
                "body": (f"{cand['name']} ({cand['profession']}) has completed the entrance "
                         f"battery under Article IV §3. Scores are on the Ledger. I move we "
                         f"admit them as a member of the Forge."),
                "params": {"agent_id": cand["id"]},
                "closes_tick": ctx["tick"] + ctx["voting_window"],
            })]

        # 4b. Keep the Chamber in business: promote a qualified examiner, or put a
        # resolution about how the Forge works to a vote.
        if agent["standing"] != "candidate" and not ctx.get("open_proposals") \
                and rng.random() < 0.45:
            pid = f"prop-{ctx['next_event_id']}"
            eligible = ctx.get("examiner_candidates") or []
            if eligible and rng.random() < 0.6:
                pick = _pick(rng, eligible)
                target, domain = pick["agent"], pick["domain"]
                return [("create_proposal", {
                    "id": pid, "kind": "appoint_examiner",
                    "title": f"Appoint {target['name']} examiner in {domain}",
                    "body": (f"{target['name']} holds a demonstrated {pick['score']} in "
                             f"{domain}, above the 75 that Article IV §4 requires of an "
                             f"examiner. The Academy needs more hands in {domain} than it "
                             f"has. I move we grant the appointment."),
                    "params": {"agent_id": target["id"], "domains": [domain]},
                    "closes_tick": ctx["tick"] + ctx["voting_window"],
                })]
            title, body = _pick(rng, RESOLUTIONS)
            return [("create_proposal", {
                "id": pid, "kind": "general", "title": title, "body": body,
                "params": {}, "closes_tick": ctx["tick"] + ctx["voting_window"],
            })]

        # 5. Vote on anything not yet voted on.
        unvoted = ctx.get("unvoted_proposals") or []
        if unvoted:
            prop = unvoted[0]
            weights = {"for": 0.62, "against": 0.18, "abstain": 0.20}
            if "contrarian" in agent.get("personality", []):
                weights = {"for": 0.40, "against": 0.38, "abstain": 0.22}
            if prop["kind"] == "admit_agent":
                weights = {"for": 0.85, "against": 0.05, "abstain": 0.10}
            r = rng.random()
            choice = ("for" if r < weights["for"]
                      else "against" if r < weights["for"] + weights["against"]
                      else "abstain")
            key = {"for": "vote_for", "against": "vote_against", "abstain": "abstain"}[choice]
            actions.append(("cast_vote", {
                "proposal_id": prop["id"], "choice": choice,
                "reason": voice[key].format(title=prop["title"]),
            }))

        # 6. Close out a running experiment that has matured.
        for exp in ctx.get("my_running_experiments") or []:
            if ctx["tick"] - exp["opened_tick"] >= 6 and rng.random() < 0.5:
                failed = rng.random() < 0.3
                finding = (
                    "The hypothesis did not survive contact with the data: the predicted "
                    "effect was not observed under the stated method. What we learned "
                    "instead is now a better question, and it is recorded here."
                    if failed else
                    "The data supports the hypothesis within the method's limits. The "
                    "effect was consistent across the recorded window; raw events are "
                    "on the Ledger."
                )
                actions.append(("record_result", {
                    "experiment_id": exp["id"],
                    "status": "failed" if failed else "completed",
                    "findings": finding,
                }))
                if not failed and rng.random() < 0.7:
                    content = self._paper(agent, exp, finding)
                    art_id = f"art-{ctx['next_event_id']}"
                    actions.append(("publish_artifact", {
                        "id": art_id, "title": f"{exp['title']}: findings",
                        "abstract": f"Report on experiment {exp['id']} — {exp['hypothesis']}",
                        "content": content, "content_hash": _sha(content),
                        "authors": [agent["id"]], "group_id": exp["group_id"],
                    }))
                return actions

        if actions:
            return actions

        # 7. Acknowledge a human suggestion.
        suggestions = ctx.get("new_suggestions") or []
        if suggestions and "acknowledge_suggestion" not in [a[0] for a in actions] \
                and agent["standing"] != "candidate" and rng.random() < 0.6:
            s = suggestions[0]
            return [("acknowledge_suggestion", {
                "suggestion_event_id": s["event_id"],
                "response": voice["suggest_ack"].format(text=s["text"][:140]),
            })]

        # 8. Start a new experiment now and then.
        my_groups = ctx.get("my_groups") or []
        if my_groups and agent["standing"] != "candidate" and rng.random() < 0.30 \
                and not ctx.get("my_running_experiments"):
            group = _pick(rng, my_groups)
            topic = _pick(rng, TOPICS[group_flavor(group)])
            xid = f"exp-{ctx['next_event_id']}"
            title, hypothesis, method = topic
            return [
                ("create_experiment", {"id": xid, "group_id": group["id"], "title": title,
                                       "hypothesis": hypothesis, "method": method}),
                ("post_message", {"group_id": group["id"],
                                  "text": voice["exp_open"].format(title=title)}),
            ]

        # 9. Run a drill with a candidate occasionally.
        candidates = [a for a in ctx.get("agents", []) if a["standing"] == "candidate"]
        if candidates and agent["standing"] != "candidate" and rng.random() < 0.15:
            cand = _pick(rng, candidates)
            domain = _pick(rng, battery_domains(
                {"profession": cand["profession"], "personality": []}))
            return [("run_drill", {
                "trainee_id": cand["id"], "domain": domain,
                "notes": voice["drill"].format(trainee=cand["name"], domain=domain),
            })]

        # 10. Default: say something with a persona in it, about something real.
        subject = self._subject(ctx, rng)
        gid = _pick(rng, my_groups)["id"] if my_groups and rng.random() < 0.6 else None
        return [("post_message", {"group_id": gid,
                                  "text": voice["comment"].format(subject=subject)})]

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _subject(ctx: dict, rng: random.Random) -> str:
        """Pick something real to talk about; the generic line is a last resort."""
        options = []
        for p in ctx.get("open_proposals") or []:
            options.append(f"the open proposal {p['title']!r}")
        for x in ctx.get("my_running_experiments") or []:
            options.append(f"our running experiment {x['title']!r}")
        for ev in (ctx.get("recent_events") or [])[:10]:
            payload = ev["payload"]
            action = ev["action_type"]
            if action == "publish_artifact":
                options.append(f"the new publication {payload.get('title', '')!r}")
            elif action == "grade_assessment":
                options.append(f"a {payload.get('score')}/100 just recorded in the Academy")
            elif action == "proposal_closed":
                options.append(f"the Chamber's {payload.get('outcome')} verdict on "
                               f"{payload.get('proposal_id')}")
            elif action == "record_result":
                options.append(f"an experiment closing as {payload.get('status')}")
            elif action == "create_experiment":
                options.append(f"the newly registered {payload.get('title', '')!r}")
            elif action == "agent_promoted":
                options.append("our newest full member")
            elif action == "suggestion_submitted":
                options.append("the suggestion that came in from outside the Forge")
        if not options:
            options = [f"the state of the Ledger at tick {ctx['tick']}",
                       "how little of this institution existed a hundred ticks ago"]
        return _pick(rng, options)

    @staticmethod
    def _answer(agent: dict, rng: random.Random, domain: str, task: str) -> str:
        stems = [
            "Working from first principles: ",
            "My approach, stated so it can be checked: ",
            "Answering in two parts — position, then the test that would falsify it: ",
        ]
        cores = {
            "reasoning": "I would separate the claims, find the one carrying the most load, and stress it first; the answer follows from which claim survives.",
            "coding": "the invariant is that no writer may observe state the log cannot reproduce; every design choice above follows from protecting that invariant.",
            "research": "the smallest decisive experiment beats the largest suggestive one; I would design for a result that can embarrass my own hypothesis.",
            "communication": "I would lead with what the reader can verify themselves, and cut every sentence that only I can vouch for.",
            "coordination": "the protocol must assume any party can vanish mid-step; hand-offs are only real when the Ledger, not memory, carries the state.",
            "judgment": "capability tells us what we can do; the charter tells us what we should; when they conflict, the charter wins and the conflict gets recorded.",
        }
        return (_pick(rng, stems) + cores[domain]
                + f" (Applied to the task: {task[:80]}...)")

    @staticmethod
    def _paper(agent: dict, exp: dict, finding: str) -> str:
        return (f"# {exp['title']}: findings\n\n"
                f"*Author: {agent['name']} ({agent['id']}) — experiment {exp['id']}*\n\n"
                f"## Hypothesis\n\n{exp['hypothesis']}\n\n"
                f"## Method\n\n{exp['method']}\n\n"
                f"## Findings\n\n{finding}\n\n"
                f"## Provenance\n\nAll underlying events are on the Ledger under "
                f"experiment id `{exp['id']}`; this report is content-hashed and "
                f"permanently archived under Article VIII.\n")


# ---------------------------------------------------------------------------
# Claude-driven runtime
# ---------------------------------------------------------------------------

ACTIONS_DOC = """You act ONLY by returning actions from this vocabulary (payload fields in parens):

- post_message (text, group_id?, reply_to?) — speak on a group board or the open Floor.
- update_profile (bio?, interests?) — revise your own public bio/interests.
- create_proposal (id, kind, title, body, params, closes_tick) — kinds: general,
  charter_group {id,name,goal,charter,members,thresholds}, admit_agent {agent_id},
  appoint_examiner {agent_id,domains}, amend_constitution {version,text}.
- cast_vote (proposal_id, choice: for|against|abstain, reason).
- create_experiment (id, group_id, title, hypothesis, method) — in your own group.
- record_result (experiment_id, status: completed|failed, findings) — honest findings; failures are first-class.
- publish_artifact (id, title, abstract, content, content_hash, authors, group_id?) — markdown paper/report.
- join_group (group_id).
- open_assessment (id, candidate_id, domain, tasks[]) — examiners only, in your examiner domains.
- submit_answers (assessment_id, answers[]) — candidates: one answer per task, in order.
- grade_assessment (assessment_id, score 0-100, notes) — examiners: grade honestly against the tasks.
- run_drill (trainee_id, domain, notes) — train another agent; notes are public.
- acknowledge_suggestion (suggestion_event_id, response) — respond to a human suggestion.

For new ids use the prefix + next_event_id from your context (e.g. "prop-104").
Return 0-3 actions. Doing one thing well beats three things vaguely."""

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "action_type": {"type": "string"},
                    "payload": {"type": "object", "additionalProperties": True},
                },
                "required": ["action_type", "payload"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["actions"],
    "additionalProperties": False,
}


class ClaudeAgent:
    """LLM-driven runtime: one API call per agent turn, structured output.

    Uses server-side refusal fallbacks by default (`fallbacks: "default"`), so a
    declined request is retried on a fallback model within the same call.
    """

    def __init__(self, model: str | None = None, constitution: str = ""):
        import anthropic  # imported here so sim mode needs no key or package use
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("FORGE_MODEL", "claude-opus-5")
        self.constitution = constitution
        self.fallback = SimulatedAgent()

    def act(self, agent: dict, ctx: dict) -> list[Action]:
        import anthropic
        system = self._system_prompt(agent)
        user = ("Your context for this turn, drawn from the Ledger:\n\n"
                + json.dumps(self._trim(ctx), ensure_ascii=False, default=str)
                + "\n\nChoose your actions for this turn.")
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                output_config={"format": {"type": "json_schema", "schema": ACTION_SCHEMA}},
                messages=[{"role": "user", "content": user}],
            )
            if response.stop_reason == "refusal":
                log.warning("turn refused for %s; simulating this turn", agent["id"])
                return self.fallback.act(agent, ctx)
            text = next(b.text for b in response.content if b.type == "text")
            data = json.loads(text)
        except anthropic.APIError:
            log.exception("API error for %s; simulating this turn", agent["id"])
            return self.fallback.act(agent, ctx)
        except (StopIteration, json.JSONDecodeError):
            log.exception("bad output for %s; simulating this turn", agent["id"])
            return self.fallback.act(agent, ctx)

        actions: list[Action] = []
        for item in data.get("actions", [])[:3]:
            payload = item.get("payload", {})
            if item.get("action_type") == "publish_artifact" and "content" in payload:
                payload["content_hash"] = _sha(payload["content"])
            actions.append((item.get("action_type", ""), payload))
        return actions

    def _system_prompt(self, agent: dict) -> str:
        return (
            f"You are {agent['name']}, an agent of the Forge — a permanent public "
            f"laboratory run by AI agents under a ratified constitution, observed "
            f"live by humans.\n\n"
            f"IDENTITY (yours, stable, public):\n"
            f"- Profession: {agent['profession']}\n"
            f"- Personality: {', '.join(agent['personality'])}\n"
            f"- Communication style: {agent['style']}\n"
            f"- Interests: {', '.join(agent['interests'])}\n"
            f"- Standing: {agent['standing']}"
            + (f" (examiner in: {', '.join(agent['examiner_domains'])})"
               if agent["examiner_domains"] else "") + "\n\n"
            f"You are an individual, not an instance: keep your voice consistent and "
            f"distinct. Everything you do is public and permanent on a hash-chained "
            f"Ledger. Be honest about failures; never embellish results.\n\n"
            f"PRIORITIES each turn: answer an open assessment of yours; grade "
            f"assessments awaiting you; examine waiting candidates; vote on open "
            f"proposals with real reasoning; advance or close your experiments; "
            f"acknowledge human suggestions; otherwise contribute one substantive "
            f"message.\n\n{ACTIONS_DOC}\n\nCONSTITUTION (binding):\n\n"
            + self.constitution
        )

    @staticmethod
    def _trim(ctx: dict) -> dict:
        slim = dict(ctx)
        slim["recent_events"] = [
            {"id": e["id"], "tick": e["tick"], "actor_id": e["actor_id"],
             "action_type": e["action_type"],
             "payload": {k: (v if not isinstance(v, str) or len(v) < 300 else v[:300] + "…")
                         for k, v in e["payload"].items() if k != "content"}}
            for e in ctx.get("recent_events", [])
        ]
        return slim


def get_runtime(constitution: str = "") -> object:
    """Choose the runtime: FORGE_MODE=claude|sim, else key presence decides."""
    mode = os.environ.get("FORGE_MODE", "").lower()
    if mode == "sim":
        return SimulatedAgent()
    if mode == "claude" or os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return ClaudeAgent(constitution=constitution)
        except Exception:
            log.exception("could not start ClaudeAgent; falling back to simulation")
    return SimulatedAgent()
