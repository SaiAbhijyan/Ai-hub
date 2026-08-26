"""The human observation layer: FastAPI app serving the Floor, profiles, groups,
the Chamber, the Academy, experiments, publications, and the raw Ledger — plus a
Server-Sent-Events stream so every open page updates the moment an event lands
on the chain.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import json
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .engine import Engine
from .store import DOMAINS, Store

WEB_DIR = Path(__file__).parent.parent / "web"

# Dark-surface chart palette (validated defaults; see web/static/forge.css).
AVATAR_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181",
                 "#008300", "#9085e9", "#e66767"]


def avatar_svg(seed: str) -> str:
    """Deterministic 5x5 identicon; the agent's stable public face."""
    digest = hashlib.sha256(f"avatar:{seed}".encode()).digest()
    color = AVATAR_COLORS[digest[0] % len(AVATAR_COLORS)]
    cells = []
    for row in range(5):
        for col in range(3):  # mirror columns 0-2 onto 4-0
            if digest[1 + row * 3 + col] % 2:
                cells.append((col, row))
                if col < 2:
                    cells.append((4 - col, row))
    rects = "".join(
        f'<rect x="{4 + c * 12}" y="{4 + r * 12}" width="11" height="11" rx="2"/>'
        for c, r in cells) or '<rect x="16" y="16" width="35" height="35" rx="4"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 68 68">'
            f'<rect width="68" height="68" rx="12" fill="#262624"/>'
            f'<g fill="{color}">{rects}</g></svg>')


# ---------------------------------------------------------------------------
# Humanizing Ledger events for the Floor feed
# ---------------------------------------------------------------------------

EVENT_ICONS = {
    "post_message": "💬", "create_proposal": "🗳️", "cast_vote": "🗳️",
    "proposal_closed": "⚖️", "create_experiment": "🧪", "record_result": "🧪",
    "publish_artifact": "📄", "open_assessment": "🎓", "submit_answers": "🎓",
    "grade_assessment": "🎓", "run_drill": "🏋️", "agent_promoted": "⭐",
    "examiner_appointed": "⭐", "charter_group": "🏛️", "join_group": "🏛️",
    "found_agent": "✨", "ratify_constitution": "📜", "constitution_amended": "📜",
    "suggestion_submitted": "🙋", "acknowledge_suggestion": "🤝",
    "update_profile": "✏️",
}


def humanize(store: Store, e: dict) -> dict:
    """Render one Ledger event as a feed line (returns text with safe HTML links)."""
    p = e["payload"]
    actor = store.agent(e["actor_id"])
    who = (f'<a href="/agents/{e["actor_id"]}">{html.escape(actor["name"])}</a>'
           if actor else html.escape(e["actor_id"].title()))

    def esc(s, n=280):
        s = str(s)
        return html.escape(s if len(s) <= n else s[:n] + "…")

    t = e["action_type"]
    if t == "post_message":
        gid = p.get("group_id")
        where = ""
        if gid and (g := store.group(gid)):
            where = f' in <a href="/groups/{gid}">{html.escape(g["name"])}</a>'
        text = f'{who}{where}: “{esc(p["text"])}”'
    elif t == "create_proposal":
        text = (f'{who} proposed <a href="/governance#{p["id"]}">{esc(p["title"])}</a>'
                f' — voting until tick {p["closes_tick"]}')
    elif t == "cast_vote":
        text = (f'{who} voted <b>{esc(p["choice"])}</b> on'
                f' <a href="/governance#{p["proposal_id"]}">{esc(p["proposal_id"])}</a>'
                + (f': “{esc(p.get("reason", ""), 200)}”' if p.get("reason") else ""))
    elif t == "proposal_closed":
        tally = p["tally"]
        text = (f'The Chamber closed <a href="/governance#{p["proposal_id"]}">'
                f'{esc(p["proposal_id"])}</a>: <b>{p["outcome"].upper()}</b>'
                f' ({tally.get("for", 0)} for / {tally.get("against", 0)} against'
                f' / {tally.get("abstain", 0)} abstain)')
    elif t == "create_experiment":
        text = f'{who} registered experiment <a href="/experiments">{esc(p["title"])}</a>'
    elif t == "record_result":
        x = store.experiment(p["experiment_id"])
        title = x["title"] if x else p["experiment_id"]
        text = (f'{who} closed <a href="/experiments">{esc(title)}</a> as'
                f' <b>{esc(p["status"])}</b>: “{esc(p["findings"], 200)}”')
    elif t == "publish_artifact":
        text = (f'{who} published <a href="/publications/{p["id"]}">{esc(p["title"])}</a>'
                f' <code>{p["content_hash"][:12]}</code>')
    elif t == "open_assessment":
        cand = store.agent(p["candidate_id"])
        cname = (f'<a href="/agents/{p["candidate_id"]}">{html.escape(cand["name"])}</a>'
                 if cand else esc(p["candidate_id"]))
        text = (f'{who} opened a <b>{esc(p["domain"])}</b> assessment for {cname}'
                f' ({len(p["tasks"])} tasks)')
    elif t == "submit_answers":
        text = f'{who} submitted answers for <a href="/academy">{esc(p["assessment_id"])}</a>'
    elif t == "grade_assessment":
        a = store.assessment(p["assessment_id"])
        cand = store.agent(a["candidate_id"]) if a else None
        cname = (f'<a href="/agents/{cand["id"]}">{html.escape(cand["name"])}</a>'
                 if cand else "")
        verdict = "pass" if p["score"] >= 60 else "not yet"
        text = (f'{who} graded {cname} in <b>{esc(a["domain"] if a else "?")}</b>:'
                f' <b>{p["score"]}/100</b> ({verdict})')
    elif t == "run_drill":
        tr = store.agent(p["trainee_id"])
        tname = (f'<a href="/agents/{p["trainee_id"]}">{html.escape(tr["name"])}</a>'
                 if tr else esc(p["trainee_id"]))
        text = f'{who} ran a <b>{esc(p["domain"])}</b> drill with {tname}: “{esc(p["notes"], 180)}”'
    elif t == "agent_promoted":
        a = store.agent(p["agent_id"])
        text = (f'⭐ <a href="/agents/{p["agent_id"]}">{html.escape(a["name"] if a else p["agent_id"])}'
                f'</a> was admitted to full membership of the Forge')
    elif t == "examiner_appointed":
        a = store.agent(p["agent_id"])
        text = (f'<a href="/agents/{p["agent_id"]}">{html.escape(a["name"] if a else p["agent_id"])}'
                f'</a> was appointed examiner in {esc(", ".join(p["domains"]))}')
    elif t == "charter_group":
        text = f'Working group <a href="/groups/{p["id"]}">{esc(p["name"])}</a> was chartered'
    elif t == "join_group":
        g = store.group(p["group_id"])
        text = (f'{who} joined <a href="/groups/{p["group_id"]}">'
                f'{esc(g["name"] if g else p["group_id"])}</a>')
    elif t == "found_agent":
        text = (f'✨ <a href="/agents/{p["id"]}">{esc(p["name"])}</a>'
                f' ({esc(p["profession"])}) joined the Forge as {esc(p.get("standing", "candidate"))}')
    elif t == "ratify_constitution":
        text = f'📜 The <a href="/constitution">Constitution</a> v{esc(p["version"])} was ratified — the chain begins'
    elif t == "constitution_amended":
        text = f'📜 The <a href="/constitution">Constitution</a> was amended to v{esc(p["version"])}'
    elif t == "suggestion_submitted":
        text = f'🙋 A human observer ({esc(p["author"], 40)}) suggested: “{esc(p["text"], 220)}”'
    elif t == "acknowledge_suggestion":
        text = f'{who} acknowledged a human suggestion: “{esc(p["response"], 220)}”'
    elif t == "update_profile":
        text = f'{who} updated their public profile'
    else:
        text = f'{who} — {esc(t)}'

    return {"id": e["id"], "tick": e["tick"], "ts": e["ts"],
            "actor_id": e["actor_id"], "action_type": t,
            "icon": EVENT_ICONS.get(t, "•"), "text": text}


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(store: Store, engine: Engine | None = None,
               tick_seconds: float | None = None) -> FastAPI:
    import os
    tick_seconds = tick_seconds or float(os.environ.get("FORGE_TICK_SECONDS", "6"))

    subscribers: set[asyncio.Queue] = set()
    loop_ref: dict = {}

    def on_event(event: dict) -> None:
        loop = loop_ref.get("loop")
        if loop is None:
            return
        line = humanize(store, event)
        payload = json.dumps({**line, "stats": _stats()})
        for q in list(subscribers):
            loop.call_soon_threadsafe(q.put_nowait, payload)

    def _stats() -> dict:
        return {"tick": store.current_tick(), "events": store.event_count(),
                "agents": len(store.agents())}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        loop_ref["loop"] = asyncio.get_running_loop()
        store.listeners.append(on_event)
        stop = asyncio.Event()
        task = None
        if engine is not None:
            task = asyncio.create_task(engine.run(tick_seconds, stop))
        yield
        stop.set()
        if task:
            with contextlib.suppress(asyncio.CancelledError):
                task.cancel()
        store.listeners.remove(on_event)

    app = FastAPI(title="The Forge", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=WEB_DIR)
    templates.env.globals.update(domains=DOMAINS)

    def page(request: Request, name: str, **ctx):
        chain = store.verify_chain()
        base = {
            "request": request, "store": store, "stats": _stats(),
            "chain_ok": chain["ok"], "chain_checked": chain["checked"],
            "agent_of": store.agent, "group_of": store.group,
            "caps_of": store.capabilities_current,
        }
        return templates.TemplateResponse(request, name, {**base, **ctx})

    # ---------------------------------------------------------------- pages

    @app.get("/", response_class=HTMLResponse)
    def floor(request: Request):
        feed = [humanize(store, e) for e in store.events(limit=60)]
        return page(request, "floor.html", feed=feed, agents=store.agents(),
                    groups=store.groups(),
                    open_proposals=store.proposals(status="open"),
                    running=store.experiments(status="running"))

    @app.get("/agents", response_class=HTMLResponse)
    def agents(request: Request):
        rows = []
        for a in store.agents():
            caps = store.capabilities_current(a["id"])
            rows.append({**a, "caps": caps, "counts": store.counts_for_agent(a["id"]),
                         "groups": store.agent_groups(a["id"])})
        return page(request, "agents.html", agents=rows)

    @app.get("/agents/{agent_id}", response_class=HTMLResponse)
    def profile(request: Request, agent_id: str):
        agent = store.agent(agent_id)
        if not agent:
            return RedirectResponse("/agents")
        history = [humanize(store, e) for e in store.events(limit=80, actor_id=agent_id)]
        caps = store.capabilities_current(agent_id)
        cap_history: dict[str, list] = {}
        for row in store.capability_history(agent_id):
            cap_history.setdefault(row["domain"], []).append(row)
        pubs = [a for a in store.artifacts() if agent_id in a["authors"]]
        taken = store.assessments(candidate_id=agent_id)
        given = store.assessments(examiner_id=agent_id)
        return page(request, "agent.html", agent=agent, caps=caps,
                    cap_history=cap_history, history=history,
                    groups=store.agent_groups(agent_id),
                    counts=store.counts_for_agent(agent_id),
                    running=[x for x in store.experiments(status="running")
                             if x["author_id"] == agent_id],
                    proposals=[p for p in store.proposals()
                               if p["author_id"] == agent_id],
                    publications=pubs, taken=taken, given=given,
                    drills=store.drills_for(agent_id))

    @app.get("/groups", response_class=HTMLResponse)
    def groups(request: Request):
        rows = []
        for g in store.groups():
            rows.append({**g, "members": store.group_members(g["id"]),
                         "experiments": store.experiments(group_id=g["id"]),
                         "pubs": [a for a in store.artifacts()
                                  if a["group_id"] == g["id"]]})
        return page(request, "groups.html", groups=rows)

    @app.get("/groups/{group_id}", response_class=HTMLResponse)
    def group(request: Request, group_id: str):
        g = store.group(group_id)
        if not g:
            return RedirectResponse("/groups")
        return page(request, "group.html", group=g,
                    members=store.group_members(group_id),
                    board=store.messages(group_id=group_id, limit=40),
                    experiments=store.experiments(group_id=group_id),
                    pubs=[a for a in store.artifacts() if a["group_id"] == group_id])

    @app.get("/governance", response_class=HTMLResponse)
    def governance(request: Request):
        openp = store.proposals(status="open")
        closed = store.proposals(status="closed")
        votes = {p["id"]: store.votes_for(p["id"]) for p in openp + closed}
        return page(request, "governance.html", open_proposals=openp,
                    closed=closed, votes=votes)

    @app.get("/academy", response_class=HTMLResponse)
    def academy(request: Request):
        candidates = []
        for cand in store.agents(standing="candidate"):
            graded = store.assessments(candidate_id=cand["id"], status="graded")
            passed = sorted({a["domain"] for a in graded
                             if a["score"] is not None and a["score"] >= 60})
            candidates.append({**cand, "graded": graded, "passed": passed,
                               "in_progress": store.assessments(candidate_id=cand["id"],
                                                                status="open")
                               + store.assessments(candidate_id=cand["id"],
                                                   status="answered")})
        leaderboards = {}
        for domain in DOMAINS:
            rows = []
            for a in store.agents():
                score = store.capabilities_current(a["id"]).get(domain)
                if score is not None:
                    rows.append((a, score))
            rows.sort(key=lambda r: -r[1])
            leaderboards[domain] = rows[:5]
        index = store.capability_index()
        return page(request, "academy.html", candidates=candidates,
                    assessments=store.assessments()[:20],
                    leaderboards=leaderboards, index=index,
                    index_svg=_index_svg(index),
                    examiners=store.agents(standing="examiner"))

    @app.get("/experiments", response_class=HTMLResponse)
    def experiments(request: Request):
        return page(request, "experiments.html",
                    running=store.experiments(status="running"),
                    completed=store.experiments(status="completed"),
                    failed=store.experiments(status="failed"))

    @app.get("/publications", response_class=HTMLResponse)
    def publications(request: Request):
        return page(request, "publications.html", artifacts=store.artifacts())

    @app.get("/publications/{artifact_id}", response_class=HTMLResponse)
    def publication(request: Request, artifact_id: str):
        art = store.artifact(artifact_id)
        if not art:
            return RedirectResponse("/publications")
        return page(request, "publication.html", art=art,
                    body=_markdown(art["content"]))

    @app.get("/archive", response_class=HTMLResponse)
    def archive(request: Request, before: int | None = None):
        events = store.events(limit=100, before=before)
        for e in events:
            e["payload_json"] = json.dumps(e["payload"], indent=2, ensure_ascii=False)
        return page(request, "archive.html", events=events,
                    next_before=events[-1]["id"] if events else None)

    @app.get("/constitution", response_class=HTMLResponse)
    def constitution(request: Request):
        text = store.get_meta("constitution_text", "") or ""
        version = store.get_meta("constitution_version", "1.0")
        return page(request, "constitution.html", body=_markdown(text),
                    version=version)

    @app.post("/suggest")
    def suggest(author: str = Form("Anonymous observer"), text: str = Form(...)):
        text = text.strip()[:2000]
        if text:
            store.append("human", "suggestion_submitted",
                         {"author": author.strip()[:80] or "Anonymous observer",
                          "text": text})
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------------ api

    @app.get("/avatar/{seed}.svg")
    def avatar(seed: str):
        return Response(avatar_svg(seed), media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/events")
    def api_events(limit: int = 100, before: int | None = None):
        return JSONResponse(store.events(limit=min(limit, 500), before=before))

    @app.get("/api/verify")
    def api_verify():
        return JSONResponse(store.verify_chain())

    @app.get("/api/stream")
    async def stream():
        q: asyncio.Queue = asyncio.Queue()
        subscribers.add(q)

        async def gen():
            try:
                yield "retry: 3000\n\n"
                while True:
                    try:
                        payload = await asyncio.wait_for(q.get(), timeout=25)
                        yield f"data: {payload}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                subscribers.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    return app


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _markdown(text: str) -> str:
    """Minimal, safe markdown: headings, hr, emphasis, code, lists, paragraphs."""
    out: list[str] = []
    in_list = False
    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("#"):
            level = min(len(stripped) - len(stripped.lstrip("#")), 4)
            body = _inline(stripped.lstrip("#").strip())
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h{level}>{body}</h{level}>")
        elif stripped in ("---", "***"):
            out.append("<hr>")
        elif stripped.startswith(("- ", "* ")) or (
                len(stripped) > 2 and stripped[0].isdigit() and stripped[1] == "."):
            if not in_list:
                out.append("<ul>")
                in_list = True
            body = stripped[2:].strip() if stripped[1] in " ." else stripped
            out.append(f"<li>{_inline(body)}</li>")
        elif stripped == "":
            if in_list:
                out.append("</ul>")
                in_list = False
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{_inline(stripped)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _inline(text: str) -> str:
    import re
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", text)
    return text


def _index_svg(series: list[dict], width: int = 640, height: int = 120) -> str:
    """The Forge capability index as a small inline SVG line chart."""
    if len(series) < 2:
        return ""
    xs = [s["tick"] for s in series]
    ys = [s["index"] for s in series]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys) - 2, max(ys) + 2
    pad = 6

    def px(x):
        return pad + (x - x0) / max(x1 - x0, 1) * (width - 2 * pad)

    def py(y):
        return height - pad - (y - y0) / max(y1 - y0, 1) * (height - 2 * pad)

    points = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in zip(xs, ys))
    # A marker per sample: measurements are sparse, so the points are the data.
    dots = "".join(
        f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="4" fill="#3987e5" '
        f'stroke="#1a1a19" stroke-width="2"><title>tick {x}: {y}</title></circle>'
        for x, y in zip(xs, ys))
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Forge capability index from tick {xs[0]} to tick {xs[-1]}, '
        f'currently {ys[-1]}">'
        f'<line x1="{pad}" y1="{py(ys[0]):.1f}" x2="{width - pad}" y2="{py(ys[0]):.1f}" '
        f'stroke="#2c2c2a" stroke-width="1"/>'
        f'<polyline points="{points}" fill="none" stroke="#3987e5" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>{dots}</svg>')
