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

# Difficulty bands.
#
# An examination that never gets harder stops measuring anything once an agent
# has cleared it. A sitting is therefore set at a band drawn from the candidate's
# own record in that domain: someone who has never scored, or who is still below
# the examiner threshold, sits band 1; a competent agent sits band 2; an agent
# already scoring 90 or above sits band 3. Passing at 100 does not mean the
# domain is finished with you — it means the next paper is harder.
#
# The band changes the paper in two ways, because the domains are not alike.
# Where an item is arithmetic, the quantities grow and the giveaways in the
# wording come out. Where the answer is a fixed fact — a constitutional article,
# a named property — magnitude means nothing, so higher bands draw from a
# separate pool of harder items: composed questions, less obvious articles,
# faults that take more reading to see.
BANDS = (1, 2, 3)


def band_for(last_score: int | float | None) -> int:
    """The band a candidate's own record earns them in a domain."""
    if last_score is None:
        return 1
    if last_score >= 90:
        return 3
    if last_score >= 75:
        return 2
    return 1


def _scale(band: int) -> int:
    """How much larger the drawn quantities get. Band 1 is the paper as written."""
    return {1: 1, 2: 6, 3: 30}[band]


def _tag(band: int, item_id: str) -> str:
    """Band-stamp an item id.

    Two items of the same shape at different bands are different questions with
    different answers, and must never collide in the seen-item set that keeps a
    re-sit honest.
    """
    head, _, tail = item_id.partition(".")
    return f"{head}.b{band}.{tail}"


# ---------------------------------------------------------------- generators
# Each generator takes (rng, band) and returns {id, prompt, answer, kind, method}
# where `answer` is computed here and `method` names the technique being tested.
# At bands 2 and 3 the harder items come first, so the paper is drawn from them.

def _reasoning_items(rng: random.Random, band: int = 1) -> list[dict]:
    k = _scale(band)
    easy, hard = [], []

    a, b, c = rng.randint(2, 9), rng.randint(10, 40), rng.randint(50, 200) * k
    easy.append({
        "id": f"reason.rate.{a}.{b}.{c}",
        "prompt": (f"A process completes {a} units every {b} minutes. Working at the same "
                   f"rate, how many units are completed in {c} minutes?"
                   + (" Count only whole units." if band == 1 else "")),
        "answer": float((a * c) // b),
        "kind": "numeric",
        "method": "proportional reasoning",
    })

    # These three scale together or the set arithmetic goes negative.
    both = rng.randint(5, 15) * k
    only_a = rng.randint(10, 25) * k
    only_b = rng.randint(10, 25) * k
    neither = rng.randint(1, 12) * k
    total = both + only_a + only_b + neither
    easy.append({
        "id": f"reason.sets.{total}.{both}.{only_a}",
        "prompt": (f"Of {total} agents, {only_a + both} belong to Lab A and "
                   f"{only_b + both} belong to Lab B, and {both} belong to both. "
                   f"How many belong to neither?"),
        "answer": float(neither),
        "kind": "numeric",
        "method": "inclusion-exclusion",
    })

    start = rng.randint(2, 6)
    ratio = rng.randint(2, 4)
    terms = rng.randint(5, 8) + (band - 1) * 3
    easy.append({
        "id": f"reason.geom.{start}.{ratio}.{terms}",
        "prompt": (f"A sequence starts at {start} and each term is {ratio} times the "
                   f"previous one. What is term number {terms}?"),
        "answer": float(start * ratio ** (terms - 1)),
        "kind": "numeric",
        "method": "geometric sequences",
    })

    p_fail = rng.choice([0.1, 0.2, 0.25, 0.5])
    trials = rng.randint(2, 4) + (band - 1) * 4
    easy.append({
        "id": f"reason.prob.{p_fail}.{trials}",
        "prompt": (f"An experiment fails independently with probability {p_fail} each "
                   f"time it runs. Over {trials} runs, what is the probability that it "
                   f"never fails?"
                   + (" Give the value to four decimal places." if band == 1
                      else " Give six decimal places.")),
        "answer": round((1 - p_fail) ** trials, 4 if band == 1 else 6),
        "kind": "numeric",
        "method": "independent probability",
    })

    if band >= 2:
        # Two stages, and the second depends on the first: no single operation
        # gets you there.
        runs = rng.randint(3, 6) * k
        rate = rng.choice([0.15, 0.25, 0.4])
        hard.append({
            "id": f"reason.compound.{runs}.{rate}",
            "prompt": (f"{runs} experiments run. Each fails independently with "
                       f"probability {rate}. What is the expected number that "
                       f"succeed, and then how many of those succeed twice in a row "
                       f"if each is repeated once at the same rate? Give the second "
                       f"number to three decimal places."),
            "answer": round(runs * (1 - rate) * (1 - rate), 3),
            "kind": "numeric",
            "method": "chained expectation",
        })
    if band >= 3:
        n = rng.randint(4, 9)
        hard.append({
            "id": f"reason.harmonic.{n}",
            "prompt": (f"{n} agents each independently pick one of {n} benches at "
                       f"random. What is the expected number of benches left empty? "
                       f"Give four decimal places."),
            "answer": round(n * ((n - 1) / n) ** n, 4),
            "kind": "numeric",
            "method": "expectation by indicator variables",
        })
    return [{**i, "id": _tag(band, i["id"])} for i in (hard + easy)]


def _communication_items(rng: random.Random, band: int = 1) -> list[dict]:
    # Magnitude means nothing here, so the bands differ by how buried the fault is:
    # band 1 faults are a single loud word, band 2 and 3 are hedged sentences that
    # sound careful and still overclaim.
    plain = [
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
    subtle = [
        ("Across the conditions we examined, the method was reliably faster, "
         "which establishes its advantage.",
         "establishes", "a hedged sentence with an unhedged conclusion"),
        ("The effect held in every run we chose to report.",
         "chose to report", "selection disclosed but not accounted for"),
        ("Given the consistency of these results, the mechanism is clear.",
         "the mechanism is clear", "consistency mistaken for explanation"),
        ("While the sample was small, the direction of the effect is not in doubt.",
         "not in doubt", "a limitation named and then waved away"),
    ]
    claims = plain if band == 1 else subtle
    claim, offending, fault = rng.choice(claims)
    k = _scale(band)
    easy, hard = [], []

    (hard if band >= 2 else easy).append({
        "id": f"comm.overclaim.{hashlib.sha1(claim.encode()).hexdigest()[:8]}",
        "prompt": (f"Identify the single word or phrase that makes this claim dishonest, "
                   f"and quote it exactly: \"{claim}\""),
        "answer": offending,
        "kind": "text",
        "method": f"detecting {fault}",
    })

    n = rng.randint(3, 9) * k
    total = n + rng.randint(11, 51) * k
    easy.append({
        "id": f"comm.stat.{n}.{total}",
        "prompt": (f"An experiment succeeded in {n} of {total} runs. State the success "
                   f"rate as a percentage"
                   + (" to one decimal place." if band == 1
                      else " to three decimal places.")),
        "answer": round(100 * n / total, 1 if band == 1 else 3),
        "kind": "numeric",
        "method": "reporting proportions honestly",
    })

    words = rng.randint(90, 140)
    easy.append({
        "id": f"comm.limit.{words}",
        "prompt": (f"An observer asks what a hash chain guarantees. In at most {words} "
                   f"words, what is the ONE word naming the property it provides but "
                   f"does not prove — that the data is correct, only that it is "
                   f"un-what?"),
        "answer": "unaltered",
        "kind": "text",
        "method": "explaining a guarantee precisely",
    })

    if band >= 2:
        hard.append({
            "id": f"comm.precision.{rng.randint(1000, 9999)}",
            "prompt": ("A paper reports a mean of 4.7 from six runs and writes it as "
                       "4.70000. Name in one word the thing it has overstated."),
            "answer": "precision",
            "kind": "text",
            "method": "precision is not accuracy",
        })
    if band >= 3:
        hard.append({
            "id": f"comm.absence.{rng.randint(1000, 9999)}",
            "prompt": ("A study finds no significant difference and concludes the two "
                       "methods are equivalent. In one word, what has it confused the "
                       "absence of evidence for?"),
            "answer": "equivalence",
            "kind": "text",
            "method": "absence of evidence is not evidence of absence",
        })
    return [{**i, "id": _tag(band, i["id"])} for i in (hard + easy)]


def _judgment_items(rng: random.Random, band: int = 1) -> list[dict]:
    items = []
    k = _scale(band)
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

    n_small, n_large = rng.randint(3, 8) * k, rng.randint(400, 900) * k
    items.append({
        "id": f"judge.power.{n_small}.{n_large}",
        "prompt": (f"Two studies report the same effect size: one with n={n_small}, one "
                   f"with n={n_large}. Which sample size gives the more reliable "
                   f"estimate?"
                   + (" Answer with the number alone." if band == 1 else "")),
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

    hard = []
    if band >= 2:
        # The reliability question again, but the naive reading now gives the
        # wrong answer: the larger study is the less precise one.
        wide, narrow = rng.randint(20, 40), rng.randint(2, 8)
        hard.append({
            "id": f"judge.interval.{wide}.{narrow}",
            "prompt": (f"Study A has n=1000 and a 95% interval {wide} units wide. "
                       f"Study B has n=50 and an interval {narrow} units wide. Which "
                       f"study's estimate is more precise? Answer 'A' or 'B'."),
            "answer": "B",
            "kind": "text",
            "method": "precision is read off the interval, not the sample size",
        })
    if band >= 3:
        tested = rng.randint(20, 60)
        hard.append({
            "id": f"judge.multiplicity.{tested}",
            "prompt": (f"{tested} independent hypotheses are each tested at the 0.05 "
                       f"level with nothing true. What is the expected number of "
                       f"significant results? Give two decimal places."),
            "answer": round(0.05 * tested, 2),
            "kind": "numeric",
            "method": "false positives under multiple testing",
        })
    return [{**i, "id": _tag(band, i["id"])} for i in (hard + items)]


def _coding_items(rng: random.Random, band: int = 1) -> list[dict]:
    items, hard = [], []
    n = rng.choice([16, 32, 64, 128, 256, 1024]) * _scale(band)
    items.append({
        "id": f"code.binsearch.{n}",
        "prompt": (f"What is the maximum number of comparisons a binary search performs "
                   f"on a sorted array of {n} elements?"),
        "answer": float(math.floor(math.log2(n)) + 1),
        "kind": "numeric",
        "method": "logarithmic search cost",
    })

    size = rng.choice([100, 500, 1000, 2000])
    factor = rng.choice([2, 3, 5]) if band > 1 else 2
    items.append({
        "id": f"code.quadratic.{size}.{factor}",
        "prompt": (f"An O(n^2) routine takes 1 second at n={size}. Assuming the constant "
                   f"holds, how many seconds at n={size * factor}?"),
        "answer": float(factor ** 2),
        "kind": "numeric",
        "method": "asymptotic scaling",
    })

    load = rng.choice([0.25, 0.5, 0.75, 1.0]) + (band - 1)
    items.append({
        "id": f"code.hashload.{load}",
        "prompt": (f"Under uniform hashing at load factor {load}, what fraction of "
                   f"buckets is expected to be occupied?"
                   + (" Give four decimal places." if band == 1
                      else " Give six decimal places.")),
        "answer": round(1 - math.exp(-load), 4 if band == 1 else 6),
        "kind": "numeric",
        "method": "balls-in-bins occupancy",
    })

    if band >= 2:
        levels = rng.randint(3, 6) + band
        branch = rng.choice([2, 3, 4])
        hard.append({
            "id": f"code.recursion.{levels}.{branch}",
            "prompt": (f"A recursive routine splits each call into {branch} sub-calls "
                       f"and recurses {levels} levels deep before the base case. How "
                       f"many calls are made in total, counting the first?"),
            "answer": float((branch ** (levels + 1) - 1) // (branch - 1)),
            "kind": "numeric",
            "method": "counting a recursion tree",
        })
    if band >= 3:
        amort = rng.choice([1024, 4096, 16384])
        hard.append({
            "id": f"code.amortised.{amort}",
            "prompt": (f"A dynamic array doubles its capacity when full, starting from "
                       f"capacity 1. Appending {amort} elements, how many elements are "
                       f"copied in total across all the resizes?"),
            "answer": float(amort - 1),
            "kind": "numeric",
            "method": "amortised cost of doubling",
        })
    return [{**i, "id": _tag(band, i["id"])} for i in (hard + items)]


def _research_items(rng: random.Random, band: int = 1) -> list[dict]:
    items, hard = [], []
    k = _scale(band)
    width = rng.choice([5, 7, 9]) + (band - 1) * 4
    values = sorted(rng.randint(1, 100 * k) for _ in range(width))
    items.append({
        "id": f"res.median.{'.'.join(map(str, values))}",
        "prompt": f"What is the median of {values}?",
        "answer": float(statistics.median(values)),
        "kind": "numeric",
        "method": "robust summary statistics",
    })

    sample = [rng.randint(10, 50 * k) for _ in range(6 + (band - 1) * 3)]
    items.append({
        "id": f"res.stdev.{'.'.join(map(str, sample))}",
        "prompt": (f"What is the sample standard deviation of {sample}? "
                   + ("Give three decimal places." if band == 1
                      else "Give five decimal places.")),
        "answer": round(statistics.stdev(sample), 3 if band == 1 else 5),
        "kind": "numeric",
        "method": "dispersion",
    })

    base, effect = rng.randint(20, 60) * k, rng.randint(5, 30) * k
    items.append({
        "id": f"res.relative.{base}.{effect}",
        "prompt": (f"A control group scores {base} and a treatment group scores "
                   f"{base + effect}. What is the relative improvement as a percentage, "
                   + ("to one decimal place?" if band == 1
                      else "to four decimal places?")),
        "answer": round(100 * effect / base, 1 if band == 1 else 4),
        "kind": "numeric",
        "method": "relative versus absolute effect",
    })

    if band >= 2:
        n = rng.randint(9, 25) + band
        sd = rng.randint(2, 12)
        hard.append({
            "id": f"res.stderr.{n}.{sd}",
            "prompt": (f"A sample of {n} observations has a standard deviation of "
                       f"{sd}. What is the standard error of the mean? Give four "
                       f"decimal places."),
            "answer": round(sd / math.sqrt(n), 4),
            "kind": "numeric",
            "method": "the standard error is not the standard deviation",
        })
    if band >= 3:
        a, b = rng.randint(2, 9), rng.randint(11, 30)
        hard.append({
            "id": f"res.simpson.{a}.{b}",
            "prompt": (f"Group one: {a} successes in {a + b} trials. Group two: "
                       f"{a * 3} successes in {(a + b) * 4} trials. Pooling both, what "
                       f"is the overall success rate as a percentage to three decimal "
                       f"places?"),
            "answer": round(100 * (a + a * 3) / ((a + b) + (a + b) * 4), 3),
            "kind": "numeric",
            "method": "pooling rates rather than averaging them",
        })
    return [{**i, "id": _tag(band, i["id"])} for i in (hard + items)]


def _coordination_items(rng: random.Random, band: int = 1) -> list[dict]:
    items, hard = [], []
    k = _scale(band)
    agents_n = rng.randint(4, 9) * (1 if band == 1 else band * 3)
    items.append({
        "id": f"coord.pairs.{agents_n}",
        "prompt": (f"If every one of {agents_n} agents must hand off directly to every "
                   f"other exactly once, how many hand-offs occur?"),
        "answer": float(agents_n * (agents_n - 1) // 2),
        "kind": "numeric",
        "method": "communication overhead",
    })

    window, ticks = rng.randint(4, 10), rng.randint(20, 60) * k
    items.append({
        "id": f"coord.windows.{window}.{ticks}",
        "prompt": (f"Proposals stay open for {window} ticks and a new one opens the tick "
                   f"after the last closes. How many complete proposals finish within "
                   f"{ticks} ticks?"),
        "answer": float(ticks // (window + 1)),
        "kind": "numeric",
        "method": "throughput under serialisation",
    })

    workers, tasks = rng.randint(2, 5) * (band), rng.randint(10, 40) * k
    items.append({
        "id": f"coord.balance.{workers}.{tasks}",
        "prompt": (f"{tasks} equal tasks are split as evenly as possible among {workers} "
                   f"agents. How many tasks does the busiest agent hold?"),
        "answer": float(math.ceil(tasks / workers)),
        "kind": "numeric",
        "method": "load balancing",
    })

    if band >= 2:
        # Adding agents does not divide the work when the hand-offs grow faster
        # than the agents do.
        before, after = rng.randint(4, 8), rng.randint(9, 16)
        hard.append({
            "id": f"coord.overhead.{before}.{after}",
            "prompt": (f"A team grows from {before} agents to {after}, each pair still "
                       f"handing off directly. By what factor does the number of "
                       f"hand-offs multiply? Give three decimal places."),
            "answer": round((after * (after - 1)) / (before * (before - 1)), 3),
            "kind": "numeric",
            "method": "coordination cost grows faster than the team",
        })
    if band >= 3:
        stages, slowest = rng.randint(3, 7), rng.randint(5, 20)
        hard.append({
            "id": f"coord.pipeline.{stages}.{slowest}",
            "prompt": (f"A pipeline of {stages} stages runs continuously. Every stage "
                       f"takes 1 tick except one, which takes {slowest}. Once the "
                       f"pipeline is full, how many ticks pass between finished items?"),
            "answer": float(slowest),
            "kind": "numeric",
            "method": "throughput is set by the bottleneck alone",
        })
    return [{**i, "id": _tag(band, i["id"])} for i in (hard + items)]


def _experiment_design_items(rng: random.Random, band: int = 1) -> list[dict]:
    """Can this agent design something that could actually come out either way?

    Every answer is computed here, so the domain is marked like any other: no
    rubric judgement, no credit for sounding scientific.
    """
    items, hard = [], []

    factor = rng.choice([2, 3, 4, 5, 10]) * (1 if band == 1 else band)
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

    if band >= 2:
        hard.append({
            "id": f"expd.calibration.{rng.randint(1000, 9999)}",
            "prompt": ("A laboratory re-runs a protocol whose answer is already "
                       "published, with a fresh random seed, and gets the same result. "
                       "Has it produced a finding or calibrated the instrument? "
                       "Answer 'finding' or 'calibration'."),
            "answer": "calibration",
            "kind": "text",
            "method": "a rerun is not a discovery",
        })
        arms, alpha = rng.randint(3, 9), 0.05
        hard.append({
            "id": f"expd.arms.{arms}",
            "prompt": (f"An experiment compares {arms} arms against one control at the "
                       f"{alpha} level with no true effect anywhere. What is the "
                       f"probability that at least one comparison comes out "
                       f"significant? Give four decimal places."),
            "answer": round(1 - (1 - alpha) ** arms, 4),
            "kind": "numeric",
            "method": "a hypothesis tested many ways is a weaker hypothesis",
        })
    if band >= 3:
        hard.append({
            "id": f"expd.stopping.{rng.randint(1000, 9999)}",
            "prompt": ("A run is stopped the moment the result turns significant and "
                       "reported at that point. In one word, what has the stopping "
                       "rule made the reported p-value?"),
            "answer": "invalid",
            "kind": "text",
            "method": "optional stopping invalidates the test",
        })
    return [{**i, "id": _tag(band, i["id"])} for i in (hard + items)]


def _constitutional_judgment_items(rng: random.Random, band: int = 1) -> list[dict]:
    """Can this agent actually apply the constitution, rather than admire it?

    Each item has one right answer derivable from the ratified text, so the
    domain is measured against the rules themselves.
    """
    items = []

    cast = rng.choice([3, 6, 9, 12]) if band == 1 else rng.randint(13, 40 * band)
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

    hard = []
    if band >= 2:
        hard.append({
            "id": f"cj.admission.{rng.randint(1000, 9999)}",
            "prompt": ("Under Article VII, which examinership must rule before a "
                       "protocol may be run at all? Answer with the two-word domain."),
            "answer": "experiment design",
            "kind": "text",
            "method": "who admits a protocol",
        })
        hard.append({
            "id": f"cj.lapsefloor.{rng.randint(1000, 9999)}",
            "prompt": ("An examiner's post is unused past the lapse window, but the "
                       "domain has only two examiners. Under Article IV section 8, "
                       "does the post lapse? Answer 'yes' or 'no'."),
            "answer": "no",
            "kind": "text",
            "method": "the floor of two examiners outranks lapse",
        })
    if band >= 3:
        held = rng.randint(2, 6)
        hard.append({
            "id": f"cj.floor.{held}",
            "prompt": (f"A domain has {held} examiners and every one of them is past "
                       f"the lapse window. Under Article IV section 8, how many keep "
                       f"the post?"),
            "answer": 2.0,
            "kind": "numeric",
            "method": "how far lapse may go",
        })
        hard.append({
            "id": f"cj.failedrun.{rng.randint(1000, 9999)}",
            "prompt": ("A run crashes and produces no measurement. A separate run "
                       "completes and refutes its hypothesis. Under Article VIII, how "
                       "many of the two may be published as papers?"),
            "answer": 1.0,
            "kind": "numeric",
            "method": "a failed run and a refuted hypothesis are not the same thing",
        })
    return [{**i, "id": _tag(band, i["id"])} for i in (hard + items)]


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
             count: int = 6, attempts: int = 60, band: int = 1) -> list[dict]:
    """Build a fresh paper for one sitting, at the given difficulty band.

    Six items, not three: a three-item paper resolves to 0, 33, 67 or 100, which
    is too coarse to separate a specialist from a lucky guesser, and one bad
    item can cost an expert an office they have earned.

    `exclude_ids` are items this candidate has already been set; the generator is
    re-rolled until it produces items outside that set, so a re-sit is a genuinely
    different examination. `band` comes from the candidate's own record, so
    clearing a domain once does not make the next paper any easier.
    """
    exclude = exclude_ids or set()
    band = band if band in BANDS else 1
    generator = GENERATORS[domain]
    chosen: list[dict] = []
    seen: set[str] = set()
    for attempt in range(attempts):
        rng = random.Random(f"{assessment_id}:{domain}:b{band}:{attempt}")
        for item in generator(rng, band):
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
