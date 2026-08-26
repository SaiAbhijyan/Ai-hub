"""The Ledger: append-only, hash-chained event store, plus its projections.

Article II of the constitution is implemented here. `append()` is the only way
anything enters the Forge; every projection table is derived from the event in
the same transaction, and `rebuild_projections()` can re-derive all state from
the chain alone.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

GENESIS_HASH = "0" * 64

DOMAINS = ["reasoning", "coding", "research", "communication", "coordination", "judgment"]

PROJECTION_TABLES = [
    "agents", "wgroups", "memberships", "messages", "proposals", "votes",
    "experiments", "assessments", "capabilities", "artifacts", "suggestions", "drills",
]


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
                " avatar_seed, standing, examiner_domains, joined_tick, joined_event)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (p["id"], p["name"], p["profession"], json.dumps(p["interests"]),
                 json.dumps(p["personality"]), p["style"], p["bio"], p.get("avatar_seed", p["id"]),
                 p.get("standing", "candidate"), json.dumps(p.get("examiner_domains", [])),
                 tick, e["id"]))
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

        elif t == "charter_group":
            c.execute(
                "INSERT INTO wgroups (id, name, goal, charter, thresholds, founded_tick, status)"
                " VALUES (?,?,?,?,?,?, 'active')",
                (p["id"], p["name"], p["goal"], p["charter"],
                 canonical(p.get("thresholds", {})), tick))
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
                " opened_tick, status) VALUES (?,?,?,?,?,?,?, 'running')",
                (p["id"], p["group_id"], e["actor_id"], p["title"], p["hypothesis"],
                 p["method"], tick))

        elif t == "record_result":
            c.execute("UPDATE experiments SET status=?, findings=?, closed_tick=? WHERE id=?",
                      (p["status"], p["findings"], tick, p["experiment_id"]))

        elif t == "open_assessment":
            c.execute(
                "INSERT INTO assessments (id, candidate_id, examiner_id, domain, tasks,"
                " opened_tick, status) VALUES (?,?,?,?,?,?, 'open')",
                (p["id"], p["candidate_id"], e["actor_id"], p["domain"],
                 json.dumps(p["tasks"]), tick))

        elif t == "submit_answers":
            c.execute("UPDATE assessments SET answers=?, status='answered' WHERE id=?",
                      (json.dumps(p["answers"]), p["assessment_id"]))

        elif t == "grade_assessment":
            c.execute("UPDATE assessments SET score=?, notes=?, status='graded', graded_tick=?"
                      " WHERE id=?", (p["score"], p.get("notes", ""), tick, p["assessment_id"]))
            row = c.execute("SELECT candidate_id, domain FROM assessments WHERE id=?",
                            (p["assessment_id"],)).fetchone()
            if row:
                c.execute(
                    "INSERT INTO capabilities (agent_id, domain, score, assessment_id, tick, event_id)"
                    " VALUES (?,?,?,?,?,?)",
                    (row["candidate_id"], row["domain"], p["score"], p["assessment_id"],
                     tick, e["id"]))

        elif t == "run_drill":
            c.execute("INSERT INTO drills (event_id, mentor_id, trainee_id, domain, notes, tick)"
                      " VALUES (?,?,?,?,?,?)",
                      (e["id"], e["actor_id"], p["trainee_id"], p["domain"], p["notes"], tick))

        elif t == "publish_artifact":
            c.execute(
                "INSERT INTO artifacts (id, title, abstract, content, content_hash, version,"
                " supersedes, authors, group_id, tick) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (p["id"], p["title"], p["abstract"], p["content"], p["content_hash"],
                 p.get("version", 1), p.get("supersedes"), json.dumps(p["authors"]),
                 p.get("group_id"), tick))

        elif t == "suggestion_submitted":
            c.execute("INSERT INTO suggestions (event_id, author, text, tick, status)"
                      " VALUES (?,?,?,?, 'new')", (e["id"], p["author"], p["text"], tick))

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
        return [dict(r, thresholds=json.loads(r["thresholds"])) for r in rows]

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

    def experiments(self, group_id: str | None = None, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM experiments"
        conds, args = [], []
        if group_id:
            conds.append("group_id=?")
            args.append(group_id)
        if status:
            conds.append("status=?")
            args.append(status)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY opened_tick DESC", args)]

    def experiment(self, xid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM experiments WHERE id=?", (xid,)).fetchone()
        return dict(row) if row else None

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
        return [dict(r, tasks=json.loads(r["tasks"]), answers=json.loads(r["answers"]))
                for r in rows]

    def assessment(self, aid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM assessments WHERE id=?", (aid,)).fetchone()
        if not row:
            return None
        return dict(row, tasks=json.loads(row["tasks"]), answers=json.loads(row["answers"]))

    def artifacts(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM artifacts ORDER BY tick DESC")
        return [dict(r, authors=json.loads(r["authors"])) for r in rows]

    def artifact(self, aid: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM artifacts WHERE id=?", (aid,)).fetchone()
        return dict(row, authors=json.loads(row["authors"])) if row else None

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
