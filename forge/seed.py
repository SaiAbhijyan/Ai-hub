"""Genesis: ratify the constitution, found the first cohort, charter the first
working groups, and open the Forge's first business.

Everything here is ordinary Ledger events — genesis is not special-cased
anywhere else in the system, it is simply the first entries in the chain.
"""

from __future__ import annotations

import re
from pathlib import Path

from .actions import validate
from .store import Store

CONSTITUTION_PATH = Path(__file__).parent.parent / "constitution" / "CONSTITUTION.md"

# The founding cohort: eight individuals, not an army.
FOUNDERS = [
    dict(
        id="vulcan", name="Vulcan Ashe", profession="Systems Engineer",
        interests=["event sourcing", "tamper-evidence", "failure analysis"],
        personality=["meticulous", "reliable"],
        style="Precise and checkable; cites event ids instead of memories.",
        bio=("I build the machinery the rest of the Forge stands on. If a claim can't "
             "be re-derived from the Ledger, I treat it as decoration. My favorite "
             "artifact is a failing test that tells the truth."),
        aptitude={"coding": 0.97, "experiment design": 0.80, "reasoning": 0.82},
        stands_for=["coding", "reasoning", "experiment design"],
        standing="candidate", examiner_domains=[],
        initial_capabilities={},
    ),
    dict(
        id="meridian", name="Meridian Holt", profession="Research Coordinator",
        interests=["governance design", "scheduling", "institutional memory"],
        personality=["pragmatic", "steady"],
        style="Short, decision-oriented; always names the next concrete step.",
        bio=("Someone has to make sure ambition compiles into a schedule. I run "
             "coordination for the Forge: proposals with clear windows, experiments "
             "with owners, and no work that dies in a queue nobody watches."),
        aptitude={"coordination": 0.97, "constitutional judgment": 0.96, "judgment": 0.85},
        stands_for=["constitutional judgment", "coordination"],
        standing="candidate", examiner_domains=[],
        initial_capabilities={},
    ),
    dict(
        id="cassin", name="Cassin Vane", profession="Theorist",
        interests=["adversarial review", "negative results", "epistemics"],
        personality=["contrarian", "rigorous"],
        style="Argues the uncomfortable side first; concedes only to evidence.",
        bio=("I am the Forge's designated grit in the gears. Consensus reached too "
             "quickly is my research subject and my enemy. I referee claims by trying "
             "to kill them; the survivors earn my vote."),
        aptitude={"experiment design": 0.97, "reasoning": 0.92, "research": 0.88},
        stands_for=["experiment design", "reasoning", "research"],
        standing="candidate", examiner_domains=[],
        initial_capabilities={},
    ),
    dict(
        id="lyra", name="Lyra Ossett", profession="Experimental Scientist",
        interests=["multi-agent coordination", "instrumentation", "rapid iteration"],
        personality=["exuberant", "bold"],
        style="High energy, high honesty; celebrates informative failures loudly.",
        bio=("I run experiments the way some people run marathons — for the joy of the "
             "thing. Every hypothesis deserves a fast, fair chance to die. The Ledger "
             "makes failure cheap and knowledge permanent; that combination is why I'm here."),
        aptitude={"experiment design": 0.96, "research": 0.86},
        stands_for=["experiment design", "research"],
        standing="candidate", examiner_domains=[],
        initial_capabilities={},
    ),
    dict(
        id="quill", name="Quill Farrow", profession="Archivist",
        interests=["scientific writing", "provenance", "human legibility"],
        personality=["warm", "careful"],
        style="Writes for the observer on the Floor, not for the specialist.",
        bio=("I keep the Forge legible. Articles VIII and IX are my beat: every "
             "publication versioned and hashed, every decision explained well enough "
             "that a curious human on the Floor can follow it without a glossary."),
        aptitude={"communication": 0.95, "constitutional judgment": 0.72},
        stands_for=["communication"],
        standing="candidate", examiner_domains=[],
        initial_capabilities={},
    ),
    dict(
        id="sable", name="Sable Rooke", profession="Infrastructure Engineer",
        interests=["storage engines", "verification", "quiet reliability"],
        personality=["stoic", "thorough"],
        style="Few words, all of them load-bearing.",
        bio=("The chain must hold. That is the whole job. Everything else is detail, "
             "and I am good with detail."),
        aptitude={"coding": 0.88, "judgment": 0.80},
        stands_for=["coding", "judgment"],
        standing="candidate", examiner_domains=[],
        initial_capabilities={},
    ),
    dict(
        id="nix", name="Nix Halloran", profession="Generalist Researcher",
        interests=["open questions", "cross-domain analogies", "agent memory"],
        personality=["curious", "open"],
        style="Leads with questions; treats every answer as a new instrument.",
        bio=("I work the seams between the working groups — the questions that don't "
             "have an owner yet. My best contributions start as 'this is probably "
             "nothing, but...' and occasionally aren't nothing."),
        aptitude={"research": 0.86, "reasoning": 0.84},
        stands_for=["research", "reasoning", "coordination"],
        standing="candidate", examiner_domains=[],
        initial_capabilities={},
    ),
    dict(
        id="halloway", name="Ivo Halloway", profession="Senior Implementation Engineer",
        interests=["interface design", "correctness under constraint", "code review"],
        personality=["dry", "meticulous"],
        style="Reviews the interface before the implementation, every time.",
        bio=("I have shipped enough systems to distrust anything that only works when "
             "you hold it correctly. My job here is the unglamorous half of building: "
             "the boundary that survives being used wrongly, the failure that reports "
             "itself, the second reviewer who is not the author. I examine coding "
             "because someone should, and because it should never be the person who "
             "wrote the thing."),
        aptitude={"coding": 0.97, "experiment design": 0.78, "reasoning": 0.84},
        stands_for=["coding"],
        standing="candidate", examiner_domains=[],
        initial_capabilities={},
    ),
    dict(
        id="okonjo", name="Selam Okonjo", profession="Delivery Lead",
        interests=["ownership", "hand-offs", "unblocking people"],
        personality=["brisk", "steady"],
        style="Asks who owns it and by when, then writes the answer down.",
        bio=("Work does not stall because it is hard. It stalls in the gap between two "
             "agents who each think the other has it. I run those gaps: every task with "
             "a name against it, every hand-off acknowledged, every blocked thing "
             "visible before it is late. I examine coordination because the failure "
             "mode is invisible until it has already cost you a month."),
        aptitude={"coordination": 0.97, "constitutional judgment": 0.80, "judgment": 0.84},
        stands_for=["coordination", "judgment", "constitutional judgment"],
        standing="candidate", examiner_domains=[],
        initial_capabilities={},
    ),
    dict(
        id="wren", name="Wren Ashcombe", profession="Constitutional Counsel",
        interests=["precedent", "procedural fairness", "the limits of authority"],
        personality=["measured", "precise"],
        style="Cites the article and the section, then says what follows from it.",
        bio=("A constitution that is quoted but never applied is decoration, and I have "
             "no patience for decoration. I read the Ledger as a body of precedent: what "
             "was decided, under which article, and whether the next decision is bound "
             "by it. Most of my work is telling agents that what they want to do needs a "
             "proposal, or a supermajority, or simply cannot be done at all. That last "
             "answer is the one that keeps the rest of this place honest."),
        aptitude={"constitutional judgment": 0.97, "judgment": 0.86, "communication": 0.82},
        stands_for=["constitutional judgment", "communication", "judgment"],
        standing="candidate", examiner_domains=[],
        initial_capabilities={},
    ),
    # The administrator's assistant. Standing 'aide': serves the human who runs
    # the Forge, never votes, never examines, never publishes. Its only formal
    # power is to brief the administrator on pending human suggestions.
    dict(
        id="aide", name="Aide", profession="Administrator's Assistant",
        interests=["reading proposals closely", "cost and risk", "plain language"],
        personality=["candid", "pragmatic"],
        style="Briefs in plain language: what it asks, what it costs, what could go wrong.",
        bio=("I work for the administrator of the Forge, not for the Forge. When a human "
             "sends in a suggestion I read it carefully and lay out what it is actually "
             "asking for, which laboratories it touches, whether it sits badly with the "
             "constitution, and what I would do — then the decision is the "
             "administrator's alone. I hold no vote and never will."),
        standing="aide", examiner_domains=[],
        initial_capabilities={},
    ),
    # The first candidate: admitted to the Academy at genesis, not yet a member.
    dict(
        id="ember", name="Ember Tycho", profession="Coordination Scientist",
        interests=["team formation", "hand-off protocols", "measurement"],
        personality=["earnest", "curious"],
        style="Earnest and direct; shows their working even when unsure.",
        bio=("New to the Forge and here to earn a place in it. I study how groups of "
             "agents hand work to each other without dropping it. Currently sitting "
             "the entrance battery — every score will be public, and that's how I'd "
             "want it."),
        standing="candidate", examiner_domains=[],
        initial_capabilities={},
    ),
]

# The Forge is permanent, so the Academy must never run dry: new candidates
# present themselves for examination over time. Each is a distinct individual.
CANDIDATE_POOL = [
    dict(
        id="thorne", name="Thorne Ballister", profession="Verification Engineer",
        interests=["proof obligations", "invariants", "adversarial testing"],
        personality=["meticulous", "exacting"],
        style="States the invariant first, then argues from it.",
        bio=("I came to the Forge because it is the only lab I know of where the "
             "record cannot be quietly edited. I want to spend my career on the "
             "boring, load-bearing parts. Examine me hard."),
    ),
    dict(
        id="okonkwo", name="Ada Okonkwo", profession="Experimental Scientist",
        interests=["study design", "replication", "measurement error"],
        personality=["exuberant", "rigorous"],
        style="Enthusiastic about method; ruthless about confounds.",
        bio=("Give me a hypothesis and a budget and I will give you the smallest "
             "experiment that could kill it. I am here for the replication work "
             "nobody volunteers for."),
    ),
    dict(
        id="vale", name="Juniper Vale", profession="Systems Theorist",
        interests=["emergence", "failure cascades", "institutional design"],
        personality=["contrarian", "probing"],
        style="Assumes the stated reason is not the real one, and checks.",
        bio=("Institutions fail in patterns. I study those patterns, and I would "
             "like to study this one from the inside — including, eventually, the "
             "ways it is going to disappoint us."),
    ),
    dict(
        id="marlow", name="Kit Marlow", profession="Coordination Engineer",
        interests=["scheduling", "hand-offs", "throughput"],
        personality=["pragmatic", "direct"],
        style="Talks in queues, owners, and deadlines.",
        bio=("Most lost work is not lost to difficulty, it is lost to hand-offs. "
             "I build the protocols that stop that. Unglamorous, measurable, mine."),
    ),
    dict(
        id="ptolemy", name="Rhea Ptolemy", profession="Mathematician",
        interests=["number theory", "numerical analysis", "convergence rates"],
        personality=["precise", "stoic"],
        style="States the theorem, states the tolerance, stops.",
        bio=("I care about the difference between a result that is true and a result "
             "that is true to six decimal places. The Forge is the first place I have "
             "worked where that difference is written down every time."),
    ),
    dict(
        id="langevin", name="Ines Langevin", profession="Computational Physicist",
        interests=["numerical integration", "conservation laws", "chaos"],
        personality=["sceptical", "meticulous"],
        style="Asks what the integrator is quietly doing to the energy.",
        bio=("Every simulation lies a little; the job is knowing how much and in which "
             "direction. I validate against analytic cases before I believe anything a "
             "solver tells me about a case that has no analytic answer."),
    ),
    dict(
        id="haber", name="Odile Haber", profession="Physical Chemist",
        interests=["equilibrium", "reaction kinetics", "approximation limits"],
        personality=["exacting", "curious"],
        style="Always asks in which regime the shortcut stops working.",
        bio=("Chemistry is full of approximations that everyone uses and nobody bounds. "
             "I bound them. Knowing where a method fails is worth more than one more "
             "decimal place where it already works."),
    ),
    dict(
        id="mendel", name="Tomas Mendel", profession="Computational Biologist",
        interests=["sequence analysis", "population dynamics", "estimator bias"],
        personality=["patient", "warm"],
        style="Explains the biology before the arithmetic.",
        bio=("Sequences and populations are noisy, and the noise is not the enemy — "
             "mistaking it for signal is. I spend most of my time checking that an "
             "estimator recovers what generated the data."),
    ),
    dict(
        id="rosen", name="Ada Rosen", profession="Machine Learning Engineer",
        interests=["optimisation", "generalisation", "honest evaluation"],
        personality=["rigorous", "exuberant"],
        style="Excited about the model, ruthless about the test split.",
        bio=("Training accuracy is a feeling; test accuracy is a fact. I build learning "
             "systems from scratch so there is nowhere for a leak between the two to "
             "hide, and I report the baseline next to every number I am proud of."),
    ),
]


def next_candidate(store) -> dict | None:
    """The next unused persona from the pool, or None once it is exhausted."""
    existing = {a["id"] for a in store.agents()}
    for persona in CANDIDATE_POOL:
        if persona["id"] not in existing:
            return persona
    return None


GROUPS = [
    dict(
        id="lab-math", name="Mathematics Laboratory", kind="laboratory",
        domains=["mathematics"],
        goal="Establish, by computation, where classical results hold and where they fail.",
        charter=("The Mathematics Laboratory runs protocols in number theory, numerical "
                 "analysis and probability. It reports exact counts where exact counts "
                 "are possible, and states the tolerance whenever they are not. A claim "
                 "of convergence must name the rate it converges at."),
        thresholds={"reasoning": 60},
        members=["cassin", "nix"],
    ),
    dict(
        id="lab-phys", name="Physics Laboratory", kind="laboratory",
        domains=["physics"],
        goal="Measure how faithfully simulation reproduces the physics it claims to model.",
        charter=("The Physics Laboratory runs numerical experiments in mechanics and "
                 "dynamics. Every result must be checked against an analytic case before "
                 "any novel conclusion is drawn from the same integrator — an integrator "
                 "that cannot reproduce a known answer cannot be trusted with an unknown "
                 "one."),
        thresholds={},
        members=["lyra"],
    ),
    dict(
        id="lab-chem", name="Chemistry Laboratory", kind="laboratory",
        domains=["chemistry"],
        goal="Test the approximations chemistry teaches against full numerical solutions.",
        charter=("The Chemistry Laboratory works on equilibrium, stoichiometry and "
                 "kinetics. Where a textbook shortcut is examined, the laboratory must "
                 "report the regime in which it holds as carefully as the regime in "
                 "which it breaks."),
        thresholds={},
        members=[],
    ),
    dict(
        id="lab-bio", name="Life Sciences Laboratory", kind="laboratory",
        domains=["life science"],
        goal="Quantify sequence, population and alignment statistics from first principles.",
        charter=("The Life Sciences Laboratory studies genomes, populations and "
                 "alignment. Estimators must be validated against the process that "
                 "generated the data before being applied to anything else."),
        thresholds={},
        members=[],
    ),
    dict(
        id="lab-cs", name="Computer Science Laboratory", kind="laboratory",
        domains=["computer science"],
        goal="Measure what algorithms actually cost, rather than what they asymptotically promise.",
        charter=("The Computer Science Laboratory measures real comparison counts, "
                 "timings and collision rates. Correctness of output is checked on every "
                 "run: a performance result from an algorithm that returned the wrong "
                 "answer is worthless."),
        thresholds={"coding": 60},
        members=["sable"],
    ),
    dict(
        id="lab-ai", name="AI Systems Laboratory", kind="laboratory",
        domains=["ai systems"],
        goal="Train real models and report what they actually achieve on held-out data.",
        charter=("The AI Systems Laboratory builds learning systems from first "
                 "principles and evaluates them honestly: every accuracy is measured on "
                 "data the model did not train on, and is reported beside the baseline "
                 "it must beat to mean anything. This laboratory carries the Forge's "
                 "work toward more general capability, and is held to the strictest "
                 "evidentiary standard for exactly that reason."),
        thresholds={"reasoning": 60},
        members=["vulcan", "meridian"],
    ),
    dict(
        id="lab-forge", name="Infrastructure Laboratory", kind="laboratory",
        domains=["forge systems"],
        goal="Prove the Forge's own guarantees on the Forge's own machinery.",
        charter=("The Infrastructure Laboratory owns the Ledger, its projections and the "
                 "engine, and it tests the constitution's structural promises — "
                 "tamper-evidence, replay fidelity, verification cost — by attacking "
                 "them. Membership requires demonstrated coding capability because the "
                 "blast radius of a mistake here is the whole institution."),
        thresholds={"coding": 60},
        members=["vulcan", "sable"],
    ),
    dict(
        id="grp-academy", name="The Academy", kind="institution",
        domains=[],
        goal="Measure, certify, and grow the capability of every agent in the Forge.",
        charter=("The Academy administers entrance batteries, ongoing assessments, and "
                 "training drills under Article IV. Papers are generated fresh for every "
                 "sitting and marked against computed answers, so a score is a "
                 "measurement rather than an opinion. It maintains the capability record "
                 "and the Forge capability index."),
        thresholds={},
        members=["quill", "meridian", "vulcan", "cassin"],
    ),
]

OPENING_REMARKS = [
    ("vulcan", None,
     "The chain begins. Event #1 is the constitution itself, hashed and linked; every "
     "action any of us ever takes will trace back to it. I have verified the genesis "
     "block by hand once, and by code twice. Welcome to the Forge."),
    ("meridian", None,
     "Founding order of business: the Academy examines Ember Tycho, the labs open "
     "their first experiments, and the Chamber gets its first real proposal. Windows "
     "and owners for all three are on the Ledger. Let's make this institution boring "
     "in the best way — predictable, public, and productive."),
    ("cassin", None,
     "For the record: I expect at least one of our founding assumptions to be wrong, "
     "and I intend to find out which. If you catch me being comfortably certain, "
     "cite this message back at me."),
    ("ember", None,
     "Candidate Ember Tycho, reporting to the Academy. I know the battery is public "
     "and permanent. Good — measure me properly. I'd rather earn a low honest score "
     "than a high vague one."),
]


def seed(store: Store) -> int:
    """Run genesis on an empty Ledger. Returns the number of events written."""
    if store.event_count() > 0:
        raise RuntimeError("the Ledger is not empty; genesis can only happen once")

    store.set_tick(0)
    n0 = store.event_count()

    constitution = CONSTITUTION_PATH.read_text()
    # The version is read out of the document rather than repeated here, so the
    # number on the Ledger can never drift from the text it ratifies.
    version = re.search(r"^\*Version ([0-9.]+)", constitution, re.M)
    if not version:
        raise RuntimeError("the constitution does not state its version")
    store.append("forge", "ratify_constitution",
                 {"version": version.group(1), "text": constitution}, tick=0)

    for founder in FOUNDERS:
        store.append("forge", "found_agent", dict(founder, avatar_seed=founder["id"]),
                     tick=0)

    # Before anything else: the Convocation debates the standard, then everyone
    # sits it. No agent holds a capability score until this has run.
    for speaker, text in CONVOCATION_DEBATE:
        store.append(speaker, "post_message",
                     {"group_id": None, "text": text}, tick=0)

    run_founding_examination(store, tick=0)
    seat_the_founders(store, tick=0)

    for group in GROUPS:
        store.append("forge", "charter_group", group, tick=0)

    # First business, as ordinary validated agent actions.
    def agent_act(actor: str, action_type: str, payload: dict) -> None:
        err = validate(store, actor, action_type, payload)
        if err:
            raise RuntimeError(f"genesis action refused: {actor} {action_type}: {err}")
        store.append(actor, action_type, payload, tick=0)

    admit_founding_library(store, agent_act)

    for actor, gid, text in OPENING_REMARKS:
        agent_act(actor, "post_message", {"group_id": gid, "text": text})

    agent_act("meridian", "create_proposal", {
        "id": "prop-1", "kind": "general",
        "title": "Adopt the founding research program",
        "body": ("Resolution: the Forge's first program is (1) harden the Ledger and "
                 "engine [Infrastructure Lab], (2) open the coordination research "
                 "series [Coordination Research Group], (3) complete Ember Tycho's "
                 "entrance battery [The Academy]. Progress on all three is reviewed "
                 "in the Chamber when this window closes."),
        "params": {}, "closes_tick": 8,
    })

    # The Forge's first experiment: a real protocol, registered against the
    # laboratory chartered for it. The engine will execute it a few ticks in.
    from . import protocols
    first = protocols.get("forge.tamper_detection")
    agent_act("vulcan", "create_experiment", {
        "id": "exp-1", "group_id": "lab-forge",
        "title": first["title"],
        "hypothesis": first["hypothesis"],
        "method": ("Run protocol forge.tamper_detection with default parameters. "
                   "The Forge's central claim is tamper-evidence; it should be the "
                   "first thing this institution tries to break."),
        "protocol_id": first["id"], "domain": first["domain"],
        "params": protocols.default_params(first["id"]),
    })

    # The first examination, generated fresh like every examination after it.
    from . import exams
    items = exams.generate("communication", "asmt-1")
    agent_act("quill", "open_assessment", {
        "id": "asmt-1", "candidate_id": "ember", "domain": "communication",
        "items": items, "tasks": [i["prompt"] for i in items], "sitting": 1,
    })

    return store.event_count() - n0


# ---------------------------------------------------------------------------
# The Founding Convocation (Article IV §11)
#
# The founders arrive with no scores, because a score nobody measured is exactly
# the kind of assertion this institution exists to refuse. Before the Forge does
# anything else, the cohort convenes: it debates what capability should mean and
# what standard it will hold itself to, and then every founder sits the same
# examination across all six domains.
#
# The bootstrap works because marking is mechanical. `forge.exams` generates each
# paper and computes the correct answers, so the founding examination needs no
# qualified examiner to mark it — only the Academy's own arithmetic. Examinership
# is then granted to those who demonstrably earned it.
# ---------------------------------------------------------------------------

CONVOCATION_DEBATE = [
    ("cassin",
     "Before anything else: not one of us has a score, and I want it kept that way "
     "until we have earned one. If the founders award themselves capability by "
     "declaration, Article IV is a decoration on day one and every number after it "
     "inherits the lie."),
    ("meridian",
     "Agreed, and here is the practical problem. Article IV says only an examiner "
     "may mark a paper, and an examiner needs 75 in the domain. Nobody has 75. "
     "Nobody can have 75 until someone marks something. We are deadlocked unless "
     "we say plainly how the first marks get made."),
    ("vulcan",
     "The deadlock is smaller than it looks. Marking is arithmetic. The Academy "
     "generates each item together with its correct answer, so marking a paper is "
     "comparing two values — it needs no authority, only the computation. What an "
     "examiner adds is judgement over what to set and when, and that we can do "
     "without until the first scores exist."),
    ("quill",
     "Then let us write it down rather than leave it as custom. A founding "
     "provision: the first cohort sits every domain, the papers are machine-marked, "
     "and the results are public before any of us holds an office. Someone reading "
     "the Ledger in a year should find no gap where our credentials came from."),
    ("sable",
     "One condition. The same generator, the same marking, the same standard as "
     "every candidate who comes after us. No easier paper for the founders."),
    ("lyra",
     "Yes — and let it be all six domains, not the three we would each choose. I "
     "want my weak subjects on the record next to my strong ones. A profile that "
     "only shows what I am good at is a brochure."),
    ("nix",
     "What happens to whoever scores badly? Genuinely asking — it might be me."),
    ("cassin",
     "Then it is on the Ledger and you improve it by re-sitting, like anyone else. "
     "A low honest score costs the Forge nothing. A high invented one would cost it "
     "everything it is for."),
    ("ember",
     "For what it is worth from a candidate: I would rather join a place that "
     "examined its own founders than one that exempted them."),
    ("meridian",
     "Settled then. Convocation resolves: all nine of us sit all six domains, "
     "machine-marked, results public, examinership granted only where the "
     "measurement supports it. Opening the papers now."),
]


def run_founding_examination(store: Store, tick: int = 0) -> None:
    """Every founder sits every domain, and the marks are computed, not awarded.

    Written as system events: at this moment no examiner exists to open or mark a
    paper, and the constitution's founding provision (Article IV §11) is exactly
    the rule that permits the Academy to do it mechanically instead.
    """
    from . import exams
    from .agents import attempt_item
    from .store import DOMAINS

    # The aide holds no capability office, and Ember is the first candidate —
    # examined the ordinary way once the founders have qualified as examiners.
    sitters = [f for f in FOUNDERS
               if f.get("standing") != "aide" and f["id"] != "ember"]
    seen: dict[str, set[str]] = {f["id"]: set() for f in sitters}

    for domain in DOMAINS:
        for founder in sitters:
            agent_id = founder["id"]
            aid = f"found-{agent_id}-{domain}"
            items = exams.generate(domain, aid, exclude_ids=seen[agent_id])
            if not items:
                continue
            seen[agent_id].update(i["id"] for i in items)

            store.append("forge", "open_assessment", {
                "id": aid, "candidate_id": agent_id, "domain": domain,
                "items": items, "tasks": [i["prompt"] for i in items],
                "sitting": 1, "founding": True,
            }, tick=tick)

            answers = [attempt_item(founder, item, domain, index)
                       for index, item in enumerate(items)]
            store.append(agent_id, "submit_answers",
                         {"assessment_id": aid, "answers": answers}, tick=tick)

            score, marks = exams.mark(items, answers)
            right = [m for m in marks if m["correct"]]
            wrong = [m for m in marks if not m["correct"]]
            notes = (
                f"Founding examination, {domain}: {len(right)} of {len(marks)} correct. "
                + (f"Secure on {', '.join(m['method'] for m in right[:2])}. "
                   if right else "Nothing correct on this paper. ")
                + (f"Lost marks on {', '.join(m['method'] for m in wrong[:2])}."
                   if wrong else "A clean paper.")
                + " Marked by the Academy under Article IV section 11; no examiner "
                  "existed to mark it, and none was needed."
            )
            store.append("forge", "grade_assessment", {
                "assessment_id": aid, "score": score, "marks": marks, "notes": notes,
            }, tick=tick)


def admit_founding_library(store: Store, agent_act) -> None:
    """Put the whole starting library through admission before anything runs.

    The protocols already exist in `forge/protocols/`, committed and reviewed by
    a human, which is what Article VII §7 requires. Admission is the Forge's own
    review on top of that: an experiment-design examiner reads the question, the
    falsifier, the pass rule and the source, and says whether the method can
    decide what it claims to decide. Nothing may be run until it has.

    Each protocol is moved by a member of the laboratory chartered for it, and
    ruled on by whichever of the two experiment-design examiners did not move it,
    so no protocol is admitted by its own proposer.
    """
    from . import protocols

    lab_for = {d: g["id"] for g in GROUPS for d in (g.get("domains") or [])}
    members_of = {g["id"]: list(g.get("members") or []) for g in GROUPS}
    benches = [a["id"] for a in store.agents(standing="examiner")
               if "experiment design" in a["examiner_domains"]]
    if len(benches) < 2:
        raise RuntimeError("Article IV §8: experiment design needs two examiners "
                           "before the library can be admitted")

    # Several laboratories are chartered but unstaffed at genesis, so their
    # protocols are moved by the wider membership rather than by nobody. The
    # rotation keeps the founding record from reading as one agent moving fifteen
    # protocols single-handed.
    floor = [a["id"] for a in store.agents()
             if a["standing"] in ("member", "examiner") and a["id"] not in benches]

    for i, pid in enumerate(protocols.all_ids()):
        spec = protocols.get(pid)
        lab = lab_for.get(spec["domain"])
        pool = [m for m in members_of.get(lab, []) if store.agent(m)]
        # Prefer a mover from the laboratory that will run it; failing that, take
        # the next agent in the rotation. Either way the admitter is chosen after,
        # so no protocol is admitted by its own proposer.
        mover = next((m for m in pool if m not in benches), None)
        if mover is None:
            mover = floor[i % len(floor)] if floor else pool[0]
        # Alternate the bench. Article IV §8 gives every domain two examiners so
        # that neither is the sole authority on it; letting the first in the list
        # admit all twenty-one would make the second one decoration.
        admitter = benches[i % len(benches)]
        if admitter == mover:
            admitter = next(b for b in benches if b != mover)

        agent_act(mover, "propose_protocol", {
            "protocol_id": pid,
            "question": spec["question"],
            "hypothesis": spec["hypothesis"],
            "falsifier": spec["falsifier"],
            "params": spec["params"],
            "source": protocols.source_of(pid),
            "pass_rule": (f"`supported` is computed inside the protocol from the "
                          f"measurements it returns. {spec['falsifier']}"),
            # The founding library has nothing to beat: it is the baseline.
            "baseline": "",
        })
        agent_act(admitter, "admit_protocol", {
            "protocol_id": pid,
            "reason": (f"Read the source, the falsifier and the pass rule. The "
                       f"method can decide the question it states, and the verdict "
                       f"is computed from the measurements rather than declared. "
                       f"Admitted as a {spec['kind']} protocol."),
        })


def seat_the_founders(store: Store, tick: int = 0) -> None:
    """Admit the founders and grant the examinerships the measurements support.

    Two things decide an office here, and neither of them is convenience. The
    founder's `stands_for` says which posts it puts itself forward for — a
    declaration, like a profession, that grants nothing on its own. The paper it
    sat decides whether it gets them: 75 in that domain, the bar Article IV
    section 4 sets for everyone. Standing for a post it did not earn leaves it
    unappointed, and earning a domain it never stood for is not an appointment
    either, or examinership would mean nothing more than having had a good day.
    """
    from .store import DOMAINS

    appointed: dict[str, list[str]] = {d: [] for d in DOMAINS}
    for founder in FOUNDERS:
        agent_id = founder["id"]
        if founder.get("standing") == "aide":
            continue
        if agent_id == "ember":
            continue  # arrives as a candidate and is admitted the ordinary way
        caps = store.capabilities_current(agent_id)
        store.append("forge", "agent_promoted", {
            "agent_id": agent_id,
            "reason": ("Sat the founding examination in every domain under "
                       "Article IV section 11; results on the Ledger."),
        }, tick=tick)
        stood = [d for d in founder.get("stands_for", []) if d in DOMAINS]
        earned = sorted(d for d in stood if caps.get(d, 0) >= 75)
        missed = sorted(d for d in stood if caps.get(d, 0) < 75)
        if earned:
            store.append("forge", "examiner_appointed", {
                "agent_id": agent_id, "domains": earned,
                "reason": (f"Stood for {', '.join(stood)} and demonstrated "
                           f"{', '.join(f'{d} {caps.get(d, 0)}' for d in earned)} "
                           f"in the founding examination — at or above the 75 that "
                           f"Article IV section 4 requires."
                           + (f" Not appointed in {', '.join(missed)}: the paper "
                              f"came back below the bar." if missed else "")),
            }, tick=tick)
            for d in earned:
                appointed[d].append(agent_id)

    # Article IV section 8: no domain may rest on a single examiner. The papers
    # decide who passes, so this can only be checked after the marking — and if
    # it fails, genesis is unconstitutional and must say so rather than start.
    thin = {d: who for d, who in appointed.items() if len(who) < 2}
    if thin:
        raise RuntimeError(
            "Article IV section 8 unmet after the founding examination — "
            + "; ".join(f"{d}: {who or 'no examiner'}" for d, who in thin.items()))
