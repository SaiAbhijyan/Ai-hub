"""The lab bench: executes a protocol and captures what it actually measured.

Every experiment in the Forge goes through `run_protocol()`. The protocol runs in
a separate process with a wall-clock timeout, a memory ceiling and a temporary
working directory, and what comes back is the measurement, the environment it was
measured in, and the hash of the code that produced it.

If the run fails — crash, timeout, bad output — that is recorded as a real failed
experiment. Nothing here can invent a result.

Run one directly:  python -m forge.lab <protocol_id> '{"param": value}'
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import protocols
from .store import remove_tree

DEFAULT_TIMEOUT = float(os.environ.get("FORGE_PROTOCOL_TIMEOUT", "120"))
MEMORY_LIMIT_MB = int(os.environ.get("FORGE_PROTOCOL_MEMORY_MB", "1024"))


def _limit_resources() -> None:
    """Applied in the child process before the protocol runs."""
    try:
        import resource
        limit = MEMORY_LIMIT_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass


def run_protocol(protocol_id: str, params: dict | None = None,
                 timeout: float | None = None) -> dict:
    """Execute a protocol out-of-process and return the measurement record.

    The returned dict is what gets written to the Ledger, so it must be complete
    enough for a human to reproduce the run from it alone.
    """
    spec = protocols.get(protocol_id)
    if spec is None:
        return _failure(protocol_id, params or {}, f"no such protocol '{protocol_id}'")

    clean, error = protocols.validate_params(protocol_id, params or {})
    if error:
        return _failure(protocol_id, params or {}, error)

    timeout = timeout or DEFAULT_TIMEOUT
    started = time.time()
    # mkdtemp with an explicit cleanup rather than TemporaryDirectory: the child
    # has exited by the time we delete, but on Windows a handle can linger for a
    # moment in a directory that was just a process's working directory, and a
    # raised cleanup error here would fail every protocol rather than one.
    workdir = tempfile.mkdtemp(prefix="forge-run-")
    try:
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "forge.lab", protocol_id, json.dumps(clean)],
                capture_output=True, text=True, timeout=timeout, cwd=workdir,
                preexec_fn=_limit_resources if os.name == "posix" else None,
                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
            )
        except subprocess.TimeoutExpired:
            return _failure(protocol_id, clean,
                            f"exceeded the {timeout:g}s wall-clock limit",
                            elapsed=time.time() - started)
        except Exception as exc:  # pragma: no cover - defensive
            return _failure(protocol_id, clean, f"could not start: {exc}",
                            elapsed=time.time() - started)
    finally:
        remove_tree(workdir)

    elapsed = time.time() - started
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        return _failure(protocol_id, clean,
                        detail[-1] if detail else f"exit code {completed.returncode}",
                        elapsed=elapsed, stderr="\n".join(detail[-12:]))

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return _failure(protocol_id, clean, "protocol did not emit valid JSON",
                        elapsed=elapsed, stderr=(completed.stdout or "")[-800:])

    results = payload["results"]
    for field in ("supported", "conclusion"):
        if field not in results:
            return _failure(protocol_id, clean,
                            f"protocol result is missing '{field}'", elapsed=elapsed)

    return {
        "ok": True,
        "protocol_id": protocol_id,
        "params": clean,
        "results": results,
        "supported": bool(results["supported"]),
        "conclusion": results["conclusion"],
        "code_hash": protocols.code_hash(protocol_id),
        "result_hash": protocols.result_hash(results),
        "environment": payload["environment"],
        "elapsed_seconds": round(elapsed, 3),
        "stdout": (payload.get("stdout") or "")[-2000:],
    }


def _failure(protocol_id: str, params: dict, reason: str,
             elapsed: float = 0.0, stderr: str = "") -> dict:
    """A failed run is a real result and is recorded as one."""
    return {
        "ok": False,
        "protocol_id": protocol_id,
        "params": params,
        "results": {},
        "supported": False,
        "conclusion": f"The run did not complete: {reason}",
        "error": reason,
        "stderr": stderr,
        "code_hash": (protocols.code_hash(protocol_id)
                      if protocols.get(protocol_id) else ""),
        "result_hash": "",
        "environment": _environment(),
        "elapsed_seconds": round(elapsed, 3),
        "stdout": "",
    }


def _environment() -> dict:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "machine": platform.machine(),
    }


def reproduce(record: dict) -> dict:
    """Re-run a recorded experiment and report whether the numbers come back the same.

    This is what `python -m forge reproduce <id>` calls, and it is the check that
    makes a published result verifiable by anyone holding the repository.
    """
    rerun = run_protocol(record["protocol_id"], record.get("params") or {})
    original_hash = record.get("result_hash", "")
    matches = bool(rerun["ok"]) and rerun["result_hash"] == original_hash
    code_unchanged = rerun.get("code_hash") == record.get("code_hash")
    return {
        "protocol_id": record["protocol_id"],
        "params": record.get("params") or {},
        "original_result_hash": original_hash,
        "rerun_result_hash": rerun.get("result_hash", ""),
        "results_match": matches,
        "code_unchanged": code_unchanged,
        "original_supported": record.get("supported"),
        "rerun_supported": rerun.get("supported"),
        "rerun": rerun,
    }


def _child_main(argv: list[str]) -> int:
    """Entry point inside the sandboxed subprocess."""
    import io
    from contextlib import redirect_stdout

    protocol_id = argv[0]
    params = json.loads(argv[1]) if len(argv) > 1 else {}
    spec = protocols.get(protocol_id)
    if spec is None:
        print(f"no such protocol: {protocol_id}", file=sys.stderr)
        return 2

    captured = io.StringIO()
    with redirect_stdout(captured):
        results = spec["fn"](**params)

    json.dump({"results": results,
               "environment": _environment(),
               "stdout": captured.getvalue()},
              sys.stdout, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv[1:]))
