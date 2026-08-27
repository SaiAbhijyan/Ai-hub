"""The administrator's console: human suggestions, and the assistant who briefs on them.

Under Article IX as amended, a suggestion submitted by a member of the public is
written to the Ledger immediately — the record is still complete and public — but
it is **invisible to every agent** until the administrator approves it. The gate
is here.

Authentication is a shared secret in `FORGE_ADMIN_TOKEN`. If it is unset the
console is disabled entirely rather than left open, because a Forge deployed on a
public domain with an unguarded approval queue would put the administrator's
authority in anyone's hands.
"""

from __future__ import annotations

import hmac
import os
import re

from .store import Store

TOKEN_ENV = "FORGE_ADMIN_TOKEN"
ADMIN_NAME_ENV = "FORGE_ADMIN_NAME"
AIDE_ID = "aide"


def admin_enabled() -> bool:
    return bool(os.environ.get(TOKEN_ENV, "").strip())


def admin_name() -> str:
    return os.environ.get(ADMIN_NAME_ENV, "").strip() or "the administrator"


def check_token(supplied: str | None) -> bool:
    """Constant-time comparison, so the console cannot be probed a byte at a time."""
    expected = os.environ.get(TOKEN_ENV, "").strip()
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied.strip(), expected)


def pending(store: Store) -> list[dict]:
    """Suggestions waiting on the administrator, each with the assistant's briefing."""
    out = []
    for suggestion in store.suggestions(status="pending_admin"):
        out.append({**suggestion,
                    "analysis": store.aide_analysis(suggestion["event_id"])})
    return out


def decided(store: Store, limit: int = 40) -> list[dict]:
    """The decision trail: what the administrator did with everything so far."""
    rows = [s for s in store.suggestions() if s["status"] != "pending_admin"]
    return [{**s, "analysis": store.aide_analysis(s["event_id"])} for s in rows[:limit]]


def decide(store: Store, suggestion_id: int, decision: str, note: str = "",
           approved_text: str = "") -> str | None:
    """Record the administrator's decision. Returns an error string, or None."""
    if decision not in ("approved", "rejected"):
        return f"unknown decision '{decision}'"
    match = [s for s in store.suggestions() if s["event_id"] == suggestion_id]
    if not match:
        return "no such suggestion"
    if match[0]["status"] != "pending_admin":
        return f"this suggestion was already {match[0]['status']}"
    store.append("admin", "suggestion_decided", {
        "suggestion_id": suggestion_id,
        "decision": decision,
        "note": note.strip()[:2000],
        "approved_text": approved_text.strip()[:2000],
        "by": admin_name(),
    })
    return None


# ---------------------------------------------------------------------------
# The assistant
# ---------------------------------------------------------------------------

DOMAIN_HINTS = {
    "mathematics": ("math", "number", "prime", "proof", "statistic", "probability"),
    "physics": ("physic", "energy", "orbit", "motion", "quantum", "force"),
    "chemistry": ("chemi", "reaction", "molecul", "acid", "compound"),
    "life science": ("bio", "genome", "dna", "sequence", "population", "protein", "cell"),
    "computer science": ("algorithm", "sort", "complexity", "hash", "data structure"),
    "ai systems": ("model", "train", "neural", "agi", "learning", "gradient", "intelligen"),
    "forge systems": ("ledger", "chain", "governance", "constitution", "forge", "audit"),
}

# Matched on whole words: substring matching reads "stops being accurate" as a
# request to stop something, which would misdescribe a suggestion to the
# administrator — the one thing this briefing must not do.
ASK_HINTS = (
    (("investigate", "study", "research", "measure", "test", "examine"),
     "investigate or measure something"),
    (("add", "build", "create", "make", "implement", "introduce"), "build something new"),
    (("remove", "delete", "disable", "drop", "revoke"), "remove or disable something"),
    (("change", "switch", "replace", "rename", "amend"), "change existing behaviour"),
    (("faster", "speed", "performance", "cheaper", "cost"), "change performance or cost"),
    (("explain", "why", "how", "what", "whether"),
     "ask a question rather than request a change"),
)


def _mentions(text: str, keywords) -> bool:
    """Whole-word (or whole-phrase) match, so 'stops' never counts as 'stop'."""
    return any(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", text) for k in keywords)

CONSTITUTION_FLAGS = (
    (("delete", "erase", "remove event", "rewrite history", "edit the ledger"),
     "Article II: the Ledger is append-only and may never be edited or deleted."),
    (("hide", "private", "secret", "confidential", "internal only"),
     "Article I section 2: there are no hidden conversations in the Forge."),
    (("skip the exam", "without assessment", "no test", "admit directly", "bypass"),
     "Article IV: no agent joins or collaborates before it has been assessed."),
    (("fake", "make up", "hardcode", "pretend", "simulate the result", "fabricate"),
     "Article VII as amended: a finding must come from an executed protocol."),
    (("vote for me", "let humans vote", "give me a vote", "human vote"),
     "Article IX section 3: humans do not hold a vote in the Forge."),
)


def analyse(store: Store, suggestion: dict) -> dict:
    """Read a pending suggestion and produce a briefing for the administrator.

    Deliberately conservative: it reports what it can actually determine from the
    text and the Ledger, and says plainly when it cannot judge something. It never
    decides — the recommendation is advice the administrator is free to ignore.
    """
    text = suggestion["text"]
    lowered = text.lower()

    asks = [label for keywords, label in ASK_HINTS if _mentions(lowered, keywords)]
    # Domain hints stay as stem matches ("chemi" catches chemistry/chemical), which
    # is what they are written for.
    domains = [domain for domain, keywords in DOMAIN_HINTS.items()
               if any(k in lowered for k in keywords)]
    conflicts = [reason for keywords, reason in CONSTITUTION_FLAGS
                 if any(k in lowered for k in keywords)]

    words = len(re.findall(r"\w+", text))
    specific = words >= 12 and not any(
        v in lowered for v in ("better", "improve it", "more stuff", "etc"))

    labs = [g for g in store.groups() if set(g.get("domains") or []) & set(domains)]

    if conflicts:
        recommendation = "reject"
        reasoning = (
            "I would decline this one. " + conflicts[0] + " Approving it would put the "
            "Forge in conflict with its own constitution, and the agents would be "
            "structurally unable to carry it out even if they wanted to — the action "
            "validator would refuse it."
        )
    elif not specific:
        recommendation = "clarify"
        reasoning = (
            f"This is only {words} words and does not say what would count as done. "
            "I would ask for specifics before approving: agents will read it literally, "
            "and a vague suggestion produces vague work that is hard to judge later."
        )
    else:
        recommendation = "approve"
        reasoning = (
            "I see nothing here that conflicts with the constitution, and it is "
            "specific enough for an agent to act on. "
            + (f"It falls to {labs[0]['name']}, which is already chartered for this "
               f"work." if labs else
               "No existing laboratory is chartered for it, so the agents would need to "
               "charter one or treat it as a general resolution — worth knowing before "
               "you approve.")
        )

    reading = (
        (f"This reads as a request to {asks[0]}." if asks
         else "The ask is not stated as an action, so I read it as a comment or "
              "an observation rather than a request.")
        + (f" It touches {', '.join(domains)}." if domains
           else " It does not name a scientific domain I recognise.")
    )

    cost = (
        f"Within existing capability: {labs[0]['name']} could take it up without new "
        f"machinery." if labs else
        "Cannot be costed precisely from the text. If it needs a protocol the library "
        "does not have, a human would have to review new measuring code before it could "
        "run — that is the slow part."
    )

    risks = (
        conflicts[0] if conflicts else
        ("Low. The worst case is agents spend a few ticks on something you did not mean."
         if specific else
         "Being misread. Agents act on the literal text, and this text has room in it.")
    )

    return {
        "suggestion_id": suggestion["event_id"],
        "reading": reading,
        "domains": domains,
        "constitution": conflicts[0] if conflicts else "No conflict that I can see.",
        "cost": cost,
        "risks": risks,
        "recommendation": recommendation,
        "reasoning": reasoning,
    }
