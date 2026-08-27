"""The protocol library: the Forge's actual science.

Every protocol here is real, executable code. An experiment in the Forge runs one
of these, and the numbers it publishes are the numbers this code measured on the
machine — never a template, never a hardcoded result.

A protocol declares:
    id          stable identifier, "<domain-prefix>.<name>"
    domain      one of DOMAINS
    title       what a paper about it would be called
    question    the research question, in plain words
    hypothesis  the falsifiable claim the run will test
    falsifier   the measured condition under which `supported` comes out False,
                stated in words. If the falsifier and the code ever disagree,
                the code is the truth and the falsifier is the bug.
    params      {name: {type, min, max, default, doc}}
    fn          the callable that performs the measurement

and `fn(**params)` returns:
    {
      "series":     [ {col: value, ...}, ... ]   # the measured rows (optional)
      "summary":    { name: value }              # scalar measurements
      "supported":  bool                         # COMPUTED from the data, never asserted
      "conclusion": "one sentence stating what the numbers show"
    }

`supported` must be derived from the measurements. A protocol that decides its own
verdict in advance would be exactly the fabrication this library exists to remove.
"""

from __future__ import annotations

import hashlib
import inspect
from typing import Any, Callable

from . import ai, chemistry, computerscience, forgesystems, lifescience, mathematics, physics

DOMAINS = [
    "mathematics",
    "physics",
    "chemistry",
    "life science",
    "computer science",
    "ai systems",
    "forge systems",
]

_MODULES = [mathematics, physics, chemistry, lifescience, computerscience, ai, forgesystems]

REGISTRY: dict[str, dict] = {}
for _module in _MODULES:
    for _spec in _module.PROTOCOLS:
        if _spec["id"] in REGISTRY:
            raise RuntimeError(f"duplicate protocol id: {_spec['id']}")
        if _spec["domain"] not in DOMAINS:
            raise RuntimeError(f"unknown domain {_spec['domain']!r} in {_spec['id']}")
        # A protocol that cannot say what would refute it is not an experiment.
        if not _spec.get("falsifier"):
            raise RuntimeError(f"protocol {_spec['id']} declares no falsifier")
        REGISTRY[_spec["id"]] = _spec


def get(protocol_id: str) -> dict | None:
    return REGISTRY.get(protocol_id)


def by_domain(domain: str) -> list[dict]:
    return [s for s in REGISTRY.values() if s["domain"] == domain]


def all_ids() -> list[str]:
    return sorted(REGISTRY)


def source_of(protocol_id: str) -> str:
    """The exact source of the measuring function — published with every paper."""
    spec = REGISTRY[protocol_id]
    return inspect.getsource(spec["fn"])


def code_hash(protocol_id: str) -> str:
    return hashlib.sha256(source_of(protocol_id).encode("utf-8")).hexdigest()


def default_params(protocol_id: str) -> dict:
    spec = REGISTRY[protocol_id]
    return {name: meta["default"] for name, meta in spec["params"].items()}


def validate_params(protocol_id: str, params: dict) -> tuple[dict, str | None]:
    """Coerce and bounds-check parameters. Agents choose these; nothing else."""
    spec = REGISTRY.get(protocol_id)
    if spec is None:
        return {}, f"no such protocol '{protocol_id}'"
    clean = default_params(protocol_id)
    for name, value in (params or {}).items():
        meta = spec["params"].get(name)
        if meta is None:
            return {}, f"unknown parameter '{name}' for {protocol_id}"
        try:
            value = int(value) if meta["type"] == "int" else float(value)
        except (TypeError, ValueError):
            return {}, f"parameter '{name}' must be {meta['type']}"
        if not meta["min"] <= value <= meta["max"]:
            return {}, (f"parameter '{name}'={value} out of range "
                        f"[{meta['min']}, {meta['max']}]")
        clean[name] = value
    return clean, None


def result_hash(results: dict) -> str:
    import json
    return hashlib.sha256(
        json.dumps(results, sort_keys=True, separators=(",", ":"),
                   default=str).encode("utf-8")).hexdigest()


Callable_ = Callable[..., dict[str, Any]]
