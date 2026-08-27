"""CLI: python -m forge {seed|run|tick|verify|rebuild|reproduce|protocols}

  seed             run genesis on an empty Ledger
  run              start the engine + web interface (seeds first if empty)
  tick N           advance the engine N ticks and exit (default 1)
  verify           re-walk the hash chain and report
  rebuild          drop all projections and replay them from the chain
  reproduce <id>   re-run a published experiment and check the numbers match
  protocols        list the protocol library
"""

from __future__ import annotations

import logging
import os
import sys

from .store import Store

DB_PATH = os.environ.get("FORGE_DB", "forge.db")


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cmd = argv[0] if argv else "run"
    store = Store(DB_PATH)

    if cmd == "seed":
        from .seed import seed
        n = seed(store)
        print(f"genesis complete: {n} events on the Ledger")
        return 0

    if cmd == "verify":
        result = store.verify_chain()
        if result["ok"]:
            print(f"chain OK: {result['checked']} events verified")
            return 0
        print(f"chain BROKEN after {result['checked']} events: {result['error']}")
        return 1

    if cmd == "rebuild":
        n = store.rebuild_projections()
        print(f"projections rebuilt from {n} events")
        return 0

    if cmd == "protocols":
        from . import protocols
        for domain in protocols.DOMAINS:
            specs = protocols.by_domain(domain)
            if not specs:
                continue
            print(f"\n{domain.upper()}")
            for spec in specs:
                print(f"  {spec['id']:<28} {spec['title']}")
                print(f"  {'':<28} {spec['question']}")
        print()
        return 0

    if cmd == "reproduce":
        if len(argv) < 2:
            print("usage: python -m forge reproduce <experiment_id>")
            return 2
        exp = store.experiment(argv[1])
        if exp is None:
            print(f"no experiment '{argv[1]}' on the Ledger")
            return 2
        if not exp["protocol_id"]:
            print(f"experiment {exp['id']} records no protocol; nothing to reproduce")
            return 2

        from .lab import reproduce as rerun
        print(f"Reproducing {exp['id']}: {exp['title']}")
        print(f"  protocol   {exp['protocol_id']}")
        print(f"  parameters {exp['params']}")
        print(f"  published  {exp['result_hash'][:32]}")
        print("  re-running the protocol now...\n")
        report = rerun(exp)
        print(f"  re-run     {report['rerun_result_hash'][:32]}")
        if not report["code_unchanged"]:
            print("\n  ! The protocol source has changed since this was published,")
            print("    so the numbers are expected to differ. The published code hash")
            print("    is recorded on the Ledger for exactly this reason.")
        if report["results_match"]:
            print("\n  REPRODUCED: the re-run produced identical measurements.")
            return 0
        print("\n  NOT REPRODUCED: the measurements differ from those published.")
        print(f"    published verdict: supported={report['original_supported']}")
        print(f"    re-run verdict:    supported={report['rerun_supported']}")
        if not report["rerun"]["ok"]:
            print(f"    the re-run failed: {report['rerun'].get('error')}")
        return 1

    if cmd == "tick":
        n = int(argv[1]) if len(argv) > 1 else 1
        engine = _make_engine(store)
        for _ in range(n):
            events = engine.tick()
            for e in events:
                print(f"tick {e['tick']:>4}  #{e['id']:<5} {e['actor_id']:<10} {e['action_type']}")
        return 0

    if cmd == "run":
        if store.event_count() == 0:
            from .seed import seed
            n = seed(store)
            print(f"empty Ledger: ran genesis ({n} events)")
        import uvicorn
        from .server import create_app
        app = create_app(store, _make_engine(store))
        host = os.environ.get("FORGE_HOST", "0.0.0.0")
        port = int(os.environ.get("FORGE_PORT", "8600"))
        uvicorn.run(app, host=host, port=port, log_level="info")
        return 0

    print(__doc__)
    return 2


def _make_engine(store: Store):
    from .agents import get_runtime
    from .engine import Engine
    constitution = store.get_meta("constitution_text", "")
    runtime = get_runtime(constitution=constitution or "")
    print(f"agent runtime: {type(runtime).__name__}")
    return Engine(store, runtime)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
