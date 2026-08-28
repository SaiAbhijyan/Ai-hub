"""The Ledger: append-only, hash-chained event store, plus its projections.

Article II of the constitution is implemented here. `append()` is the only way
anything enters the Forge; every projection table is derived from the event in
the same transaction, and `rebuild_projections()` can re-derive all state from
the chain alone.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

GENESIS_HASH = "0" * 64

DOMAINS = ["reasoning", "coding", "research", "communication", "coordination",
           "judgment", "experiment design", "constitutional judgment"]

# What counts as an agent doing something.
#
# Activity is a fact on the Ledger, not an animation: an agent is active because
# it authored one of these, and idle because it did not.
#
# The rule is simply *did the agent do it, or was it done to them*. Everything an
# agent may author counts; everything the engine writes about an agent —
# promotion, appointment, a refused paper, a lapsed post, an idle notice — does
# not, because counting those would report an agent as busy for sitting still
# while the institution worked around it. A test holds this equal to the union of
# `actions.ALLOWED`, so a new agent action cannot quietly fall out of the
# definition.
ACTIVITY_ACTIONS = (
    "post_message", "post_commons", "update_profile",          # speech and self
    "cast_vote", "create_proposal",                            # the Chamber
    "open_assessment", "submit_answers", "grade_assessment",   # the Academy
    "run_drill",                                               # teaching
    "propose_protocol", "admit_protocol", "refuse_protocol",   # the library
    "create_experiment", "record_result",                      # the bench
    "publish_artifact",                                        # the archive
    "join_group", "acknowledge_suggestion",                    # joining, answering
    "aide_analysis",                                           # the aide's whole job
)

PROJECTION_TABLES = [
    "agents", "wgroups", "memberships", "messages", "proposals", "votes",
    "experiments", "assessments", "capabilities", "artifacts", "suggestions", "drills",
    "commons", "aide_analyses",
]


def remove_tree(path: str, attempts: int = 10, pause: float = 0.05) -> None:
    """Delete a directory, retrying briefly. The companion to `Store.close()`.

    Closing the connection is what actually releases the database files; this
    only covers the short window on Windows where a virus scanner or the search
    indexer still holds a handle on a file that was open a moment ago.

    Bounded on purpose — ten attempts, fifty milliseconds apart, half a second at
    worst — and never an unbounded wait. A directory that still will not go is
    left behind rather than raised: by the time cleanup runs the caller's work is
    finished, and throwing away a completed result over a failed delete is
    precisely the defect this exists to remove.
    """
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if attempt == attempts - 1:
                shutil.rmtree(path, ignore_errors=True)
                return
            time.sleep(pause)


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_hash(eid: int, tick: int, ts: str, actor_id: str, action_type: str,
               payload_json: str, prev_hash: str) -> str:
    material = f"{eid}|{tick}|{ts}|{actor_id}|{action_type}|{payload_json}|{prev_hash}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        schema = (Path(__file__).parent / "schema.sql").read_text()
        with self._lock, self.conn:
            self.conn.executescript(schema)
        # Called with the event dict after each successful append (e.g. SSE fanout).
        self.listeners: list[Callable[[dict], None]] = []

    def close(self) -> None:
        """Release the database files.

        In WAL mode SQLite holds three handles — the database, the `-wal` and the
        `-shm` — and keeps them until the connection is closed. POSIX lets you
        unlink an open file, so on Linux a caller can get away with never calling
        this; Windows refuses, and a throwaway Store whose directory is then
        deleted fails with WinError 32. The Store owns the connection, so the
        Store is what closes it.

        Idempotent: closing twice is not an error, so a `finally` may call it
        without first checking.
        """
        self.listeners.clear()
        with self._lock:
            self.conn.close()

    # ------------------------------------------------------------------ ledger

    def last_event(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 1").fetchone()

    def next_id(self) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM events").fetchone()
        return row["m"] + 1

    def append(self, actor_id: str, action_type: str, payload: dict,
               tick: int | None = None) -> dict:
        """Append one event to the chain and apply its projection atomically."""
        with self._lock:
            if tick is None:
                tick = self.current_tick()
            last = self.last_event()
            prev_hash = last["hash"] if last else GENESIS_HASH
            eid = (last["id"] + 1) if last else 1
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            payload_json = canonical(payload)
            h = event_hash(eid, tick, ts, actor_id, action_type, payload_json, prev_hash)
            with self.conn:
                self.conn.execute(
                    "INSERT INTO events (id, tick, ts, actor_id, action_type, payload, prev_hash, hash)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (eid, tick, ts, actor_id, action_type, payload_json, prev_hash, h),
                )
                event = {"id": eid, "tick": tick, "ts": ts, "actor_id": actor_id,
                         "action_type": action_type, "payload": payload,
                         "prev_hash": prev_hash, "hash": h}
                self._apply(event)
        for fn in list(self.listeners):
            try:
                fn(event)
            except Exception:
                pass
        return event

    def verify_chain(self) -> dict:
        """Re-walk the chain, recomputing every hash. The auditability guarantee."""
        prev = GENESIS_HASH
        expected_id = 1
        n = 0
        for row in self.conn.execute("SELECT * FROM events ORDER BY id"):
            if row["id"] != expected_id:
                return {"ok": False, "checked": n, "error": f"gap in chain at event {expected_id}"}
            if row["prev_hash"] != prev:
                return {"ok": False, "checked": n, "error": f"broken link at event {row['id']}"}
            h = event_hash(row["id"], row["tick"], row["ts"], row["actor_id"],
                           row["action_type"], row["payload"], row["prev_hash"])
            if h != row["hash"]:
                return {"ok": False, "checked": n, "error": f"hash mismatch at event {row['id']}"}
            prev = row["hash"]
            expected_id += 1
            n += 1
        return {"ok": True, "checked": n, "error": None}

    def rebuild_projections(self) -> int:
        """Drop all derived state and replay the chain. Proves Article II §4."""
        with self._lock, self.conn:
            for table in PROJECTION_TABLES:
                self.conn.execute(f"DELETE FROM {table}")
            self.conn.execute("DELETE FROM meta WHERE key != 'tick'")
            n = 0
            for row in self.conn.execute("SELECT * FROM events ORDER BY id"):
                event = dict(row)
                event["payload"] = json.loads(row["payload"])
                self._apply(event)
                n += 1
        return n

    # ------------------------------------------------------------------ meta

    def current_tick(self) -> int:
        row = self.conn.execute("SELECT value FROM meta WHERE key='tick'").fetchone()
        return int(row["value"]) if row else 0

    def set_tick(self, tick: int) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES ('tick', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(tick),))

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # ------------------------------------------------------------------ projections

    def _apply(self, e: dict) -> None:
        p = e["payload"]
        t = e["action_type"]
        c = self.conn
        tick = e["tick"]

        if t == "ratify_constitution":
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('constitution_version', ?)",
                      (p["version"],))
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('constitution_text', ?)",
                      (p["text"],))

        elif t == "constitution_amended":
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('constitution_version', ?)",
                      (p["version"],))
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('constitution_text', ?)",
                      (p["text"],))

        elif t == "found_agent":
            c.execute(
                "INSERT INTO agents (id, name, profession, interests, personality, style, bio,"
                " avatar_seed, standing, examiner_domains, aptitude, joined_tick,"
                " joined_event) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (p["id"], p["name"], p["profession"], json.dumps(p["interests"]),
                 json.dumps(p["personality"]), p["style"], p["bio"], p.get("avatar_seed", p["id"]),
                 p.get("standing", "candidate"), json.dumps(p.get("examiner_domains", [])),
                 canonical(p.get("aptitude", {})), tick, e["id"]))
            for domain, score in p.get("initial_capabilities", {}).items():
                c.execute(
                    "INSERT INTO capabilities (agent_id, domain, score, assessment_id, tick, event_id)"
                    " VALUES (?,?,?,NULL,?,?)", (p["id"], domain, score, tick, e["id"]))

        elif t == "update_profile":
            if "bio" in p:
                c.execute("UPDATE agents SET bio=? WHERE id=?", (p["bio"], e["actor_id"]))
            if "interests" in p:
                c.execute("UPDATE agents SET interests=? WHERE id=?",
                          (json.dumps(p["interests"]), e["actor_id"]))

        elif t == "agent_promoted":
            c.execute("UPDATE agents SET standing='member' WHERE id=? AND standing='candidate'",
                      (p["agent_id"],))

        elif t == "examiner_appointed":
            row = c.execute("SELECT examiner_domains FROM agents WHERE id=?",
                            (p["agent_id"],)).fetchone()
            if row:
                domains = sorted(set(json.loads(row["examiner_domains"]) + p["domains"]))
                c.execute("UPDATE agents SET standing='examiner', examiner_domains=? WHERE id=?",
                          (json.dumps(domains), p["agent_id"]))

        elif t == "examiner_lapsed":
            # The post goes in one domain only. An agent left examining nothing
            # is a member again: Article III §2 defines an examiner as a member
            # *additionally* empowered, and it is no longer empowered anywhere.
            row = c.execute("SELECT examiner_domains FROM agents WHERE id=?",
                            (p["agent_id"],)).fetchone()
            if row:
                domains = [d for d in json.loads(row["examiner_domains"])
                           if d != p["domain"]]
                c.execute("UPDATE agents SET examiner_domains=?, standing=?"
                          " WHERE id=? AND standing='examiner'",
                          (json.dumps(domains),
                           "examiner" if domains else "member", p["agent_id"]))

        elif t == "charter_group":
            c.execute(
                "INSERT INTO wgroups (id, name, goal, charter, thresholds, domains, kind,"
                " founded_tick, status) VALUES (?,?,?,?,?,?,?,?, 'active')",
                (p["id"], p["name"], p["goal"], p["charter"],
                 canonical(p.get("thresholds", {})), canonical(p.get("domains", [])),
                 p.get("kind", "group"), tick))
            for i, agent_id in enumerate(p.get("members", [])):
                c.execute(
                    "INSERT OR IGNORE INTO memberships (group_id, agent_id, role, joined_tick)"
                    " VALUES (?,?,?,?)",
                    (p["id"], agent_id, "lead" if i == 0 else "member", tick))

        elif t == "join_group":
            c.execute("INSERT OR IGNORE INTO memberships (group_id, agent_id, role, joined_tick)"
                      " VALUES (?,?, 'member', ?)", (p["group_id"], e["actor_id"], tick))

        elif t == "post_message":
            c.execute("INSERT INTO messages (event_id, group_id, agent_id, tick, text, reply_to)"
                      " VALUES (?,?,?,?,?,?)",
                      (e["id"], p.get("group_id"), e["actor_id"], tick, p["text"],
                       p.get("reply_to")))

        elif t == "create_proposal":
            c.execute(
                "INSERT INTO proposals (id, kind, title, body, params, author_id, opened_tick,"
                " closes_tick, status) VALUES (?,?,?,?,?,?,?,?, 'open')",
                (p["id"], p["kind"], p["title"], p["body"], canonical(p.get("params", {})),
                 e["actor_id"], tick, p["closes_tick"]))

        elif t == "cast_vote":
            c.execute("INSERT OR IGNORE INTO votes (proposal_id, agent_id, choice, reason, tick)"
                      " VALUES (?,?,?,?,?)",
                      (p["proposal_id"], e["actor_id"], p["choice"], p.get("reason", ""), tick))

        elif t == "proposal_closed":
            c.execute("UPDATE proposals SET status=?, tally=? WHERE id=?",
                      (p["outcome"], canonical(p["tally"]), p["proposal_id"]))

        elif t == "create_experiment":
            c.execute(
                "INSERT INTO experiments (id, group_id, author_id, title, hypothesis, method,"
                " opened_tick, status, domain, protocol_id, params)"
                " VALUES (?,?,?,?,?,?,?, 'running',?,?,?)",
                (p["id"], p["group_id"], e["actor_id"], p["title"], p["hypothesis"],
                 p["method"], tick, p.get("domain", ""), p.get("protocol_id", ""),
                 canonical(p.get("params", {}))))

        elif t == "record_result":
            # Everything written here came back from an actual protocol run.
            c.execute(
                "UPDATE experiments SET status=?, findings=?, closed_tick=?, results=?,"
                " supported=?, code_hash=?, result_hash=?, environment=?, elapsed_seconds=?"
                " WHERE id=?",
                (p["status"], p["findings"], tick, canonical(p.get("results", {})),
                 1 if p.get("supported") else 0, p.get("code_hash", ""),
                 p.get("result_hash", ""), canonical(p.get("environment", {})),
                 float(p.get("elapsed_seconds", 0)), p["experiment_id"]))

        elif t == "open_assessment":
            items = p.get("items", [])
            c.execute(
                "INSERT INTO assessments (id, candidate_id, examiner_id, domain, tasks,"
                " items, item_ids, opened_tick, status, sitting, band)"
                " VALUES (?,?,?,?,?,?,?,?, 'open', ?, ?)",
                (p["id"], p["candidate_id"], e["actor_id"], p["domain"],
                 canonical(p["tasks"]), canonical(items),
                 canonical([i["id"] for i in items]), tick,
                 int(p.get("sitting", 1)), int(p.get("band", 1))))

        elif t == "submit_answers":
            c.execute("UPDATE assessments SET answers=?, status='answered' WHERE id=?",
                      (canonical(p["answers"]), p["assessment_id"]))

        elif t == "grade_assessment":
            c.execute("UPDATE assessments SET score=?, notes=?, marks=?, status='graded',"
                      " graded_tick=? WHERE id=?",
                      (p["score"], p.get("notes", ""), canonical(p.get("marks", [])),
                       tick, p["assessment_id"]))
            row = c.execute("SELECT candidate_id, domain FROM assessments WHERE id=?",
                            (p["assessment_id"],)).fetchone()
            if row:
                c.execute(
                    "INSERT INTO capabilities (agent_id, domain, score, assessment_id, tick, event_id)"
                    " VALUES (?,?,?,?,?,?)",
                    (row["candidate_id"], row["domain"], p["score"], p["assessment_id"],
                     tick, e["id"]))

        elif t == "propose_protocol":
            c.execute(
                "INSERT OR REPLACE INTO protocol_admissions (protocol_id, proposer_id,"
                " question, hypothesis, falsifier, pass_rule, baseline, status,"
                " proposed_tick, proposed_event) VALUES (?,?,?,?,?,?,?, 'proposed',?,?)",
                (p["protocol_id"], e["actor_id"], p.get("question", ""),
                 p.get("hypothesis", ""), p.get("falsifier", ""),
                 p.get("pass_rule", ""), p.get("baseline", ""), tick, e["id"]))

        elif t in ("admit_protocol", "refuse_protocol"):
            c.execute(
                "UPDATE protocol_admissions SET status=?, decided_by=?,"
                " decision_reason=?, ground=?, decided_tick=?, decided_event=?"
                " WHERE protocol_id=?",
                ("admitted" if t == "admit_protocol" else "refused", e["actor_id"],
                 p.get("reason", ""), p.get("ground", ""), tick, e["id"],
                 p["protocol_id"]))

        elif t == "run_drill":
            c.execute("INSERT INTO drills (event_id, mentor_id, trainee_id, domain, notes, tick)"
                      " VALUES (?,?,?,?,?,?)",
                      (e["id"], e["actor_id"], p["trainee_id"], p["domain"], p["notes"], tick))

        elif t == "publish_artifact":
            c.execute(
                "INSERT INTO artifacts (id, title, abstract, content, content_hash, version,"
                " supersedes, authors, group_id, tick, domain, kind, protocol_id,"
                " experiment_id, result_hash, data, supported)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (p["id"], p["title"], p["abstract"], p["content"], p["content_hash"],
                 p.get("version", 1), p.get("supersedes"), canonical(p["authors"]),
                 p.get("group_id"), tick, p.get("domain", ""), p.get("kind", "paper"),
                 p.get("protocol_id", ""), p.get("experiment_id", ""),
                 p.get("result_hash", ""), canonical(p.get("data", {})),
                 None if p.get("supported") is None else (1 if p["supported"] else 0)))

        elif t == "post_commons":
            c.execute("INSERT INTO commons (event_id, agent_id, topic, text, mentions, tick)"
                      " VALUES (?,?,?,?,?,?)",
                      (e["id"], e["actor_id"], p["topic"], p["text"],
                       canonical(p.get("mentions", [])), tick))

        elif t == "suggestion_submitted":
            # Article IX as amended: nothing reaches the agents until the
            # administrator has approved it.
            c.execute("INSERT INTO suggestions (event_id, author, text, tick, status)"
                      " VALUES (?,?,?,?, 'pending_admin')",
                      (e["id"], p["author"], p["text"], tick))

        elif t == "aide_analysis":
            c.execute(
                "INSERT OR REPLACE INTO aide_analyses (event_id, suggestion_id, reading,"
                " domains, constitution, cost, risks, recommendation, reasoning, tick)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (e["id"], p["suggestion_id"], p["reading"], canonical(p.get("domains", [])),
                 p.get("constitution", ""), p.get("cost", ""), p.get("risks", ""),
                 p.get("recommendation", ""), p.get("reasoning", ""), tick))

        elif t == "suggestion_decided":
            c.execute(
                "UPDATE suggestions SET status=?, admin_note=?, decided_tick=?,"
                " approved_text=? WHERE event_id=?",
                ("new" if p["decision"] == "approved" else "rejected",
                 p.get("note", ""), tick, p.get("approved_text", ""),
                 p["suggestion_id"]))

        elif t == "acknowledge_suggestion":
            c.execute("UPDATE suggestions SET status='acknowledged', response=?, responder_id=?"
                      " WHERE event_id=?",
                      (p["response"], e["actor_id"], p["suggestion_event_id"]))

    # ------------------------------------------------------------------ queries

    def agent(self, agent_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        return self._agent_dict(row) if row else None

    def agents(self, standing: str | None = None) -> list[dict]:
        q = "SELECT * FROM agents"
        args: tuple = ()
        if standing:
            q += " WHERE standing=?"
            args = (standing,)
        return [self._agent_dict(r) for r in self.conn.execute(q + " ORDER BY joined_event", args)]

    @staticmethod
    def _agent_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["interests"] = json.loads(d["interests"])
        d["personality"] = json.loads(d["personality"])
        d["examiner_domains"] = json.loads(d["examiner_domains"])
        d["aptitude"] = json.loads(d.get("aptitude") or "{}")
        return d

    def capabilities_current(self, agent_id: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT domain, score FROM capabilities WHERE agent_id=? ORDER BY event_id",
            (agent_id,))
        out: dict[str, int] = {}
        for r in rows:
            out[r["domain"]] = r["score"]
        return out

    def capability_history(self, agent_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM capabilities WHERE agent_id=? ORDER BY event_id", (agent_id,))]

    def entrance_battery_passed(self, agent_id: str) -> bool:
        """Article IV §3: >= 3 domains examined at >= 60 (assessed, not seeded)."""
        rows = self.conn.execute(
            "SELECT domain, MAX(score) AS best FROM capabilities"
            " WHERE agent_id=? AND assessment_id IS NOT NULL GROUP BY domain", (agent_id,))
        return sum(1 for r in rows if r["best"] >= 60) >= 3

    def groups(self, status: str = "active") -> list[dict]:
        rows = self.conn.execute("SELECT * FROM wgroups WHERE status=? ORDER BY founded_tick",
                                 (status,))
        return [self._group_dict(r) for r in rows]

    def group(self, group_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM wgroups WHERE id=?", (group_id,)).fetchone()
        return self._group_dict(row) if row else None

    @staticmethod
    def _group_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["thresholds"] = json.loads(d["thresholds"])
        d["domains"] = json.loads(d.get("domains") or "[]")
        return d

    def group_members(self, group_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT a.*, m.role AS group_role FROM memberships m JOIN agents a ON a.id=m.agent_id"
            " WHERE m.group_id=? ORDER BY m.joined_tick", (group_id,))
        out = []
        for r in rows:
            d = self._agent_dict(r)
            d["group_role"] = r["group_role"]
            out.append(d)
        return out

    def agent_groups(self, agent_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT g.*, m.role AS group_role FROM memberships m JOIN wgroups g ON g.id=m.group_id"
            " WHERE m.agent_id=? AND g.status='active' ORDER BY m.joined_tick", (agent_id,))
        return [dict(self._group_dict(r), group_role=r["group_role"]) for r in rows]

    def messages(self, group_id: str | None = None, limit: int = 50) -> list[dict]:
        if group_id is None:
            rows = self.conn.execute(
                "SELECT * FROM messages ORDER BY event_id DESC LIMIT ?", (limit,))
        else:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE group_id=? ORDER BY event_id DESC LIMIT ?",
                (group_id, limit))
        return [dict(r) for r in rows][::-1]

    def proposals(self, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM proposals"
        args: tuple = ()
        if status == "closed":
            q += " WHERE status != 'open'"
        elif status:
            q += " WHERE status=?"
            args = (status,)
        rows = self.conn.execute(q + " ORDER BY opened_tick DESC", args)
        return [dict(r, params=json.loads(r["params"]), tally=json.loads(r["tally"] or "{}"))
                for r in rows]

    def proposal(self, pid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        return dict(row, params=json.loads(row["params"]), tally=json.loads(row["tally"] or "{}"))

    def votes_for(self, pid: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM votes WHERE proposal_id=? ORDER BY tick", (pid,))]

    def has_voted(self, pid: str, agent_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM votes WHERE proposal_id=? AND agent_id=?",
                                 (pid, agent_id)).fetchone() is not None

    @staticmethod
    def _experiment_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        for field in ("params", "results", "environment"):
            d[field] = json.loads(d.get(field) or "{}")
        d["supported"] = None if d["supported"] is None else bool(d["supported"])
        return d

    def experiments(self, group_id: str | None = None, status: str | None = None,
                    domain: str | None = None) -> list[dict]:
        q = "SELECT * FROM experiments"
        conds, args = [], []
        if group_id:
            conds.append("group_id=?")
            args.append(group_id)
        if status:
            conds.append("status=?")
            args.append(status)
        if domain:
            conds.append("domain=?")
            args.append(domain)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        return [self._experiment_dict(r)
                for r in self.conn.execute(q + " ORDER BY opened_tick DESC", args)]

    def experiment(self, xid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM experiments WHERE id=?", (xid,)).fetchone()
        return self._experiment_dict(row) if row else None

    def experiments_for_protocol(self, protocol_id: str) -> list[dict]:
        """Every run of one protocol, newest first — the history the calibration
        rules are decided against."""
        rows = self.conn.execute(
            "SELECT * FROM experiments WHERE protocol_id=? ORDER BY opened_tick DESC,"
            " id DESC", (protocol_id,))
        return [self._experiment_dict(r) for r in rows]

    # ------------------------------------------------------- protocol admission

    def protocol_admission(self, protocol_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM protocol_admissions WHERE protocol_id=?",
            (protocol_id,)).fetchone()
        return dict(row) if row else None

    def protocol_admissions(self, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM protocol_admissions"
        args: list = []
        if status:
            q += " WHERE status=?"
            args.append(status)
        return [dict(r) for r in
                self.conn.execute(q + " ORDER BY proposed_event", args)]

    def is_admitted(self, protocol_id: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM protocol_admissions WHERE protocol_id=?",
            (protocol_id,)).fetchone()
        return bool(row) and row["status"] == "admitted"

    def commons(self, limit: int = 60, agent_id: str | None = None,
                topic: str | None = None) -> list[dict]:
        q = "SELECT * FROM commons"
        conds, args = [], []
        if agent_id:
            conds.append("agent_id=?")
            args.append(agent_id)
        if topic:
            conds.append("topic=?")
            args.append(topic)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        args.append(limit)
        rows = self.conn.execute(q + " ORDER BY event_id DESC LIMIT ?", args)
        return [dict(r, mentions=json.loads(r["mentions"])) for r in rows]

    def aide_analysis(self, suggestion_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM aide_analyses WHERE suggestion_id=? ORDER BY event_id DESC LIMIT 1",
            (suggestion_id,)).fetchone()
        return dict(row, domains=json.loads(row["domains"])) if row else None

    def assessments(self, status: str | None = None, candidate_id: str | None = None,
                    examiner_id: str | None = None) -> list[dict]:
        q = "SELECT * FROM assessments"
        conds, args = [], []
        if status:
            conds.append("status=?")
            args.append(status)
        if candidate_id:
            conds.append("candidate_id=?")
            args.append(candidate_id)
        if examiner_id:
            conds.append("examiner_id=?")
            args.append(examiner_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        rows = self.conn.execute(q + " ORDER BY opened_tick DESC", args)
        return [self._assessment_dict(r) for r in rows]

    @staticmethod
    def _assessment_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        for field, default in (("tasks", "[]"), ("answers", "[]"), ("items", "[]"),
                               ("item_ids", "[]"), ("marks", "[]")):
            d[field] = json.loads(d.get(field) or default)
        return d

    def assessment(self, aid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM assessments WHERE id=?", (aid,)).fetchone()
        return self._assessment_dict(row) if row else None

    @staticmethod
    def _artifact_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["authors"] = json.loads(d["authors"])
        d["data"] = json.loads(d.get("data") or "{}")
        d["supported"] = None if d["supported"] is None else bool(d["supported"])
        return d

    def artifacts(self, domain: str | None = None, kind: str | None = None,
                  protocol_id: str | None = None) -> list[dict]:
        q = "SELECT * FROM artifacts"
        conds, args = [], []
        if domain:
            conds.append("domain=?")
            args.append(domain)
        if kind:
            conds.append("kind=?")
            args.append(kind)
        if protocol_id:
            conds.append("protocol_id=?")
            args.append(protocol_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        rows = self.conn.execute(q + " ORDER BY tick DESC", args)
        return [self._artifact_dict(r) for r in rows]

    def artifact(self, aid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM artifacts WHERE id=?", (aid,)).fetchone()
        return self._artifact_dict(row) if row else None

    def artifact_domain_counts(self) -> list[tuple[str, int]]:
        return [(r["domain"], r["n"]) for r in self.conn.execute(
            "SELECT domain, COUNT(*) AS n FROM artifacts WHERE domain != ''"
            " GROUP BY domain ORDER BY n DESC")]

    def suggestions(self, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM suggestions"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY event_id DESC", args)]

    def drills_for(self, agent_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM drills WHERE trainee_id=? OR mentor_id=? ORDER BY event_id DESC",
            (agent_id, agent_id))]

    def events(self, limit: int = 100, before: int | None = None,
               actor_id: str | None = None) -> list[dict]:
        q = "SELECT * FROM events"
        conds, args = [], []
        if before is not None:
            conds.append("id < ?")
            args.append(before)
        if actor_id:
            conds.append("actor_id=?")
            args.append(actor_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = self.conn.execute(q, args)
        return [dict(r, payload=json.loads(r["payload"])) for r in rows]

    def experiment_events(self, xid: str) -> list[dict]:
        """Every Ledger event that touches one experiment, oldest first — its
        registration, its result, and any paper that cites it. This is the audit
        trail behind what the experiment page shows."""
        rows = self.conn.execute(
            "SELECT * FROM events WHERE action_type IN "
            "('create_experiment','record_result','publish_artifact') "
            "AND payload LIKE ? ORDER BY id", (f'%"{xid}"%',))
        out = []
        for r in rows:
            payload = json.loads(r["payload"])
            if xid in (payload.get("id"), payload.get("experiment_id")):
                out.append(dict(r, payload=payload))
        return out

    def last_activity(self, agent_id: str) -> dict | None:
        """The most recent thing this agent actually *did*, or None.

        Only the doing types in ACTIVITY_ACTIONS count. Being promoted, being
        appointed, or having a paper refused are things done to an agent, and an
        institution that counted them would report an agent as busy for sitting
        still while the engine worked around it.
        """
        marks = ",".join("?" * len(ACTIVITY_ACTIONS))
        row = self.conn.execute(
            f"SELECT id, tick, action_type FROM events WHERE actor_id=?"
            f" AND action_type IN ({marks}) ORDER BY id DESC LIMIT 1",
            (agent_id, *ACTIVITY_ACTIONS)).fetchone()
        return dict(row) if row else None

    def activity(self, agent_id: str, window: int | None = None) -> dict:
        """Whether an agent is active, and the event that says so."""
        from .engine import IDLE_TICKS
        window = IDLE_TICKS if window is None else window
        last = self.last_activity(agent_id)
        now = self.current_tick()
        if last is None:
            return {"state": "never acted", "active": False, "last": None, "age": None}
        age = now - last["tick"]
        return {"state": "active" if age <= window else "idle",
                "active": age <= window, "last": last, "age": age}

    def group_activity(self, group_id: str, window: int | None = None) -> dict:
        """Members of a group, counted by whether they are still working."""
        members = self.group_members(group_id)
        rows = [{**m, "activity": self.activity(m["id"], window)} for m in members]
        active = sum(1 for r in rows if r["activity"]["active"])
        return {"members": rows, "total": len(rows), "active": active,
                "idle": len(rows) - active}

    def group_worked_since(self, group_id: str, since_tick: int,
                           action_types: tuple) -> bool:
        """Has this group's bench been used since `since_tick`?"""
        marks = ",".join("?" * len(action_types))
        row = self.conn.execute(
            f"SELECT 1 FROM events WHERE tick > ? AND action_type IN ({marks})"
            f" AND payload LIKE ? LIMIT 1",
            (since_tick, *action_types, f'%"{group_id}"%')).fetchone()
        return bool(row)

    def idle_notices(self, group_id: str | None = None,
                     since_tick: int | None = None) -> list[dict]:
        """Floor notices that a laboratory has gone quiet."""
        return self._payload_events("lab_idle_notice", since_tick,
                                    lambda p: group_id is None
                                    or p.get("group_id") == group_id)

    def lapse_deferrals(self, agent_id: str, domain: str,
                        since_tick: int | None = None) -> list[dict]:
        return self._payload_events(
            "lapse_deferred", since_tick,
            lambda p: p.get("agent_id") == agent_id and p.get("domain") == domain)

    def examiner_lapses(self, agent_id: str | None = None) -> list[dict]:
        return self._payload_events(
            "examiner_lapsed", None,
            lambda p: agent_id is None or p.get("agent_id") == agent_id)

    def _payload_events(self, action_type: str, since_tick: int | None,
                        keep) -> list[dict]:
        """Events of one type, filtered on their payload.

        These are read straight off the chain rather than projected: nothing
        queries them often enough to earn a table, and reading the events keeps
        the notice and the record identical by construction.
        """
        args: list = [action_type]
        q = "SELECT * FROM events WHERE action_type=?"
        if since_tick is not None:
            q += " AND tick > ?"
            args.append(since_tick)
        out = []
        for r in self.conn.execute(q + " ORDER BY id", args):
            payload = json.loads(r["payload"])
            if keep(payload):
                out.append(dict(r, payload=payload))
        return out

    def last_academy_touch(self, agent_id: str, domain: str) -> dict | None:
        """The last time this agent sat or marked a paper in one domain.

        Either counts. An examiner keeps its hand in by being examined as much as
        by examining, and the constitution asks for use of the domain, not for
        one particular way of using it.

        Matching is on the assessment id read out of the payload, never on a
        substring of it: `asmt-1` appears inside `asmt-12`, and a lapse decided
        by a bad LIKE would take an office off someone who had earned it.
        """
        ids = {r["id"] for r in self.conn.execute(
            "SELECT id FROM assessments WHERE domain=?", (domain,))}
        if not ids:
            return None
        best = None
        for r in self.conn.execute(
                "SELECT id, tick, action_type, payload FROM events"
                " WHERE actor_id=? AND action_type IN"
                " ('submit_answers','grade_assessment','open_assessment')"
                " ORDER BY id DESC", (agent_id,)):
            payload = json.loads(r["payload"])
            aid = payload.get("assessment_id") or payload.get("id")
            if aid in ids:
                best = {"id": r["id"], "tick": r["tick"],
                        "action_type": r["action_type"], "assessment_id": aid}
                break
        return best

    def publication_refusals(self, experiment_id: str | None = None) -> list[dict]:
        """Papers the Forge refused, and why.

        A refusal is as much a part of the record as a publication: an agent that
        tried to bank credit for a calibration rerun should be visible having
        tried. Read off the events rather than projected, because nothing else
        needs to query it.
        """
        rows = self.conn.execute(
            "SELECT * FROM events WHERE action_type='publication_refused' ORDER BY id")
        out = []
        for r in rows:
            payload = json.loads(r["payload"])
            if experiment_id and payload.get("experiment_id") != experiment_id:
                continue
            out.append(dict(r, payload=payload))
        return out

    def event(self, event_id: int) -> dict | None:
        """One event by id, so a citation of the record resolves permanently
        rather than pointing into a paginated list."""
        r = self.conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return dict(r, payload=json.loads(r["payload"])) if r else None

    def event_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]

    def capability_index(self) -> list[dict]:
        """The Forge capability index: the mean of every agent's current domain
        scores, sampled once per tick in which a measurement was recorded.

        Sampling per tick (rather than per event) keeps genesis — where every
        founding record lands at tick 0 — as a single honest starting point
        instead of a meaningless ramp.
        """
        rows = [dict(r) for r in self.conn.execute(
            "SELECT * FROM capabilities ORDER BY event_id")]
        current: dict[tuple[str, str], int] = {}
        by_tick: dict[int, float] = {}
        for r in rows:
            current[(r["agent_id"], r["domain"])] = r["score"]
            by_tick[r["tick"]] = round(sum(current.values()) / len(current), 1)
        return [{"tick": tick, "index": value} for tick, value in sorted(by_tick.items())]

    def counts_for_agent(self, agent_id: str) -> dict[str, int]:
        c = self.conn
        return {
            "messages": c.execute("SELECT COUNT(*) n FROM messages WHERE agent_id=?",
                                  (agent_id,)).fetchone()["n"],
            "proposals": c.execute("SELECT COUNT(*) n FROM proposals WHERE author_id=?",
                                   (agent_id,)).fetchone()["n"],
            "votes": c.execute("SELECT COUNT(*) n FROM votes WHERE agent_id=?",
                               (agent_id,)).fetchone()["n"],
            "experiments": c.execute("SELECT COUNT(*) n FROM experiments WHERE author_id=?",
                                     (agent_id,)).fetchone()["n"],
            "publications": c.execute(
                "SELECT COUNT(*) n FROM artifacts WHERE authors LIKE ?",
                (f'%"{agent_id}"%',)).fetchone()["n"],
            "assessments_taken": c.execute(
                "SELECT COUNT(*) n FROM assessments WHERE candidate_id=? AND status='graded'",
                (agent_id,)).fetchone()["n"],
            "assessments_given": c.execute(
                "SELECT COUNT(*) n FROM assessments WHERE examiner_id=? AND status='graded'",
                (agent_id,)).fetchone()["n"],
        }
