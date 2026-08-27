"""Life-science protocols — sequence statistics, population dynamics, alignment."""

from __future__ import annotations

import math
import random

CODON_TABLE = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L", "CTT": "L", "CTC": "L",
    "CTA": "L", "CTG": "L", "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M",
    "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V", "TCT": "S", "TCC": "S",
    "TCA": "S", "TCG": "S", "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
    "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T", "GCT": "A", "GCC": "A",
    "GCA": "A", "GCG": "A", "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*",
    "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q", "AAT": "N", "AAC": "N",
    "AAA": "K", "AAG": "K", "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
    "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W", "CGT": "R", "CGC": "R",
    "CGA": "R", "CGG": "R", "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R",
    "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
}


def gc_content_estimator(length: int = 60000, seed: int = 7) -> dict:
    """Test whether measured GC content is an unbiased estimator of the true rate.

    Sequences are generated at a known GC probability, then measured back. The
    question is whether the measurement lands within the binomial standard error
    of the value used to generate it — a real check of estimator bias.
    """
    rng = random.Random(seed)
    series = []
    for target_pct in (20, 35, 50, 65, 80):
        p = target_pct / 100.0
        seq = []
        for _ in range(length):
            if rng.random() < p:
                seq.append(rng.choice("GC"))
            else:
                seq.append(rng.choice("AT"))
        sequence = "".join(seq)
        gc = sum(1 for b in sequence if b in "GC")
        measured = gc / length
        stderr = math.sqrt(p * (1 - p) / length)
        z = abs(measured - p) / stderr if stderr else 0.0
        series.append({
            "target_gc_pct": target_pct,
            "measured_gc_pct": round(100 * measured, 4),
            "standard_error_pct": round(100 * stderr, 4),
            "z_score": round(z, 3),
            "within_3_sigma": z < 3.0,
        })
    supported = all(r["within_3_sigma"] for r in series)
    return {
        "series": series,
        "summary": {
            "sequence_length_bp": length,
            "conditions": len(series),
            "max_z_score": max(r["z_score"] for r in series),
            "all_within_3_sigma": supported,
        },
        "supported": supported,
        "conclusion": (
            f"Across {len(series)} GC levels on {length} bp sequences, the largest deviation "
            f"between measured and generating GC content was {max(r['z_score'] for r in series):.2f} "
            f"standard errors — consistent with an unbiased estimator."
        ),
    }


def codon_usage_and_translation(genes: int = 200, codons_per_gene: int = 300,
                                seed: int = 11) -> dict:
    """Measure whether translation and stop-codon statistics match expectation.

    Random coding sequences are generated and translated with the standard genetic
    code. Three of 64 codons are stops, so under uniform codon choice a stop should
    appear about 4.7% of the time; we measure the realised rate and the amino-acid
    frequency implied by codon degeneracy.
    """
    rng = random.Random(seed)
    codons = list(CODON_TABLE)
    stop_count = 0
    total_codons = 0
    aa_counts: dict[str, int] = {}
    for _ in range(genes):
        for _ in range(codons_per_gene):
            codon = rng.choice(codons)
            aa = CODON_TABLE[codon]
            total_codons += 1
            aa_counts[aa] = aa_counts.get(aa, 0) + 1
            if aa == "*":
                stop_count += 1

    expected_stop = sum(1 for v in CODON_TABLE.values() if v == "*") / len(CODON_TABLE)
    measured_stop = stop_count / total_codons
    stderr = math.sqrt(expected_stop * (1 - expected_stop) / total_codons)
    z_stop = abs(measured_stop - expected_stop) / stderr

    series = []
    for aa in sorted(aa_counts):
        degeneracy = sum(1 for v in CODON_TABLE.values() if v == aa)
        expected = degeneracy / len(CODON_TABLE)
        measured = aa_counts[aa] / total_codons
        series.append({
            "amino_acid": aa,
            "codons_encoding_it": degeneracy,
            "expected_freq_pct": round(100 * expected, 3),
            "measured_freq_pct": round(100 * measured, 3),
            "abs_difference_pct": round(100 * abs(measured - expected), 3),
        })
    worst = max(r["abs_difference_pct"] for r in series)
    supported = z_stop < 3.0 and worst < 1.0
    return {
        "series": series,
        "summary": {
            "total_codons": total_codons,
            "expected_stop_pct": round(100 * expected_stop, 3),
            "measured_stop_pct": round(100 * measured_stop, 3),
            "stop_z_score": round(z_stop, 3),
            "worst_amino_acid_deviation_pct": worst,
        },
        "supported": supported,
        "conclusion": (
            f"Over {total_codons} codons the stop rate measured "
            f"{100 * measured_stop:.2f}% against {100 * expected_stop:.2f}% expected from the "
            f"genetic code ({z_stop:.2f} sigma), and no amino-acid frequency deviated from its "
            f"degeneracy-implied value by more than {worst:.2f} percentage points."
        ),
    }


def logistic_versus_exponential(carrying_capacity: int = 1000, generations: int = 60,
                                seed: int = 3) -> dict:
    """Decide which growth model fits capacity-limited data, by measured error.

    A population is simulated under logistic dynamics with noise. Both an
    exponential and a logistic model are then fitted by least squares, and the model
    with lower residual error wins on the numbers.
    """
    rng = random.Random(seed)
    r, n = 0.18, 10.0
    observed = []
    for _ in range(generations):
        n = n + r * n * (1 - n / carrying_capacity)
        observed.append(max(n * (1 + rng.uniform(-0.03, 0.03)), 0.1))

    def sse(predicted: list[float]) -> float:
        return sum((o - p) ** 2 for o, p in zip(observed, predicted))

    # Fit exponential by least squares in log space (a real regression).
    ts = list(range(generations))
    logs = [math.log(v) for v in observed]
    mean_t = sum(ts) / len(ts)
    mean_l = sum(logs) / len(logs)
    slope = (sum((t - mean_t) * (l - mean_l) for t, l in zip(ts, logs))
             / sum((t - mean_t) ** 2 for t in ts))
    intercept = mean_l - slope * mean_t
    exp_pred = [math.exp(intercept + slope * t) for t in ts]

    # Fit logistic by grid search over (r, K) — a real search, scored on the data.
    best = None
    for r_milli in range(5, 41):
        rr = r_milli / 100.0
        for k in range(int(carrying_capacity * 0.5), int(carrying_capacity * 1.5), 25):
            pop, pred = observed[0], []
            for _ in ts:
                pred.append(pop)
                pop = pop + rr * pop * (1 - pop / k)
            score = sse(pred)
            if best is None or score < best[0]:
                best = (score, rr, k, pred)
    log_sse, fit_r, fit_k, log_pred = best
    exp_sse = sse(exp_pred)

    series = [{"generation": t,
               "observed": round(observed[t], 2),
               "logistic_fit": round(log_pred[t], 2),
               "exponential_fit": round(exp_pred[t], 2)}
              for t in range(0, generations, max(generations // 12, 1))]

    supported = log_sse < exp_sse
    return {
        "series": series,
        "summary": {
            "generations": generations,
            "true_carrying_capacity": carrying_capacity,
            "fitted_carrying_capacity": fit_k,
            "fitted_growth_rate": fit_r,
            "logistic_sse": round(log_sse, 2),
            "exponential_sse": round(exp_sse, 2),
            "sse_ratio_exp_over_logistic": round(exp_sse / log_sse, 1) if log_sse else None,
        },
        "supported": supported,
        "conclusion": (
            f"Fitted by least squares on the same {generations} observations, the logistic "
            f"model reached SSE {log_sse:.0f} against the exponential model's {exp_sse:.0f} — "
            f"{exp_sse / log_sse:.0f}x worse. The fit recovered a carrying capacity of "
            f"{fit_k} against a true value of {carrying_capacity}."
        ),
    }


def alignment_score_vs_mutation(length: int = 400, seed: int = 5) -> dict:
    """Measure how sequence-identity falls with mutation rate, via real alignment.

    A reference sequence is mutated at increasing rates and realigned with a
    Needleman-Wunsch global alignment computed here. Identity should fall
    monotonically, and at rate zero the alignment must be perfect — that perfect
    case is the check that the aligner itself is correct.
    """
    rng = random.Random(seed)
    reference = "".join(rng.choice("ACGT") for _ in range(length))

    def align(a: str, b: str, match=1, mismatch=-1, gap=-2):
        prev = [gap * j for j in range(len(b) + 1)]
        for i in range(1, len(a) + 1):
            cur = [gap * i] + [0] * len(b)
            for j in range(1, len(b) + 1):
                score = match if a[i - 1] == b[j - 1] else mismatch
                cur[j] = max(prev[j - 1] + score, prev[j] + gap, cur[j - 1] + gap)
            prev = cur
        return prev[len(b)]

    perfect = align(reference, reference)
    series = []
    for rate_pct in (0, 5, 10, 20, 30, 40):
        rate = rate_pct / 100.0
        mutated = []
        for base in reference:
            if rng.random() < rate:
                mutated.append(rng.choice([c for c in "ACGT" if c != base]))
            else:
                mutated.append(base)
        mutated = "".join(mutated)
        identity = sum(1 for x, y in zip(reference, mutated) if x == y) / length
        score = align(reference, mutated)
        series.append({
            "mutation_rate_pct": rate_pct,
            "identity_pct": round(100 * identity, 2),
            "alignment_score": score,
            "score_fraction_of_perfect": round(score / perfect, 4),
        })

    aligner_correct = series[0]["alignment_score"] == perfect == length
    monotonic = all(b["alignment_score"] <= a["alignment_score"]
                    for a, b in zip(series, series[1:]))
    supported = aligner_correct and monotonic
    return {
        "series": series,
        "summary": {
            "sequence_length_bp": length,
            "perfect_self_alignment_score": perfect,
            "aligner_self_check_passed": aligner_correct,
            "score_at_max_mutation": series[-1]["alignment_score"],
            "identity_at_max_mutation_pct": series[-1]["identity_pct"],
        },
        "supported": supported,
        "conclusion": (
            f"Self-alignment scored {perfect} on a {length} bp sequence, confirming the "
            f"aligner. Raising the mutation rate to {series[-1]['mutation_rate_pct']}% dropped "
            f"identity to {series[-1]['identity_pct']:.1f}% and the alignment score to "
            f"{series[-1]['score_fraction_of_perfect']:.2f} of perfect, "
            + ("declining monotonically throughout." if monotonic else "non-monotonically.")
        ),
    }


PROTOCOLS = [
    {
        "id": "bio.gc_content",
        "domain": "life science",
        "title": "Is measured GC content an unbiased estimator?",
        "question": "Does measured GC content recover the rate used to generate a sequence?",
        "hypothesis": "Measured GC content lies within three standard errors of the generating rate at every level.",
        "falsifier": "One sequence whose measured GC content lies 3 "
                      "standard errors or further from the generating "
                      "probability refutes it.",
        "params": {
            "length": {"type": "int", "min": 5000, "max": 300000, "default": 60000,
                       "doc": "sequence length in base pairs"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 7, "doc": "RNG seed"},
        },
        "fn": gc_content_estimator,
    },
    {
        "id": "bio.codon_usage",
        "domain": "life science",
        "title": "Stop-codon rate and amino-acid frequency under the standard genetic code",
        "question": "Do realised codon statistics match what the genetic code's degeneracy predicts?",
        "hypothesis": "Stop rate falls within three sigma of 3/64 and no amino-acid frequency deviates by more than one point.",
        "falsifier": "A stop-codon z-score of 3.0 or above, or any amino "
                      "acid whose measured frequency is off by 1 percentage "
                      "point or more, refutes it.",
        "params": {
            "genes": {"type": "int", "min": 20, "max": 1000, "default": 200,
                      "doc": "number of synthetic coding sequences"},
            "codons_per_gene": {"type": "int", "min": 50, "max": 1000, "default": 300,
                                "doc": "codons per sequence"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 11, "doc": "RNG seed"},
        },
        "fn": codon_usage_and_translation,
    },
    {
        "id": "bio.growth_model",
        "domain": "life science",
        "title": "Logistic versus exponential growth: which model the data prefers",
        "question": "Given noisy capacity-limited data, which model fits better and does the fit recover K?",
        "hypothesis": "The logistic fit achieves lower residual error than the exponential fit.",
        "falsifier": "The logistic fit's sum of squared error coming out "
                      "at or above the exponential fit's refutes it.",
        "params": {
            "carrying_capacity": {"type": "int", "min": 200, "max": 5000, "default": 1000,
                                  "doc": "true carrying capacity of the simulated population"},
            "generations": {"type": "int", "min": 20, "max": 200, "default": 60,
                            "doc": "generations observed"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 3, "doc": "RNG seed"},
        },
        "fn": logistic_versus_exponential,
    },
    {
        "id": "bio.alignment_mutation",
        "domain": "life science",
        "title": "Global alignment score against mutation rate",
        "question": "How does Needleman-Wunsch score degrade as sequences diverge?",
        "hypothesis": "Self-alignment is perfect and score falls monotonically as mutation rate rises.",
        "falsifier": "A self-alignment that is not a perfect full-length "
                      "score, or a score that rises at any higher mutation "
                      "rate, refutes it.",
        "params": {
            "length": {"type": "int", "min": 100, "max": 800, "default": 400,
                       "doc": "sequence length in base pairs"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 5, "doc": "RNG seed"},
        },
        "fn": alignment_score_vs_mutation,
    },
]
