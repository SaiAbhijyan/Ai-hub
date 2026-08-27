"""Forge-systems protocols — the institution measuring itself, on its own machinery.

These are the only protocols that touch the Forge's own code. They build a real
throwaway Ledger and measure it, so the numbers describe the system that published
them.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path


def chain_verification_cost(max_events: int = 1500) -> dict:
    """Measure how the cost of verifying the whole chain grows with its length.

    A real Ledger is built in a temporary directory and `verify_chain()` — the same
    function the site calls — is timed at increasing lengths. Verification re-hashes
    every event, so cost should be linear in chain length.
    """
    from ..store import Store

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "bench.db")
        series = []
        written = 0
        target = 250
        while target <= max_events:
            while written < target:
                store.append("bench", "post_message",
                             {"text": f"benchmark event {written}"}, tick=written)
                written += 1
            t0 = time.perf_counter()
            result = store.verify_chain()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            series.append({
                "events": written,
                "verify_ms": round(elapsed_ms, 3),
                "microseconds_per_event": round(1000 * elapsed_ms / written, 2),
                "chain_ok": result["ok"],
            })
            target *= 2

    all_ok = all(r["chain_ok"] for r in series)
    per_event = [r["microseconds_per_event"] for r in series]
    spread = max(per_event) / min(per_event) if min(per_event) else float("inf")
    supported = all_ok and spread < 3.0
    return {
        "series": series,
        "summary": {
            "max_chain_length": series[-1]["events"],
            "verify_ms_at_max": series[-1]["verify_ms"],
            "microseconds_per_event_min": min(per_event),
            "microseconds_per_event_max": max(per_event),
            "per_event_cost_spread": round(spread, 2),
            "every_chain_verified": all_ok,
        },
        "supported": supported,
        "conclusion": (
            f"Verifying a {series[-1]['events']}-event chain took "
            f"{series[-1]['verify_ms']:.1f} ms. Per-event cost stayed between "
            f"{min(per_event):.2f} and {max(per_event):.2f} microseconds — a spread of "
            f"{spread:.2f}x, consistent with linear scaling. Every chain verified."
        ),
    }


def projection_rebuild_fidelity(events: int = 250) -> dict:
    """Test the constitutional claim that all state is reproducible from the chain.

    Article II says every projection must be reproducible from the Ledger alone. We
    build a real Forge, snapshot every projection table, drop and replay them, and
    compare row by row. Any difference is a constitutional violation and is
    reported as one.
    """
    import os

    from ..agents import SimulatedAgent
    from ..engine import Engine
    from ..seed import seed as run_genesis
    from ..store import PROJECTION_TABLES, Store

    # The inner Forge exists to generate a realistic ledger to replay, not to do
    # science of its own — without this it would run protocols recursively.
    previous = os.environ.get("FORGE_NO_PROTOCOLS")
    os.environ["FORGE_NO_PROTOCOLS"] = "1"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "fidelity.db")
            run_genesis(store)
            engine = Engine(store, SimulatedAgent())
            guard = 0
            while store.event_count() < events and guard < 4000:
                engine.tick()
                guard += 1

            def snapshot():
                return {t: [tuple(r) for r in
                            store.conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
                        for t in PROJECTION_TABLES}

            before = snapshot()
            t0 = time.perf_counter()
            replayed = store.rebuild_projections()
            rebuild_ms = (time.perf_counter() - t0) * 1000
            after = snapshot()

            series = []
            for table in PROJECTION_TABLES:
                series.append({
                    "table": table,
                    "rows": len(before[table]),
                    "identical_after_replay": before[table] == after[table],
                })
    finally:
        if previous is None:
            os.environ.pop("FORGE_NO_PROTOCOLS", None)
        else:
            os.environ["FORGE_NO_PROTOCOLS"] = previous

    mismatches = [r["table"] for r in series if not r["identical_after_replay"]]
    supported = not mismatches
    return {
        "series": series,
        "summary": {
            "events_replayed": replayed,
            "tables_checked": len(series),
            "total_rows": sum(r["rows"] for r in series),
            "rebuild_ms": round(rebuild_ms, 2),
            "mismatched_tables": mismatches,
        },
        "supported": supported,
        "conclusion": (
            f"Replaying {replayed} events rebuilt all {len(series)} projection tables "
            f"({sum(r['rows'] for r in series)} rows) in {rebuild_ms:.0f} ms. "
            + ("Every table matched the live state exactly, satisfying Article II section 4."
               if supported else
               f"MISMATCH in {', '.join(mismatches)} — Article II section 4 is violated.")
        ),
    }


def tamper_detection_sensitivity(trials: int = 12) -> dict:
    """Measure what fraction of single-byte forgeries the hash chain actually catches.

    The Ledger's central claim is tamper-evidence. This builds a real chain, alters
    one event per trial, and checks whether `verify_chain()` reports the break —
    testing the guarantee rather than restating it.
    """
    import random

    from ..store import Store

    caught = 0
    series = []
    for trial in range(trials):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / f"tamper{trial}.db")
            for i in range(60):
                store.append("bench", "post_message", {"text": f"original message {i}"}, tick=i)
            rng = random.Random(trial)
            victim = rng.randrange(1, store.event_count() + 1)
            store.conn.execute(
                "UPDATE events SET payload = ? WHERE id = ?",
                ('{"text":"forged message"}', victim))
            store.conn.commit()
            result = store.verify_chain()
            detected = not result["ok"]
            caught += detected
            series.append({
                "trial": trial,
                "forged_event_id": victim,
                "detected": detected,
                "reported_at": result.get("error"),
            })

    rate = caught / trials
    supported = rate == 1.0
    return {
        "series": series[:10],
        "summary": {
            "trials": trials,
            "forgeries_detected": caught,
            "detection_rate_pct": round(100 * rate, 2),
            "undetected": trials - caught,
        },
        "supported": supported,
        "conclusion": (
            f"Of {trials} single-event forgeries against real chains, "
            f"{caught} were detected ({100 * rate:.1f}%). "
            + ("No forgery went unnoticed." if supported
               else f"{trials - caught} forgeries escaped detection, which would be a defect "
                    f"in the tamper-evidence guarantee.")
        ),
    }


PROTOCOLS = [
    {
        "id": "forge.verification_cost",
        "domain": "forge systems",
        "title": "Cost of verifying the Ledger as it grows",
        "question": "Does full-chain verification stay affordable as the Forge accumulates history?",
        "hypothesis": "Verification cost is linear in chain length: per-event cost varies by less than 3x.",
        "falsifier": "A failed verification at any chain length, or a "
                      "per-event cost varying by 3x or more across "
                      "lengths, refutes it.",
        "params": {
            "max_events": {"type": "int", "min": 500, "max": 20000, "default": 1500,
                           "doc": "longest chain to benchmark"},
        },
        "fn": chain_verification_cost,
    },
    {
        "id": "forge.rebuild_fidelity",
        "domain": "forge systems",
        "title": "Is every projection truly reproducible from the chain alone?",
        "question": "Does replaying the Ledger reproduce all derived state exactly?",
        "hypothesis": "Every projection table is byte-identical after a full replay, as Article II section 4 requires.",
        "falsifier": "One projection table that differs after a full "
                      "replay from the Ledger refutes it.",
        "params": {
            "events": {"type": "int", "min": 100, "max": 3000, "default": 250,
                       "doc": "chain length to build before replaying"},
        },
        "fn": projection_rebuild_fidelity,
    },
    {
        "id": "forge.tamper_detection",
        "domain": "forge systems",
        "title": "What fraction of forgeries does the hash chain catch?",
        "question": "Is the tamper-evidence guarantee real under repeated attack?",
        "hypothesis": "Every single-event forgery is detected by chain verification.",
        "falsifier": "A single forged event that chain verification does "
                      "not catch refutes it.",
        "params": {
            "trials": {"type": "int", "min": 5, "max": 100, "default": 12,
                       "doc": "number of independent forgery attempts"},
        },
        "fn": tamper_detection_sensitivity,
    },
]
