"""Computer-science protocols — complexity and data-structure behaviour, measured."""

from __future__ import annotations

import hashlib
import math
import random
import time


def sorting_complexity(max_size: int = 2000, seed: int = 13) -> dict:
    """Count real comparisons for insertion sort and merge sort against theory.

    Comparisons are counted by instrumenting the algorithms, not estimated. The
    test is whether measured growth matches n^2 for insertion sort and n log n for
    merge sort: doubling n should roughly quadruple one and slightly more than
    double the other.
    """
    rng = random.Random(seed)

    def insertion_sort(data):
        comparisons = 0
        arr = list(data)
        for i in range(1, len(arr)):
            key = arr[i]
            j = i - 1
            while j >= 0:
                comparisons += 1
                if arr[j] <= key:
                    break
                arr[j + 1] = arr[j]
                j -= 1
            arr[j + 1] = key
        return arr, comparisons

    def merge_sort(data):
        comparisons = 0

        def sort(arr):
            nonlocal comparisons
            if len(arr) <= 1:
                return arr
            mid = len(arr) // 2
            left, right = sort(arr[:mid]), sort(arr[mid:])
            out, i, j = [], 0, 0
            while i < len(left) and j < len(right):
                comparisons += 1
                if left[i] <= right[j]:
                    out.append(left[i]); i += 1
                else:
                    out.append(right[j]); j += 1
            out.extend(left[i:]); out.extend(right[j:])
            return out

        return sort(list(data)), comparisons

    series = []
    n = 250
    while n <= max_size:
        data = [rng.randrange(1_000_000) for _ in range(n)]
        t0 = time.perf_counter()
        ins_sorted, ins_cmp = insertion_sort(data)
        ins_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        mrg_sorted, mrg_cmp = merge_sort(data)
        mrg_ms = (time.perf_counter() - t0) * 1000
        correct = ins_sorted == mrg_sorted == sorted(data)
        series.append({
            "n": n,
            "insertion_comparisons": ins_cmp,
            "merge_comparisons": mrg_cmp,
            "insertion_ms": round(ins_ms, 3),
            "merge_ms": round(mrg_ms, 3),
            "n_log2_n": round(n * math.log2(n)),
            "both_outputs_correct": correct,
        })
        n *= 2

    all_correct = all(r["both_outputs_correct"] for r in series)
    ins_ratios = [b["insertion_comparisons"] / a["insertion_comparisons"]
                  for a, b in zip(series, series[1:])]
    mrg_ratios = [b["merge_comparisons"] / a["merge_comparisons"]
                  for a, b in zip(series, series[1:])]
    quadratic = all(3.2 < r < 4.8 for r in ins_ratios) if ins_ratios else False
    linearithmic = all(1.9 < r < 2.6 for r in mrg_ratios) if mrg_ratios else False
    supported = all_correct and quadratic and linearithmic
    return {
        "series": series,
        "summary": {
            "sizes_tested": len(series),
            "outputs_all_correct": all_correct,
            "insertion_growth_per_doubling": [round(r, 2) for r in ins_ratios],
            "merge_growth_per_doubling": [round(r, 2) for r in mrg_ratios],
            "comparison_ratio_at_max_n": round(
                series[-1]["insertion_comparisons"] / series[-1]["merge_comparisons"], 1),
        },
        "supported": supported,
        "conclusion": (
            f"Both algorithms produced correctly sorted output at every size. Doubling n "
            f"multiplied insertion-sort comparisons by {sum(ins_ratios) / len(ins_ratios):.2f} "
            f"(quadratic predicts 4) and merge-sort comparisons by "
            f"{sum(mrg_ratios) / len(mrg_ratios):.2f} (n log n predicts just over 2). At "
            f"n={series[-1]['n']} insertion sort used "
            f"{series[-1]['insertion_comparisons'] / series[-1]['merge_comparisons']:.0f}x more "
            f"comparisons."
        ),
    }


def hash_collision_rates(table_size: int = 4096, seed: int = 17) -> dict:
    """Measure hash-table collision rates against the balls-in-bins prediction.

    Keys are hashed into a table of fixed size at rising load factors. The expected
    fraction of occupied buckets under uniform hashing is 1-exp(-load); we measure
    the realised fraction and compare.

    The hash is BLAKE2b rather than Python's built-in `hash()`, which is salted
    per process: using it would make this measurement unreproducible, and a result
    that cannot be re-derived is not a result this laboratory may publish.
    """
    rng = random.Random(seed)
    series = []
    for load_pct in (25, 50, 75, 100, 150, 200):
        load = load_pct / 100.0
        keys = int(table_size * load)
        buckets = [0] * table_size
        for _ in range(keys):
            key = f"key-{rng.randrange(1 << 40)}"
            digest = hashlib.blake2b(key.encode(), digest_size=8).digest()
            buckets[int.from_bytes(digest, "big") % table_size] += 1
        occupied = sum(1 for b in buckets if b)
        collisions = sum(b - 1 for b in buckets if b > 1)
        predicted_occupancy = 1 - math.exp(-load)
        measured_occupancy = occupied / table_size
        series.append({
            "load_factor": load,
            "keys_inserted": keys,
            "occupied_buckets_pct": round(100 * measured_occupancy, 2),
            "predicted_occupancy_pct": round(100 * predicted_occupancy, 2),
            "occupancy_error_pct": round(100 * abs(measured_occupancy - predicted_occupancy), 3),
            "collisions": collisions,
            "longest_chain": max(buckets),
        })
    worst_error = max(r["occupancy_error_pct"] for r in series)
    monotonic = all(b["collisions"] >= a["collisions"] for a, b in zip(series, series[1:]))
    supported = worst_error < 2.0 and monotonic
    return {
        "series": series,
        "summary": {
            "table_size": table_size,
            "worst_occupancy_error_pct": worst_error,
            "collisions_rise_with_load": monotonic,
            "longest_chain_observed": max(r["longest_chain"] for r in series),
        },
        "supported": supported,
        "conclusion": (
            f"Measured bucket occupancy tracked the 1-exp(-load) prediction to within "
            f"{worst_error:.2f} percentage points across load factors 0.25 to "
            f"{series[-1]['load_factor']}, with the longest chain reaching "
            f"{max(r['longest_chain'] for r in series)}."
        ),
    }


PROTOCOLS = [
    {
        "id": "cs.sorting_complexity",
        "domain": "computer science",
        "title": "Measured comparison counts for insertion sort and merge sort",
        "question": "Does observed comparison growth match the n^2 and n log n predictions?",
        "hypothesis": "Doubling n multiplies insertion-sort comparisons by about 4 and merge-sort comparisons by about 2, with both producing correct output.",
        "falsifier": "Either sort returning a wrong order, an insertion-sort "
                      "comparison ratio outside 3.2-4.8x per doubling, "
                      "or a merge-sort ratio outside 1.9-2.6x, refutes it.",
        "params": {
            "max_size": {"type": "int", "min": 500, "max": 16000, "default": 2000,
                         "doc": "largest array size"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 13, "doc": "RNG seed"},
        },
        "fn": sorting_complexity,
    },
    {
        "id": "cs.hash_collisions",
        "domain": "computer science",
        "title": "Hash-table occupancy and collisions versus load factor",
        "question": "Do realised collision rates follow the balls-in-bins prediction?",
        "hypothesis": "Occupied-bucket fraction matches 1-exp(-load) within two points and collisions rise with load.",
        "falsifier": "An occupancy differing from 1-exp(-load) by 2 points "
                      "or more, or collisions failing to rise with load, "
                      "refutes it.",
        "params": {
            "table_size": {"type": "int", "min": 256, "max": 65536, "default": 4096,
                           "doc": "number of buckets"},
            "seed": {"type": "int", "min": 0, "max": 999999, "default": 17, "doc": "RNG seed"},
        },
        "fn": hash_collision_rates,
    },
]
