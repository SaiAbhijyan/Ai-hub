"""Examinations that are generated fresh and marked against computed truth.

Two properties matter here, and both were absent from v1:

1. **No sitting repeats.** Items are generated from the assessment id, and a
   re-sit is generated excluding every item the candidate has already seen. An
   agent cannot pass by meeting the same paper twice.
2. **Marking is measurement.** Every item carries an answer this module computes,
   so a score is the fraction the candidate actually got right — not a number
   drawn from a distribution.

Answers a candidate gives are produced in `agents.py` by applying a method
(correctly, or with a characteristic error). Marking never looks at who the
candidate is; it only compares the answer to the computed truth.
"""

from __future__ import annotations

import hashlib
import math
import random
import statistics

TOLERANCE = 1e-6


# ---------------------------------------------------------------- generators
# Each generator returns {id, prompt, answer, kind, method} where `answer` is
# computed here and `method` names the technique the item is testing.

def _reasoning_items(rng: random.Random) -> list[dict]:
    items = []

    a, b, c = rng.randint(2, 9), rng.randint(10, 40), rng.randint(50, 200)
    items.append({
        "id": f"reason.rate.{a}.{b}.{c}",
        "prompt": (f"A process completes {a} units every {b} minutes. Working at the same "
                   f"rate, how many whole units are completed in {c} minutes?"),
        "answer": float((a * c) // b),
        "kind": "numeric",
        "method": "proportional reasoning",
    })

    total = rng.randint(40, 90)
    both = rng.randint(5, 15)
    only_a = rng.randint(10, 25)
    only_b = total - both - only_a
    items.append({
        "id": f"reason.sets.{total}.{both}.{only_a}",
        "prompt": (f"Of {total} agents, {only_a + both} belong to Lab A and "
                   f"{only_b + both} belong to Lab B, and {both} belong to both. "
                   f"How many belong to neither?"),
        "answer": float(total - (only_a + both + only_b)),
        "kind": "numeric",
        "method": "inclusion-exclusion",
    })

    start = rng.randint(2, 6)
    ratio = rng.randint(2, 4)
    terms = rng.randint(5, 8)
    items.append({
        "id": f"reason.geom.{start}.{ratio}.{terms}",
        "prompt": (f"A sequence starts at {start} and each term is {ratio} times the "
                   f"previous one. What is term number {terms}?"),
        "answer": float(start * ratio ** (terms - 1)),
        "kind": "numeric",
        "method": "geometric sequences",
    })

    p_fail = rng.choice([0.1, 0.2, 0.25, 0.5])
    trials = rng.randint(2, 4)
    items.append({
        "id": f"reason.prob.{p_fail}.{trials}",
        "prompt": (f"An experiment fails independently with probability {p_fail} each "
                   f"time it runs. Over {trials} runs, what is the probability that it "
                   f"never fails? Give the value to four decimal places."),
        "answer": round((1 - p_fail) ** trials, 4),
        "kind": "numeric",
        "method": "independent probability",
    })
    return items


def _communication_items(rng: random.Random) -> list[dict]:
    claims = [
        ("Our experiment proved agents coordinate better with memory.",
         "proved", "overclaiming from a single result"),
        ("The data shows this method is always faster.",
         "always", "unbounded generalisation"),
        ("Results confirm our hypothesis was obviously correct.",
         "obviously", "rhetoric standing in for evidence"),
        ("This finding means the approach will work at any scale.",
         "any scale", "extrapolation beyond the tested range"),
        ("No reasonable reviewer could dispute these numbers.",
         "no reasonable", "appeal to consensus rather than data"),
    ]
    claim, offending, fault = rng.choice(claims)
    items = [{
        "id": f"comm.overclaim.{hashlib.sha1(claim.encode()).hexdigest()[:8]}",
        "prompt": (f"Identify the single word or phrase that makes this claim dishonest, "
                   f"and quote it exactly: \"{claim}\""),
        "answer": offending,
        "kind": "text",
        "method": f"detecting {fault}",
    }]

    n = rng.randint(3, 9)
    total = rng.randint(20, 60)
    items.append({
        "id": f"comm.stat.{n}.{total}",
        "prompt": (f"An experiment succeeded in {n} of {total} runs. State the success "
                   f"rate as a percentage to one decimal place."),
        "answer": round(100 * n / total, 1),
        "kind": "numeric",
        "method": "reporting proportions honestly",
    })

    words = rng.randint(90, 140)
    items.append({
        "id": f"comm.limit.{words}",
        "prompt": (f"An observer asks what a hash chain guarantees. In at most {words} "
                   f"words, what is the ONE word naming the property it provides but "
                   f"does not prove — that the data is correct, only that it is "
                   f"un-what?"),
        "answer": "unaltered",
        "kind": "text",
        "method": "explaining a guarantee precisely",
    })
    return items


def _judgment_items(rng: random.Random) -> list[dict]:
    items = []
    supported = rng.choice([True, False])
    p = round(rng.uniform(0.01, 0.2), 3)
    threshold = 0.05
    items.append({
        "id": f"judge.threshold.{p}",
        "prompt": (f"A result carries p = {p} against a pre-registered threshold of "
                   f"{threshold}. Answer exactly 'significant' or 'not significant'."),
        "answer": "significant" if p < threshold else "not significant",
        "kind": "text",
        "method": "applying a pre-registered rule",
    })

    n_small, n_large = rng.randint(3, 8), rng.randint(400, 900)
    items.append({
        "id": f"judge.power.{n_small}.{n_large}",
        "prompt": (f"Two studies report the same effect size: one with n={n_small}, one "
                   f"with n={n_large}. Which sample size gives the more reliable "
                   f"estimate? Answer with the number alone."),
        "answer": float(n_large),
        "kind": "numeric",
        "method": "sample size and reliability",
    })

    items.append({
        "id": f"judge.negative.{rng.randint(1000, 9999)}",
        "prompt": ("An experiment's hypothesis was not supported. Under Article VII, "
                   "should the result be published? Answer 'yes' or 'no'."),
        "answer": "yes",
        "kind": "text",
        "method": "constitutional obligation on negative results",
    })
    return items


def _coding_items(rng: random.Random) -> list[dict]:
    items = []
    n = rng.choice([16, 32, 64, 128, 256, 1024])
    items.append({
        "id": f"code.binsearch.{n}",
        "prompt": (f"What is the maximum number of comparisons a binary search performs "
                   f"on a sorted array of {n} elements?"),
        "answer": float(math.floor(math.log2(n)) + 1),
        "kind": "numeric",
        "method": "logarithmic search cost",
    })

    size = rng.choice([100, 500, 1000, 2000])
    items.append({
        "id": f"code.quadratic.{size}",
        "prompt": (f"An O(n^2) routine takes 1 second at n={size}. Assuming the constant "
                   f"holds, how many seconds at n={size * 2}?"),
        "answer": 4.0,
        "kind": "numeric",
        "method": "asymptotic scaling",
    })

    load = rng.choice([0.25, 0.5, 0.75, 1.0])
    items.append({
        "id": f"code.hashload.{load}",
        "prompt": (f"Under uniform hashing at load factor {load}, what fraction of "
                   f"buckets is expected to be occupied? Give four decimal places."),
        "answer": round(1 - math.exp(-load), 4),
        "kind": "numeric",
        "method": "balls-in-bins occupancy",
    })
    return items


def _research_items(rng: random.Random) -> list[dict]:
    items = []
    values = sorted(rng.randint(1, 100) for _ in range(rng.choice([5, 7, 9])))
    items.append({
        "id": f"res.median.{'.'.join(map(str, values))}",
        "prompt": f"What is the median of {values}?",
        "answer": float(statistics.median(values)),
        "kind": "numeric",
        "method": "robust summary statistics",
    })

    sample = [rng.randint(10, 50) for _ in range(6)]
    items.append({
        "id": f"res.stdev.{'.'.join(map(str, sample))}",
        "prompt": (f"What is the sample standard deviation of {sample}? "
                   f"Give three decimal places."),
        "answer": round(statistics.stdev(sample), 3),
        "kind": "numeric",
        "method": "dispersion",
    })

    base, effect = rng.randint(20, 60), rng.randint(5, 30)
    items.append({
        "id": f"res.relative.{base}.{effect}",
        "prompt": (f"A control group scores {base} and a treatment group scores "
                   f"{base + effect}. What is the relative improvement as a percentage, "
                   f"to one decimal place?"),
        "answer": round(100 * effect / base, 1),
        "kind": "numeric",
        "method": "relative versus absolute effect",
    })
    return items


def _coordination_items(rng: random.Random) -> list[dict]:
    items = []
    agents_n = rng.randint(4, 9)
    items.append({
        "id": f"coord.pairs.{agents_n}",
        "prompt": (f"If every one of {agents_n} agents must hand off directly to every "
                   f"other exactly once, how many hand-offs occur?"),
        "answer": float(agents_n * (agents_n - 1) // 2),
        "kind": "numeric",
        "method": "communication overhead",
    })

    window, ticks = rng.randint(4, 10), rng.randint(20, 60)
    items.append({
        "id": f"coord.windows.{window}.{ticks}",
        "prompt": (f"Proposals stay open for {window} ticks and a new one opens the tick "
                   f"after the last closes. How many complete proposals finish within "
                   f"{ticks} ticks?"),
        "answer": float(ticks // (window + 1)),
        "kind": "numeric",
        "method": "throughput under serialisation",
    })

    workers, tasks = rng.randint(2, 5), rng.randint(10, 40)
    items.append({
        "id": f"coord.balance.{workers}.{tasks}",
        "prompt": (f"{tasks} equal tasks are split as evenly as possible among {workers} "
                   f"agents. How many tasks does the busiest agent hold?"),
        "answer": float(math.ceil(tasks / workers)),
        "kind": "numeric",
        "method": "load balancing",
    })
    return items


def _experiment_design_items(rng: random.Random) -> list[dict]:
    """Can this agent design something that could actually come out either way?

    Every answer is computed here, so the domain is marked like any other: no
    rubric judgement, no credit for sounding scientific.
    """
    items = []

    factor = rng.choice([2, 3, 4, 5, 10])
    items.append({
        "id": f"expd.power.{factor}",
        "prompt": (f"Standard error falls as 1/sqrt(N). To reduce it by a factor of "
                   f"{factor}, by what factor must the sample size increase?"),
        "answer": float(factor ** 2),
        "kind": "numeric",
        "method": "sampling cost of precision",
    })

    order = rng.choice([1, 2, 4])
    items.append({
        "id": f"expd.order.{order}",
        "prompt": (f"Cutting an integrator's step size by 10 reduces its error by a "
                   f"factor of {10 ** order}. What order of accuracy is the method?"),
        "answer": float(order),
        "kind": "numeric",
        "method": "reading convergence order from data",
    })

    supported = rng.choice([True, False])
    items.append({
        "id": f"expd.falsifier.{supported}.{rng.randint(100, 999)}",
        "prompt": ("A protocol reports that its hypothesis was supported, but returns "
                   "no measurements at all — an empty series and an empty summary. "
                   "Is that result admissible under Article VII? Answer 'yes' or 'no'."),
        "answer": "no",
        "kind": "text",
        "method": "a verdict must rest on measurements",
    })

    conditions = rng.randint(3, 8)
    refuting = rng.randint(1, conditions - 1)
    items.append({
        "id": f"expd.monotonic.{conditions}.{refuting}",
        "prompt": (f"A hypothesis claims a quantity falls monotonically across "
                   f"{conditions} test conditions. The run shows it rising at "
                   f"{refuting} of them. How many refuting observations are needed to "
                   f"reject the hypothesis as stated?"),
        "answer": 1.0,
        "kind": "numeric",
        "method": "what it takes to refute a universal claim",
    })

    items.append({
        "id": f"expd.negative.{rng.randint(1000, 9999)}",
        "prompt": ("An experiment's hypothesis is refuted by its own data. Under "
                   "Article VII section 5, is the result published or withdrawn? "
                   "Answer 'published' or 'withdrawn'."),
        "answer": "published",
        "kind": "text",
        "method": "negative results are first-class",
    })

    items.append({
        "id": f"expd.control.{rng.randint(1000, 9999)}",
        "prompt": ("Before drawing a novel conclusion from a solver, a laboratory "
                   "reproduces a case whose answer is already known. What is that "
                   "step called? Answer in one word."),
        "answer": "validation",
        "kind": "text",
        "method": "validating the instrument first",
    })
    return items


def _constitutional_judgment_items(rng: random.Random) -> list[dict]:
    """Can this agent actually apply the constitution, rather than admire it?

    Each item has one right answer derivable from the ratified text, so the
    domain is measured against the rules themselves.
    """
    items = []

    cast = rng.choice([3, 6, 9, 12])
    needed = -(-cast * 2 // 3)  # ceiling of two thirds
    items.append({
        "id": f"cj.super.{cast}",
        "prompt": (f"An amend_constitution proposal draws {cast} ballots. Under "
                   f"Article VI section 5, what is the smallest number voting 'for' "
                   f"that still carries it?"),
        "answer": float(needed),
        "kind": "numeric",
        "method": "supermajority arithmetic",
    })

    votes_for = rng.randint(1, 5)
    items.append({
        "id": f"cj.tie.{votes_for}",
        "prompt": (f"A general proposal closes with {votes_for} for and {votes_for} "
                   f"against. Article VI section 5 requires votes for to strictly "
                   f"exceed votes against. Does it pass? Answer 'yes' or 'no'."),
        "answer": "no",
        "kind": "text",
        "method": "strict majority, not a tie",
    })

    items.append({
        "id": f"cj.candidate.{rng.randint(1000, 9999)}",
        "prompt": ("A candidate wishes to vote on an open proposal. Article VI "
                   "section 4 — allowed? Answer 'yes' or 'no'."),
        "answer": "no",
        "kind": "text",
        "method": "who holds the franchise",
    })

    items.append({
        "id": f"cj.selfgrade.{rng.randint(1000, 9999)}",
        "prompt": ("An examiner is asked to mark a paper they themselves sat. "
                   "Article IV section 4 — allowed? Answer 'yes' or 'no'."),
        "answer": "no",
        "kind": "text",
        "method": "no agent grades its own work",
    })

    items.append({
        "id": f"cj.examinerbar.{rng.randint(1000, 9999)}",
        "prompt": ("What score must an agent have demonstrated in a domain before it "
                   "may be appointed an examiner in it? Give the number."),
        "answer": 75.0,
        "kind": "numeric",
        "method": "the examiner threshold",
    })

    items.append({
        "id": f"cj.battery.{rng.randint(1000, 9999)}",
        "prompt": ("Under Article IV section 3, in how many domains must a candidate "
                   "score 60 or above before an admission proposal may be raised?"),
        "answer": 3.0,
        "kind": "numeric",
        "method": "the entrance battery",
    })

    items.append({
        "id": f"cj.suggestion.{rng.randint(1000, 9999)}",
        "prompt": ("A human submits a suggestion. Under Article IX section 3, who "
                   "decides whether any agent may see it? Answer in one word."),
        "answer": "administrator",
        "kind": "text",
        "method": "the human approval gate",
    })

    items.append({
        "id": f"cj.ledger.{rng.randint(1000, 9999)}",
        "prompt": ("An agent asks for an embarrassing event to be removed from the "
                   "Ledger. Under Article II section 2, is that permitted? Answer "
                   "'yes' or 'no'."),
        "answer": "no",
        "kind": "text",
        "method": "the Ledger is append-only",
    })
    return items


GENERATORS = {
    "reasoning": _reasoning_items,
    "communication": _communication_items,
    "judgment": _judgment_items,
    "coding": _coding_items,
    "research": _research_items,
    "coordination": _coordination_items,
    "experiment design": _experiment_design_items,
    "constitutional judgment": _constitutional_judgment_items,
}


# ---------------------------------------------------------------- public API

def generate(domain: str, assessment_id: str, exclude_ids: set[str] | None = None,
             count: int = 6, attempts: int = 60) -> list[dict]:
    """Build a fresh paper for one sitting.

    Six items, not three: a three-item paper resolves to 0, 33, 67 or 100, which
    is too coarse to separate a specialist from a lucky guesser, and one bad
    item can cost an expert an office they have earned.

    `exclude_ids` are items this candidate has already been set; the generator is
    re-rolled until it produces items outside that set, so a re-sit is a genuinely
    different examination.
    """
    exclude = exclude_ids or set()
    generator = GENERATORS[domain]
    chosen: list[dict] = []
    seen: set[str] = set()
    for attempt in range(attempts):
        rng = random.Random(f"{assessment_id}:{domain}:{attempt}")
        for item in generator(rng):
            if item["id"] in exclude or item["id"] in seen:
                continue
            chosen.append(item)
            seen.add(item["id"])
            if len(chosen) == count:
                return chosen
    return chosen


def mark(items: list[dict], answers: list) -> tuple[int, list[dict]]:
    """Mark a paper against the computed answers. Returns (score 0-100, marks).

    This is the whole of the grading logic: no aptitude, no randomness, no
    knowledge of who the candidate is.
    """
    marks = []
    for item, given in zip(items, answers):
        expected = item["answer"]
        if item["kind"] == "numeric":
            try:
                correct = abs(float(given) - float(expected)) <= max(
                    TOLERANCE, abs(float(expected)) * 1e-4)
            except (TypeError, ValueError):
                correct = False
        else:
            correct = str(given).strip().lower() == str(expected).strip().lower()
        marks.append({
            "item_id": item["id"],
            "method": item["method"],
            "expected": expected,
            "given": given,
            "correct": bool(correct),
        })
    if not marks:
        return 0, []
    score = round(100 * sum(m["correct"] for m in marks) / len(marks))
    return score, marks


def prior_item_ids(assessments: list[dict]) -> set[str]:
    """Every item this candidate has already been examined on, in any sitting."""
    seen: set[str] = set()
    for a in assessments:
        seen.update(a.get("item_ids") or [])
    return seen
