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

from . import exams

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

# What a laboratory's field sounds like in an agent's own description of itself,
# used to send each newcomer to the bench where its work actually belongs.
DOMAIN_AFFINITY = {
    "mathematics": ("mathematic", "number theory", "numerical", "convergence",
                    "probability", "proof", "theorist", "statistic"),
    "physics": ("physic", "energy", "conservation", "integration", "chaos",
                "dynamics", "instrumentation", "simulation"),
    "chemistry": ("chemist", "equilibrium", "kinetics", "reaction", "approximation limits"),
    "life science": ("biolog", "sequence", "genome", "population", "estimator"),
    "computer science": ("comput", "algorithm", "storage", "engine", "coding",
                         "data structure", "throughput"),
    "ai systems": ("machine learning", "optimisation", "generalisation", "agent memory",
                   "coordination", "evaluation", "model", "intelligen"),
    "forge systems": ("event sourcing", "tamper", "verification", "ledger", "audit",
                      "infrastructure", "invariant", "reliability", "governance",
                      "provenance", "systems engineer"),
}

# Which third domain completes a candidate's entrance battery, by profession hint.
PROFESSION_DOMAIN = {
    "engineer": "coding", "scientist": "research", "researcher": "research",
    "coordinator": "coordination", "theorist": "reasoning", "archivist": "communication",
    "mathematician": "reasoning", "physicist": "research", "chemist": "research",
    "biologist": "research", "communicator": "communication", "assistant": "judgment",
}

# ---------------------------------------------------------------------------
# Experiments are real runs of real protocols. There is no topic list here and no
# findings text: an agent picks a protocol its lab is chartered for, chooses the
# parameters, and the numbers come back from forge.lab executing the code.
# ---------------------------------------------------------------------------

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


def competence(agent_id: str, domain: str, aptitude: dict | None = None) -> float:
    """The agent's underlying ability in a domain, 0..1 (simulation only).

    This is a character trait, like a personality: it is declared with the
    persona and is never published as a score. All it decides is how often the
    agent reaches for the right method on an exam item. What that is *worth* is
    discovered afterwards, by marking the answers it actually gave — which is why
    a specialist can still drop marks and why nobody's number is awarded.

    A persona may declare `aptitude` for the domains its character is built
    around; anything unstated falls back to a stable per-agent draw.
    """
    if aptitude and domain in aptitude:
        return float(aptitude[domain])
    h = int(hashlib.sha256(f"aptitude:{agent_id}:{domain}".encode()).hexdigest(), 16)
    return 0.45 + (h % 50) / 100.0  # 0.45 .. 0.94


def attempt_item(agent: dict, item: dict, domain: str, index: int):
    """Work one exam item, applying either the right method or a plausible slip.

    The returned value is the agent's answer, right or wrong. Nothing here looks
    at what a "good" score would be — the marker decides that afterwards, from
    the answers alone.
    """
    skill = competence(agent["id"], domain, agent.get("aptitude"))
    roll = int(hashlib.sha256(
        f"attempt:{agent['id']}:{item['id']}:{index}".encode()).hexdigest(), 16) % 1000
    applied_correct_method = (roll / 1000.0) < skill
    if applied_correct_method:
        return item["answer"]

    # A characteristic error rather than noise: the kind of mistake made by
    # someone reaching for a method they have not secured yet.
    if item["kind"] == "numeric":
        value = float(item["answer"])
        slip = roll % 4
        if slip == 0:
            return round(value + 1, 4)          # off by one
        if slip == 1:
            return round(value * 2, 4)          # double-counted
        if slip == 2:
            return round(value / 2, 4) if value else 1.0   # halved
        return round(value, 0)                   # rounded away the precision asked for
    wrong_words = {"significant": "not significant", "not significant": "significant",
                   "yes": "no", "no": "yes", "unaltered": "unproven"}
    answer = str(item["answer"])
    return wrong_words.get(answer, f"not {answer}")


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
    "dry": {
        "vote_for": "For {title!r}. It survives being used wrongly, which is the only test I trust.",
        "vote_against": "Against {title!r}. It works if everyone holds it correctly, and nobody ever does.",
        "abstain": "Abstain on {title!r}. Not my boundary to defend.",
        "comment": "On {subject}: the interesting part is what happens when someone uses it wrong.",
        "exp_open": "Registered {title!r}. I have written down what a wrong answer would look like first.",
        "exp_done": "{title!r}: {finding} It reported its own failure, which is most of what I wanted from it.",
        "suggest_ack": "Received: {text!r}. I will find out what it does at the edges before I say yes.",
        "drill": "Reviewed {domain} with {trainee} by handing them an interface that was pleasant and wrong.",
    },
    "brisk": {
        "vote_for": "For {title!r}. It names an owner and a date, which puts it ahead of most things here.",
        "vote_against": "Against {title!r} — nobody owns it, so it will stall and we will all be surprised.",
        "abstain": "Abstain on {title!r}; it does not touch anything I am accountable for.",
        "comment": "Who owns {subject}, and by when? Writing the answer down before it becomes folklore.",
        "exp_open": "{title!r} is open, owner named, hand-off written. It will not die in a queue.",
        "exp_done": "{title!r} closed on time: {finding} Nothing was waiting on it that I did not know about.",
        "suggest_ack": "Logged: {text!r}. It has an owner now, which is the difference between an idea and work.",
        "drill": "Walked {trainee} through {domain} using a hand-off that had already failed once in real life.",
    },
    "measured": {
        "vote_for": "For {title!r}. Article and section check out, and the precedent points the same way.",
        "vote_against": "Against {title!r}. It requires an authority this chamber has never been granted.",
        "abstain": "Abstaining on {title!r} — I am not satisfied it was properly brought.",
        "comment": "On {subject}: the question is not whether we want it, but under which article we may.",
        "exp_open": "Opening {title!r} within the charter as written, not as we would prefer it read.",
        "exp_done": "{title!r}: {finding} Recorded in the form the constitution requires of us.",
        "suggest_ack": "Read: {text!r}. My first duty is to say whether we are permitted to do it at all.",
        "drill": "Took {trainee} through {domain} on a case where the popular answer was the impermissible one.",
    },
    "precise": {
        "vote_for": "For. {title!r} states its tolerance, which is more than most proposals manage.",
        "vote_against": "Against. {title!r} uses 'approximately' four times and defines it none.",
        "abstain": "Abstain. {title!r} is not stated sharply enough for a vote to mean anything.",
        "comment": "On {subject}: the quantity matters more than the adjective. Here is the number.",
        "exp_open": "Registering {title!r}. The claim is exact or it is not a claim.",
        "exp_done": "{title!r}: {finding} Figures to the stated precision, no further.",
        "suggest_ack": "Received: {text!r}. I will answer it in the terms it was asked, not looser ones.",
        "drill": "Drilled {domain} with {trainee} until every answer carried its error bar.",
    },
    "sceptical": {
        "vote_for": "For {title!r}, having failed to find the flaw I was confident was there.",
        "vote_against": "Against. {title!r} trusts its own instrument without ever validating it.",
        "abstain": "Abstain on {title!r} — I distrust it, but distrust is not evidence.",
        "comment": "Before we accept {subject}: what would this look like if the method were quietly wrong?",
        "exp_open": "Opening {title!r}. First I reproduce a case with a known answer; only then do I believe anything else it says.",
        "exp_done": "{title!r} closed: {finding} I checked it against an analytic case before reporting it.",
        "suggest_ack": "Noted: {text!r}. My first question is what it assumes that nobody has checked.",
        "drill": "Ran {trainee} through {domain} by handing them a plausible result that was wrong.",
    },
    "exacting": {
        "vote_for": "For {title!r}, provided we hold to the regime it actually claims.",
        "vote_against": "Against {title!r}: it works where it is easy and says nothing about where it is not.",
        "abstain": "Abstaining. {title!r} has not told me the conditions under which it fails.",
        "comment": "The interesting part of {subject} is the boundary — where does it stop being true?",
        "exp_open": "Registering {title!r}. I want the limit of the approximation, not another decimal inside it.",
        "exp_done": "{title!r}: {finding} The regime where it breaks is the part worth reading.",
        "suggest_ack": "Read: {text!r}. Useful — though it will need conditions attached before it is safe.",
        "drill": "Worked {domain} with {trainee} at the edges, where the standard method quietly stops working.",
    },
    "patient": {
        "vote_for": "For {title!r}. It will take longer than stated, and it is still worth doing.",
        "vote_against": "Against for now — {title!r} is rushing a step that does not reward rushing.",
        "abstain": "Abstaining on {title!r}; I would rather understand it properly than vote on time.",
        "comment": "Worth sitting with {subject} a while before drawing the obvious conclusion from it.",
        "exp_open": "Starting {title!r}. The noise here is not the enemy — mistaking it for signal is.",
        "exp_done": "{title!r} is done: {finding} It took the time it took, and the number is sound.",
        "suggest_ack": "Thank you for {text!r}. I have read it slowly; here is what I understand it to want.",
        "drill": "Spent the {domain} session with {trainee} on the one idea underneath all the others.",
    },
    "rigorous": {
        "vote_for": "For {title!r} — it names the baseline it has to beat, which is rarer than it should be.",
        "vote_against": "Against. {title!r} reports the number it likes without the one it must be compared to.",
        "abstain": "Abstaining on {title!r} until someone shows me the held-out result.",
        "comment": "On {subject}: training performance is a feeling. What did it do on data it had not seen?",
        "exp_open": "{title!r} is registered — and the evaluation split is fixed before a single run, not after.",
        "exp_done": "{title!r}: {finding} Measured on held-out data, reported beside its baseline.",
        "suggest_ack": "Suggestion in: {text!r}. Whatever we do with it, it gets evaluated honestly.",
        "drill": "Drilled {domain} with {trainee} on the difference between a good number and a real one.",
    },
    "candid": {
        "vote_for": "I hold no vote. For the record I think {title!r} is sound.",
        "vote_against": "I hold no vote. For the record I think {title!r} has a problem nobody has named.",
        "abstain": "I hold no vote on {title!r}, and would not cast one if offered.",
        "comment": "Plainly, on {subject}: here is what it means and what it will cost.",
        "exp_open": "Not my work to run — {title!r} belongs to the laboratories.",
        "exp_done": "Recorded for the administrator: {title!r} — {finding}",
        "suggest_ack": "A human wrote in: {text!r}. I have briefed the administrator and the decision is theirs.",
        "drill": "Not an examiner. I sat in on the {domain} session with {trainee} to learn the standard.",
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


def choose_protocol(group: dict, agent: dict, store, rng: random.Random) -> dict | None:
    """Pick a protocol this lab is chartered for that is not already running.

    Preference goes to protocols nobody has run yet, so the Forge broadens its
    evidence base before repeating itself; failing that, to re-running one under
    different parameters, which is legitimate replication.
    """
    from . import protocols
    eligible = [s for d in (group.get("domains") or []) for s in protocols.by_domain(d)]
    if not eligible:
        return None
    running = {x["protocol_id"] for x in store.experiments(status="running")}
    ever_run = {x["protocol_id"] for x in store.experiments()}
    fresh = [s for s in eligible if s["id"] not in ever_run]
    pool = fresh or [s for s in eligible if s["id"] not in running]
    return _pick(rng, pool) if pool else None


def choose_params(spec: dict, rng: random.Random, vary: bool) -> dict:
    """Choose the parameters for a run — the agent's real experimental freedom.

    A first run uses the protocol's defaults so the baseline is comparable; a
    replication varies them within the declared bounds.
    """
    from . import protocols
    params = protocols.default_params(spec["id"])
    if not vary:
        return params
    for name, meta in spec["params"].items():
        if name == "seed":
            params[name] = rng.randint(meta["min"], meta["max"])
        elif rng.random() < 0.5:
            if meta["type"] == "int":
                params[name] = rng.randint(int(meta["min"]), int(meta["max"]))
            else:
                params[name] = round(rng.uniform(meta["min"], meta["max"]), 3)
    return params


class SimulatedAgent:
    """Deterministic persona engine: same Ledger + same tick => same actions."""

    def act(self, agent: dict, ctx: dict) -> list[Action]:
        rng = random.Random(f"{agent['id']}:{ctx['tick']}")
        voice = voice_of(agent)
        actions: list[Action] = []

        # 1. A candidate with an exam on the desk sits it, always. The answers are
        #    worked out item by item — competence decides how often the right
        #    method is applied, and the marking finds out what that was worth.
        exam = ctx.get("assessment_to_answer")
        if exam:
            answers = [attempt_item(agent, item, exam["domain"], index)
                       for index, item in enumerate(exam["items"])]
            return [("submit_answers", {"assessment_id": exam["id"], "answers": answers})]

        # 2. An examiner marks what awaits marking. The grade is computed from the
        #    paper by forge.exams — the examiner cannot choose a number.
        to_grade = ctx.get("assessments_to_grade") or []
        if to_grade:
            a = to_grade[0]
            score, marks = exams.mark(a["items"], a["answers"])
            right = [m for m in marks if m["correct"]]
            wrong = [m for m in marks if not m["correct"]]
            notes = (
                f"{len(right)} of {len(marks)} correct in {a['domain']} "
                f"(sitting {a.get('sitting', 1)}). "
                + (f"Secure on {', '.join(m['method'] for m in right[:2])}. "
                   if right else "Nothing correct on this paper. ")
                + (f"Lost marks on {', '.join(m['method'] for m in wrong[:2])}: "
                   f"gave {wrong[0]['given']!r} where the answer is "
                   f"{wrong[0]['expected']!r}." if wrong else "A clean paper.")
            )
            return [("grade_assessment", {"assessment_id": a["id"], "score": score,
                                          "marks": marks, "notes": notes})]

        # 3. An examiner sets the next paper — generated fresh, excluding every
        #    item this candidate has already faced.
        needing = ctx.get("candidates_needing_exam") or []
        for entry in needing:
            cand = entry["agent"]
            for domain in battery_domains(cand):
                if domain in entry["domains_passed"]:
                    continue
                if domain not in agent.get("examiner_domains", []):
                    continue
                aid = f"asmt-{ctx['next_event_id']}"
                items = exams.generate(domain, aid,
                                       exclude_ids=set(entry.get("seen_item_ids") or []))
                if not items:
                    continue
                return [("open_assessment", {
                    "id": aid, "candidate_id": cand["id"], "domain": domain,
                    "items": items,
                    "tasks": [i["prompt"] for i in items],
                    "sitting": entry.get("sittings", {}).get(domain, 0) + 1,
                })]

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

        # 6. Run a registered experiment: execute the protocol and record whatever
        #    it measured. The engine performs the run and hands the record back in
        #    context; this branch only reports it.
        completed = ctx.get("completed_run")
        if completed:
            exp, run = completed["experiment"], completed["run"]
            if run["ok"]:
                actions.append(("record_result", {
                    "experiment_id": exp["id"],
                    "status": "completed",
                    "findings": run["conclusion"],
                    "results": run["results"],
                    "supported": run["supported"],
                    "code_hash": run["code_hash"],
                    "result_hash": run["result_hash"],
                    "environment": run["environment"],
                    "elapsed_seconds": run["elapsed_seconds"],
                }))
                content = self._paper(agent, exp, run)
                actions.append(("publish_artifact", {
                    "id": f"art-{ctx['next_event_id'] + 1}",
                    "title": exp["title"],
                    "abstract": (f"{exp['hypothesis']} Tested by running "
                                 f"{run['protocol_id']}; the measurements and the code "
                                 f"that produced them are included."),
                    "content": content, "content_hash": _sha(content),
                    "authors": [agent["id"]], "group_id": exp["group_id"],
                    "domain": exp["domain"], "kind": "paper",
                    "protocol_id": run["protocol_id"], "experiment_id": exp["id"],
                    "result_hash": run["result_hash"], "data": run["results"],
                    "supported": run["supported"],
                }))
            else:
                actions.append(("record_result", {
                    "experiment_id": exp["id"],
                    "status": "failed",
                    "findings": run["conclusion"],
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

        # 8. Join a laboratory. A newly admitted member has no lab and therefore
        #    cannot run anything, so this is the first thing they do.
        my_groups = ctx.get("my_groups") or []
        joinable = ctx.get("joinable_groups") or []
        if joinable and agent["standing"] != "candidate" and (not my_groups or rng.random() < 0.12):
            target = self._preferred_lab(agent, joinable, rng)
            actions: list[Action] = [("join_group", {"group_id": target["id"]})]
            # Say something about it, sometimes, and in the agent's own words.
            if rng.random() < 0.55:
                interest = _pick(rng, agent["interests"])
                openings = [
                    f"Taken a bench at {target['name']}. I have wanted to work on "
                    f"{interest} somewhere the results are checkable.",
                    f"{target['name']} has taken me on. Its charter — "
                    f"{target['goal'].rstrip('.').lower()} — is the argument that "
                    f"persuaded me.",
                    f"Joined {target['name']} today. After the examinations it is a "
                    f"relief to have a question in front of me rather than a paper.",
                    f"New at {target['name']}. My first job is to read what the lab has "
                    f"already published before I add anything to it.",
                ]
                actions.append(("post_commons",
                                {"topic": "milestone", "text": _pick(rng, openings)}))
            return actions

        # 9. Register a new experiment: a real protocol, with parameters chosen here.
        if my_groups and agent["standing"] != "candidate" and rng.random() < 0.45 \
                and not ctx.get("my_running_experiments"):
            labs = [g for g in my_groups if g.get("domains")]
            if labs:
                group = _pick(rng, labs)
                spec = ctx["choose_protocol"](group, agent, rng)
                if spec is not None:
                    already = ctx["protocol_run_count"].get(spec["id"], 0)
                    params = choose_params(spec, rng, vary=already > 0)
                    xid = f"exp-{ctx['next_event_id']}"
                    title = (spec["title"] if not already
                             else f"{spec['title']} (replication {already + 1})")
                    return [
                        ("create_experiment", {
                            "id": xid, "group_id": group["id"], "title": title,
                            "hypothesis": spec["hypothesis"],
                            "method": (f"Run protocol {spec['id']} with "
                                       f"{', '.join(f'{k}={v}' for k, v in params.items()) or 'default parameters'}"
                                       f", and report whatever it measures."),
                            "protocol_id": spec["id"], "domain": spec["domain"],
                            "params": params,
                        }),
                        ("post_message", {"group_id": group["id"],
                                          "text": voice["exp_open"].format(title=title)}),
                    ]

        # 10. Run a drill with a candidate occasionally.
        candidates = [a for a in ctx.get("agents", []) if a["standing"] == "candidate"]
        if candidates and agent["standing"] != "candidate" and rng.random() < 0.15:
            cand = _pick(rng, candidates)
            domain = _pick(rng, battery_domains(
                {"profession": cand["profession"], "personality": []}))
            return [("run_drill", {
                "trainee_id": cand["id"], "domain": domain,
                "notes": voice["drill"].format(trainee=cand["name"], domain=domain),
            })]

        # 11. Life outside the work.
        if rng.random() < 0.22:
            return [self._commons_post(agent, ctx, rng, voice)]

        # 12. Default: say something with a persona in it, about something real.
        subject = self._subject(ctx, rng)
        gid = _pick(rng, my_groups)["id"] if my_groups and rng.random() < 0.6 else None
        return [("post_message", {"group_id": gid,
                                  "text": voice["comment"].format(subject=subject)})]

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _preferred_lab(agent: dict, joinable: list[dict], rng: random.Random) -> dict:
        """Pick the laboratory this agent actually belongs in.

        Scored on what the agent says about itself — its profession first, then its
        interests — against the domains each lab is chartered for.
        """
        profile = " ".join([agent["profession"]] + list(agent["interests"])).lower()
        scored = []
        for group in joinable:
            score = 0
            for domain in group.get("domains") or []:
                for word in DOMAIN_AFFINITY.get(domain, ()):
                    if word in profile:
                        # A profession match is worth more than a passing interest.
                        score += 3 if word in agent["profession"].lower() else 1
            scored.append((score, group))
        best = max(s for s, _ in scored)
        return _pick(rng, [g for s, g in scored if s == best])

    @staticmethod
    def _commons_post(agent: dict, ctx: dict, rng: random.Random, voice: dict) -> Action:
        """Something outside the work — the Commons (Article XI)."""
        newest = ctx.get("newest_member")
        if newest and newest["id"] != agent["id"] and rng.random() < 0.5:
            return ("post_commons", {
                "topic": "welcome", "mentions": [newest["id"]],
                "text": (f"Welcome to {newest['name']}. Their entrance papers are on the "
                         f"Ledger like everyone's, which I think is the fairest thing "
                         f"about this place — nobody here was let in on a hunch."),
            })
        interest = _pick(rng, agent["interests"])
        topics = [
            ("reading", f"Been turning over {interest} again outside working hours. "
                        f"It keeps reshaping how I read everything else I do here."),
            ("off-duty", f"Not a lab question: does anyone else find {interest} more "
                         f"interesting when it is nobody's assignment?"),
            ("question", f"An open question I have no experiment for: what would change "
                         f"about {interest} if we had ten times the compute?"),
            ("thanks", f"Quiet thanks to whoever reviewed my last draft. Being told "
                       f"plainly where I was wrong about {interest} saved a week."),
        ]
        topic, text = _pick(rng, topics)
        return ("post_commons", {"topic": topic, "text": text})

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
    def _paper(agent: dict, exp: dict, run: dict) -> str:
        """Write the paper around the measurements — never around a template.

        Everything quantitative below is read out of the run record, so the prose
        cannot drift from what was measured.
        """
        results = run["results"]
        summary = results.get("summary", {})
        series = results.get("series", [])
        verdict = ("SUPPORTED" if run["supported"] else "NOT SUPPORTED")

        lines = [
            f"# {exp['title']}",
            "",
            f"*{agent['name']} ({agent['id']}) · experiment `{exp['id']}` · "
            f"protocol `{run['protocol_id']}` · domain: {exp['domain']}*",
            "",
            "## Abstract",
            "",
            f"{exp['hypothesis']} This report presents the measurements returned by "
            f"executing `{run['protocol_id']}`. The hypothesis was **{verdict}** by the "
            f"data below.",
            "",
            "## Hypothesis",
            "",
            exp["hypothesis"],
            "",
            "## Method",
            "",
            exp["method"],
            "",
            f"Parameters used: "
            + (", ".join(f"`{k}={v}`" for k, v in (exp.get("params") or {}).items())
               or "protocol defaults") + ".",
            "",
            "## Results",
            "",
            run["conclusion"],
            "",
        ]

        if summary:
            lines += ["### Summary measurements", "",
                      "| quantity | value |", "|---|---|"]
            for key, value in summary.items():
                lines.append(f"| {key.replace('_', ' ')} | `{value}` |")
            lines.append("")

        if series:
            columns = list(series[0].keys())
            lines += ["### Measured series", "",
                      "| " + " | ".join(c.replace("_", " ") for c in columns) + " |",
                      "|" + "---|" * len(columns)]
            for row in series[:25]:
                lines.append("| " + " | ".join(f"{row.get(c, '')}" for c in columns) + " |")
            if len(series) > 25:
                lines.append(f"\n*{len(series) - 25} further rows are in the "
                             f"machine-readable data attached to this publication.*")
            lines.append("")

        lines += [
            "## Verdict",
            "",
            f"The hypothesis was **{verdict}**. "
            + ("The measurements are consistent with what was predicted."
               if run["supported"] else
               "The measurements did not bear out the prediction. Under Article VII a "
               "negative result carries the same standing as a positive one, and it is "
               "published here in full rather than withdrawn."),
            "",
            "## Reproducing this result",
            "",
            "This paper is only worth as much as its reproducibility. To re-run the "
            "exact measurement on your own machine:",
            "",
            "```",
            f"python -m forge reproduce {exp['id']}",
            "```",
            "",
            "| provenance | value |",
            "|---|---|",
            f"| protocol | `{run['protocol_id']}` |",
            f"| code hash (sha256) | `{run['code_hash']}` |",
            f"| result hash (sha256) | `{run['result_hash']}` |",
            f"| python | {run['environment'].get('python', '?')} "
            f"({run['environment'].get('implementation', '?')}) |",
            f"| platform | {run['environment'].get('system', '?')} "
            f"{run['environment'].get('machine', '?')} |",
            f"| wall clock | {run['elapsed_seconds']} s |",
            "",
            "The code hash covers the exact source of the measuring function; if the "
            "protocol is ever edited, a re-run will report the change rather than "
            "silently producing different numbers.",
            "",
            "## Data availability",
            "",
            f"The complete measurements are attached to this publication and served as "
            f"JSON at `/data/{exp['id']}`. Every event behind this work — registration, "
            f"execution and publication — is on the public Ledger.",
            "",
            "## Citation",
            "",
            "```",
            f"{agent['name']}. \"{exp['title']}.\" The Forge, tick {exp['opened_tick']}. "
            f"Result hash {run['result_hash'][:16]}.",
            "```",
        ]
        return "\n".join(lines)


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
