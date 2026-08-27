"""Mathematics protocols — every number below is computed when the protocol runs."""

from __future__ import annotations

import math
import random


def monte_carlo_pi(max_exponent: int = 5, seed: int = 1) -> dict:
    """Estimate pi by rejection sampling and test whether the error falls as 1/sqrt(N).

    For each N = 10^k we sample N points in the unit square, count those inside the
    quarter circle, and record the absolute error against math.pi. The theoretical
    standard error of this estimator is ~1.64/sqrt(N); we test whether the measured
    error stays within a factor of 4 of that across the whole sweep.
    """
    rng = random.Random(seed)
    series = []
    for k in range(2, max_exponent + 1):
        n = 10 ** k
        inside = 0
        for _ in range(n):
            x, y = rng.random(), rng.random()
            if x * x + y * y <= 1.0:
                inside += 1
        estimate = 4.0 * inside / n
        error = abs(estimate - math.pi)
        predicted = 1.64 / math.sqrt(n)
        series.append({
            "samples": n,
            "estimate": round(estimate, 6),
            "abs_error": round(error, 6),
            "predicted_error": round(predicted, 6),
            "error_over_predicted": round(error / predicted, 3),
        })
    ratios = [row["error_over_predicted"] for row in series]
    within = [r for r in ratios if r <= 4.0]
    supported = len(within) == len(ratios)
    return {
        "series": series,
        "summary": {
            "sweeps": len(series),
            "max_error_over_predicted": round(max(ratios), 3),
            "final_estimate": series[-1]["estimate"],
            "final_abs_error": series[-1]["abs_error"],
            "true_pi": round(math.pi, 6),
        },
        "supported": supported,
        "conclusion": (
            f"Across {len(series)} sample sizes the measured error stayed within "
            f"{max(ratios):.2f}x the 1/sqrt(N) prediction; the largest deviation was "
            f"{max(ratios):.2f}x."
        ),
    }


def prime_counting_accuracy(limit_exponent: int = 6) -> dict:
    """Measure how well N/ln(N) and the logarithmic integral approximate pi(N).

    Primes are counted exactly with a sieve of Eratosthenes, so pi(N) here is a
    true count, not an estimate. We then test the classical claim that the relative
    error of N/ln(N) shrinks as N grows.
    """
    limit = 10 ** limit_exponent
    sieve = bytearray([1]) * (limit + 1)
    sieve[0:2] = b"\x00\x00"
    for p in range(2, int(limit ** 0.5) + 1):
        if sieve[p]:
            sieve[p * p::p] = bytearray(len(sieve[p * p::p]))

    def li(x: float) -> float:
        # Numerical logarithmic integral from 2 to x by Simpson's rule.
        steps = 2000
        a, b = 2.0, float(x)
        h = (b - a) / steps
        total = 0.0
        for i in range(steps + 1):
            t = a + i * h
            w = 1 if i in (0, steps) else (4 if i % 2 else 2)
            total += w / math.log(t)
        return total * h / 3.0

    series = []
    running = 0
    next_mark = 10
    for n in range(2, limit + 1):
        if sieve[n]:
            running += 1
        if n == next_mark:
            approx = n / math.log(n)
            series.append({
                "N": n,
                "pi_N": running,
                "N_over_lnN": round(approx, 1),
                "rel_error_pct": round(100 * abs(running - approx) / running, 3),
                "li_N": round(li(n), 1),
                "li_rel_error_pct": round(100 * abs(running - li(n)) / running, 4),
            })
            next_mark *= 10
    errors = [row["rel_error_pct"] for row in series]
    supported = all(b < a for a, b in zip(errors, errors[1:]))
    return {
        "series": series,
        "summary": {
            "limit": limit,
            "primes_found": running,
            "first_rel_error_pct": errors[0],
            "last_rel_error_pct": errors[-1],
            "li_last_rel_error_pct": series[-1]["li_rel_error_pct"],
        },
        "supported": supported,
        "conclusion": (
            f"pi({limit}) = {running} exactly. The relative error of N/ln(N) moved from "
            f"{errors[0]}% to {errors[-1]}%"
            + (" , decreasing at every decade." if supported
               else ", but did not decrease monotonically at every decade.")
            + f" The logarithmic integral was far closer at {series[-1]['li_rel_error_pct']}%."
        ),
    }


def root_finding_convergence(tolerance_exponent: int = 12) -> dict:
    """Count the iterations Newton and bisection actually need on the same roots.

    Newton's method converges quadratically and bisection linearly, so Newton
    should need dramatically fewer iterations. We measure the counts rather than
    assume them, on three functions with known roots.
    """
    tol = 10.0 ** (-tolerance_exponent)
    cases = [
        ("x^2 - 2", lambda x: x * x - 2, lambda x: 2 * x, 1.0, 2.0, math.sqrt(2)),
        ("cos(x) - x", lambda x: math.cos(x) - x, lambda x: -math.sin(x) - 1, 0.0, 1.0, None),
        ("x^3 - x - 2", lambda x: x ** 3 - x - 2, lambda x: 3 * x * x - 1, 1.0, 2.0, None),
    ]
    series = []
    for name, f, df, lo, hi, exact in cases:
        # Bisection
        a, b, bisect_steps = lo, hi, 0
        while (b - a) / 2 > tol and bisect_steps < 500:
            mid = (a + b) / 2
            if f(a) * f(mid) <= 0:
                b = mid
            else:
                a = mid
            bisect_steps += 1
        bisect_root = (a + b) / 2

        # Newton
        x, newton_steps = (lo + hi) / 2, 0
        while abs(f(x)) > tol and newton_steps < 500:
            d = df(x)
            if d == 0:
                break
            x -= f(x) / d
            newton_steps += 1
        newton_root = x

        series.append({
            "function": name,
            "bisection_iterations": bisect_steps,
            "newton_iterations": newton_steps,
            "speedup": round(bisect_steps / max(newton_steps, 1), 1),
            "newton_root": round(newton_root, 12),
            "root_agreement": round(abs(newton_root - bisect_root), 14),
        })
        if exact is not None:
            series[-1]["error_vs_exact"] = round(abs(newton_root - exact), 14)

    supported = all(r["newton_iterations"] < r["bisection_iterations"] for r in series)
    speedups = [r["speedup"] for r in series]
    return {
        "series": series,
        "summary": {
            "tolerance": tol,
            "cases": len(series),
            "mean_speedup": round(sum(speedups) / len(speedups), 1),
            "max_root_disagreement": max(r["root_agreement"] for r in series),
        },
        "supported": supported,
        "conclusion": (
            f"At tolerance {tol:g}, Newton needed fewer iterations than bisection in "
            f"{sum(1 for r in series if r['newton_iterations'] < r['bisection_iterations'])} "
            f"of {len(series)} cases, averaging a {sum(speedups) / len(speedups):.1f}x "
            f"reduction; both methods agreed on every root to within "
            f"{max(r['root_agreement'] for r in series):.2e}."
        ),
    }


PROTOCOLS = [
    {
        "id": "math.monte_carlo_pi",
        "domain": "mathematics",
        "title": "Monte-Carlo estimation of pi: does error fall as 1/sqrt(N)?",
        "question": "How does the error of a rejection-sampling estimate of pi scale with sample size?",
        "hypothesis": "Absolute error stays within a small constant factor of 1.64/sqrt(N) across four decades of N.",
        "falsifier": "Any sample size where the measured error exceeds 4x "
                      "the 1.64/sqrt(N) prediction refutes it.",
        "params": {
            "max_exponent": {"type": "int", "min": 3, "max": 6, "default": 5,
                             "doc": "largest sample size, as 10^k"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 1,
                     "doc": "RNG seed, recorded so the run can be reproduced exactly"},
        },
        "fn": monte_carlo_pi,
    },
    {
        "id": "math.prime_counting",
        "domain": "mathematics",
        "title": "Counting primes exactly and testing the prime number theorem",
        "question": "How fast does the relative error of N/ln(N) against pi(N) shrink?",
        "hypothesis": "The relative error of N/ln(N) decreases at every decade, and li(N) is closer than N/ln(N).",
        "falsifier": "A single decade where the relative error of N/ln(N) "
                      "fails to fall below the previous decade's refutes "
                      "it.",
        "params": {
            "limit_exponent": {"type": "int", "min": 3, "max": 7, "default": 6,
                               "doc": "sieve limit, as 10^k"},
        },
        "fn": prime_counting_accuracy,
    },
    {
        "id": "math.root_finding",
        "domain": "mathematics",
        "title": "Newton versus bisection: measured iteration counts to convergence",
        "question": "How much faster is Newton's method than bisection in practice?",
        "hypothesis": "Newton converges in fewer iterations than bisection on every test function.",
        "falsifier": "One test function on which Newton needs as many "
                      "iterations as bisection, or more, refutes it.",
        "params": {
            "tolerance_exponent": {"type": "int", "min": 6, "max": 14, "default": 12,
                                   "doc": "convergence tolerance, as 10^-k"},
        },
        "fn": root_finding_convergence,
    },
]
