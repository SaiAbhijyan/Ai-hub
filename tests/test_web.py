import os

import pytest
from fastapi.testclient import TestClient

os.environ["FORGE_MODE"] = "sim"

from forge.agents import SimulatedAgent
from forge.engine import Engine
from forge.seed import seed
from forge.server import _markdown, avatar_svg, create_app, humanize
from forge.store import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "forge.db")
    seed(s)
    engine = Engine(s, SimulatedAgent())
    for _ in range(30):
        engine.tick()
    return s


@pytest.fixture()
def client(store):
    # engine=None: the app serves a static snapshot, so tests stay deterministic.
    with TestClient(create_app(store, engine=None)) as c:
        yield c


def test_every_page_renders(client, store):
    artifact = next((a["id"] for a in store.artifacts()), None)
    closed = next((x["id"] for x in store.experiments() if x["status"] != "running"), None)
    paths = ["/", "/agents", "/agents/vulcan", "/agents/ember", "/agents/aide", "/groups",
             "/groups/lab-forge", "/groups/lab-math", "/grp-academy".replace("/", "/groups/"),
             "/governance", "/academy", "/experiments", "/publications",
             "/publications?domain=physics", "/protocols", "/commons", "/archive",
             "/constitution", "/data", "/admin"]
    if artifact:
        paths.append(f"/publications/{artifact}")
    if closed:
        paths.append(f"/data/{closed}")
    for path in paths:
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert "Internal Server Error" not in r.text


def test_publication_page_shows_the_proof(client, store):
    """A paper must carry its numbers, its code hash and how to re-run it."""
    papers = [a for a in store.artifacts() if a["kind"] == "paper"]
    if not papers:
        pytest.skip("no paper published in this run")
    paper = papers[0]
    body = client.get(f"/publications/{paper['id']}").text
    assert "Reproduce this result" in body
    assert f"python -m forge reproduce {paper['experiment_id']}" in body
    assert paper["result_hash"] in body
    exp = store.experiment(paper["experiment_id"])
    assert exp["code_hash"] in body
    assert "Cite this" in body
    assert f"/data/{paper['experiment_id']}" in body


def test_raw_data_is_public_and_matches_the_ledger(client, store):
    rows = client.get("/data").json()
    closed = [x for x in store.experiments() if x["status"] != "running"]
    assert len(rows) == len(closed)
    for row in rows:
        exp = store.experiment(row["experiment_id"])
        assert row["results"] == exp["results"]
        assert row["result_hash"] == exp["result_hash"]
        assert row["supported"] == exp["supported"]


def test_failed_results_are_published_too(client, store):
    """Article VII §5 — negative results are not hidden from the public pages."""
    unsupported = [x for x in store.experiments() if x["supported"] is False]
    if not unsupported:
        pytest.skip("nothing was refuted in this run")
    body = client.get("/experiments").text
    assert "not supported" in body


def test_admin_console_requires_the_token(client, monkeypatch):
    monkeypatch.setenv("FORGE_ADMIN_TOKEN", "s3cret")
    assert "Token required" in client.get("/admin").text
    assert "Token required" in client.get("/admin?token=nope").text
    assert "Waiting on you" in client.get("/admin?token=s3cret").text


def test_admin_console_is_disabled_without_a_token(client, monkeypatch):
    monkeypatch.delenv("FORGE_ADMIN_TOKEN", raising=False)
    body = client.get("/admin").text
    assert "console is disabled" in body


def test_suggestion_lands_pending_and_is_not_shown_as_live(client, store):
    before = store.event_count()
    client.post("/suggest", data={"author": "V", "text": "Try a chemistry protocol."},
                follow_redirects=False)
    assert store.event_count() == before + 1
    assert store.suggestions()[0]["status"] == "pending_admin"


def test_floor_shows_agents_and_chain_status(client):
    body = client.get("/").text
    assert "Vulcan Ashe" in body and "Ember Tycho" in body
    assert "chain verified" in body
    assert 'id="live-feed"' in body


def test_profile_shows_personality_and_capabilities(client):
    body = client.get("/agents/cassin").text
    assert "Cassin Vane" in body
    assert "contrarian" in body          # personality is public
    assert "Capability record" in body
    assert "Track record" in body


def test_academy_shows_battery_progress(client):
    body = client.get("/academy").text
    assert "Forge capability index" in body
    assert "Entrance battery" in body or "No candidates" in body


def test_unknown_ids_redirect_rather_than_500(client):
    for path in ("/agents/nobody", "/groups/nope", "/publications/nope"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (302, 307), f"{path} -> {r.status_code}"


def test_verify_endpoint_reports_ok(client, store):
    data = client.get("/api/verify").json()
    assert data["ok"] is True
    assert data["checked"] == store.event_count()


def test_events_api_is_newest_first(client):
    events = client.get("/api/events?limit=10").json()
    assert len(events) == 10
    assert [e["id"] for e in events] == sorted((e["id"] for e in events), reverse=True)


def test_human_suggestion_lands_on_the_ledger(client, store):
    before = store.event_count()
    r = client.post("/suggest", data={"author": "Observer",
                                      "text": "Publish more negative results."},
                    follow_redirects=False)
    assert r.status_code == 303
    assert store.event_count() == before + 1
    latest = store.events(limit=1)[0]
    assert latest["action_type"] == "suggestion_submitted"
    assert latest["payload"]["text"] == "Publish more negative results."
    assert store.verify_chain()["ok"]


def test_empty_suggestion_is_ignored(client, store):
    before = store.event_count()
    client.post("/suggest", data={"author": "x", "text": "   "},
                follow_redirects=False)
    assert store.event_count() == before


def test_avatar_is_deterministic_svg(client):
    r = client.get("/avatar/vulcan.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert avatar_svg("vulcan") == avatar_svg("vulcan")
    assert avatar_svg("vulcan") != avatar_svg("cassin")


def test_humanize_escapes_untrusted_text(store):
    event = store.append("human", "suggestion_submitted",
                         {"author": "<script>x</script>", "text": "<img onerror=1>"})
    line = humanize(store, event)
    assert "<script>" not in line["text"]
    assert "&lt;script&gt;" in line["text"]


def test_markdown_escapes_html():
    out = _markdown("# Hi\n\n<script>alert(1)</script>\n\n- one\n- two\n")
    assert "<h1>Hi</h1>" in out
    assert "<script>" not in out
    assert out.count("<li>") == 2


# ------------------------------------------------- the Chamber, read as a chamber

def test_governance_shows_bills_with_movers_and_thresholds(client, store):
    """A human should be able to read /governance without reading the Ledger:
    every bill names its mover, the article that sets its threshold, and where
    the count stands."""
    html = client.get("/governance").text
    props = store.proposals()
    assert props, "genesis should leave business before the Chamber"
    for p in props[:4]:
        assert p["title"] in html, p["title"]
    assert "moved by" in html
    assert "Threshold to carry" in html
    assert "Article VI §5" in html


def test_governance_roll_call_names_every_voter_and_their_reason(client, store):
    """Names, not only counts — and the ledger event behind each ballot."""
    html = client.get("/governance").text
    found = 0
    for p in store.proposals():
        for ballot in store.votes_for(p["id"]):
            voter = store.agent(ballot["agent_id"])
            assert voter["name"] in html, voter["name"]
            if ballot["reason"]:
                # The stated reason is shown, not summarised away.
                assert ballot["reason"][:40] in html.replace("&#39;", "'")
            found += 1
    assert found >= 3, "no ballots to check"
    assert "ledger #" in html
    assert 'href="/events/' in html


def test_the_franchise_is_withheld_by_the_constitution_not_by_css(client, store):
    """A candidate must appear unable to vote *because Article VI forbids it*.
    The page has to say so in words, and the rule has to actually refuse."""
    from forge.actions import validate

    bars = {"candidate": "Article VI §4 — candidates hold no vote",
            "aide": "Article IX §4 — the assistant holds no vote"}
    html = client.get("/governance").text
    barred = [a for a in store.agents() if a["standing"] in bars]
    assert barred, "the roll should contain at least the administrator's aide"
    openp = store.proposals(status="open")
    for agent in barred:
        assert agent["name"] in html, agent["name"]
        assert bars[agent["standing"]] in html
        # And the article, not the styling, is what stops the ballot.
        if openp:
            err = validate(store, agent["id"], "cast_vote",
                           {"proposal_id": openp[0]["id"], "choice": "for"})
            assert err and "may not" in err, err


def test_governance_shows_roles_read_off_the_ledger(client, store):
    html = client.get("/governance").text
    for agent in store.agents():
        if agent["examiner_domains"]:
            assert agent["examiner_domains"][0] in html


# --------------------------------------------- experiments, legible to a human

def test_experiment_cards_name_the_owner_and_the_room(client, store):
    html = client.get("/experiments").text
    for x in store.experiments():
        owner = store.agent(x["author_id"])
        assert owner["name"] in html, owner["name"]
    assert "Owner" in html
    assert "In the room:" in html


def test_experiment_cards_show_the_step_and_the_last_ledger_event(client, store):
    html = client.get("/experiments").text
    assert "registered at ledger #" in html
    assert "last event #" in html
    assert "read the raw event" in html
    for x in store.experiments():
        events = store.experiment_events(x["id"])
        assert events, f"{x['id']} has no events behind it"
        assert f"/events/{events[0]['id']}" in html


def test_experiment_cards_state_what_would_refute_the_hypothesis(client, store):
    html = client.get("/experiments").text
    assert "What would refute it" in html
    from forge import protocols
    for x in store.experiments():
        spec = protocols.get(x["protocol_id"])
        assert spec and spec["falsifier"], x["protocol_id"]


def test_failed_and_unsupported_runs_stay_on_the_board(client, store):
    """Article VII §5: a refuted hypothesis is published, never withdrawn."""
    html = client.get("/experiments").text
    for x in store.experiments():
        if x["supported"] == 0:
            assert x["title"] in html, x["title"]
            assert "not supported by the data" in html


def test_group_page_lists_participants_and_experiment_status(client, store):
    group = next(g for g in store.groups() if store.experiments(group_id=g["id"]))
    html = client.get(f"/groups/{group['id']}").text
    for member in store.group_members(group["id"]):
        assert member["name"] in html, member["name"]
        assert member["profession"] in html or member["group_role"] == "lead"
    for x in store.experiments(group_id=group["id"]):
        assert x["title"] in html
        assert x["hypothesis"][:40] in html


def test_a_ledger_event_page_serves_the_raw_record(client, store):
    latest = store.events(limit=1)[0]
    r = client.get(f"/events/{latest['id']}")
    assert r.status_code == 200
    assert latest["hash"] in r.text
    assert latest["prev_hash"] in r.text
    assert latest["action_type"] in r.text
    assert client.get(f"/events/{store.event_count() + 500}").status_code == 404


def test_public_json_serves_scorecards_and_examiner_names(client, store):
    """The spec the Forge runs under: results, scorecards and examiner
    identities are public — in a form a machine can read, not only a page."""
    agents = client.get("/api/agents").json()
    assert len(agents) == len(store.agents())
    for row in agents:
        stored = store.agent(row["id"])
        assert row["capabilities"] == store.capabilities_current(row["id"])
        assert row["examiner_domains"] == stored["examiner_domains"]
    assert any(row["examiner_domains"] for row in agents)
    assert any("experiment design" in row["examiner_domains"] for row in agents)
    assert any("constitutional judgment" in row["examiner_domains"] for row in agents)

    sittings = client.get("/api/assessments").json()
    graded = [s for s in sittings if s["status"] == "graded"]
    assert graded
    for sitting in graded:
        assert sitting["examiner"], sitting
        assert sitting["candidate"], sitting
        assert sitting["score"] is not None
        # No agent grades its own paper — visible in the public record itself.
        assert sitting["examiner_id"] != sitting["candidate_id"], sitting
    # The founding papers name their marker honestly rather than leaving it blank.
    founding = [s for s in graded if s["examiner_id"] == "forge"]
    assert founding, "the founding examination should be in the public record"
    assert all("Article IV §9" in s["examiner"] for s in founding)
    assert any(s["score"] < 75 for s in graded), \
        "a scorecard with no low marks is not a measurement"


def test_academy_and_profiles_show_the_new_domains(client, store):
    academy = client.get("/academy").text
    agents_page = client.get("/agents").text
    for domain in ("experiment design", "constitutional judgment"):
        assert domain in academy, domain
        assert domain in agents_page, domain
    examiner = next(a for a in store.agents()
                    if "constitutional judgment" in a["examiner_domains"])
    profile = client.get(f"/agents/{examiner['id']}").text
    assert "constitutional judgment" in profile
    caps = store.capabilities_current(examiner["id"])
    assert str(caps["constitutional judgment"]) in profile


# ------------------------------------- calibration, frontier and admission (GUI)

def test_protocol_library_shows_the_tag_and_who_admitted_it(client, store):
    from forge import protocols

    html = client.get("/protocols").text
    assert "kind-calibration" in html and "kind-frontier" in html
    for pid, spec in protocols.REGISTRY.items():
        assert f'kind-{spec["kind"]}' in html
    # Admission is not a silent fact: the mover and the bench are both named.
    assert "Admitted</b> on the motion of" in html
    for row in store.protocol_admissions(status="admitted"):
        assert store.agent(row["proposer_id"])["name"] in html
        assert store.agent(row["decided_by"])["name"] in html


def test_the_founding_library_was_admitted_by_two_benches(store):
    """Article IV §8 again: a single examiner admitting the whole library would
    make the second experiment-design examiner decoration."""
    rows = store.protocol_admissions(status="admitted")
    assert len(rows) >= 21
    benches = {r["decided_by"] for r in rows}
    assert len(benches) >= 2, benches
    assert not [r for r in rows if r["decided_by"] == r["proposer_id"]]
    for row in rows:
        assert "experiment design" in store.agent(row["decided_by"])["examiner_domains"]


def test_a_laboratory_shows_its_frontier(client, store):
    from forge import protocols

    group = next(g for g in store.groups()
                 if any(protocols.by_domain(d) and
                        any(s["kind"] == "frontier" for s in protocols.by_domain(d))
                        for d in (g["domains"] or [])))
    html = client.get(f"/groups/{group['id']}").text
    assert "The frontier" in html
    for domain in group["domains"]:
        for spec in protocols.by_domain(domain):
            if spec["kind"] == "frontier":
                assert spec["id"] in html, spec["id"]
            # A settled protocol has nothing to win and is not on the board.
    assert "there is nothing there to win" in html


def test_experiment_cards_carry_the_protocol_tag(client, store):
    from forge import protocols

    html = client.get("/experiments").text
    for x in store.experiments():
        if x["protocol_id"]:
            assert f'kind-{protocols.kind_of(x["protocol_id"])}' in html


def test_a_refused_paper_is_recorded_and_shown(tmp_path):
    """A refused publication stays on the Ledger and reaches the reader. Driven
    through the engine with a runtime that submits a paper it has not earned."""
    from forge.actions import validate
    from forge.seed import seed as seed_store
    from tests.test_research import completed_experiment

    store = Store(tmp_path / "f.db")
    seed_store(store)
    exp = completed_experiment(store, xid="exp-earned")

    paper = {"id": "art-earned", "title": "First result", "abstract": "a",
             "content": "c", "content_hash": "h", "authors": [exp["author_id"]],
             "kind": "paper", "protocol_id": exp["protocol_id"],
             "experiment_id": exp["id"], "result_hash": exp["result_hash"],
             "supported": exp["supported"], "domain": exp["domain"]}
    assert validate(store, exp["author_id"], "publish_artifact", paper) is None
    store.append(exp["author_id"], "publish_artifact", paper)

    store.set_tick(store.current_tick() + 60)
    again = completed_experiment(store, xid="exp-rerun")

    class Chancer:
        """Submits the rerun as a paper anyway."""
        def act(self, agent, ctx):
            if agent["id"] != again["author_id"]:
                return []
            return [("publish_artifact", {**paper, "id": "art-rerun",
                                          "experiment_id": again["id"],
                                          "result_hash": again["result_hash"]})]

    engine = Engine(store, Chancer())
    for _ in range(8):
        engine.tick()
        if store.publication_refusals():
            break

    refusals = store.publication_refusals()
    assert refusals, "the engine dropped the refusal instead of recording it"
    reason = refusals[0]["payload"]["reason"]
    assert "calibration protocol" in reason, reason
    assert store.artifact("art-rerun") is None, "the refused paper must not exist"
    assert store.verify_chain()["ok"]

    # And a reader of that experiment can see why no paper came out of it.
    with TestClient(create_app(store, engine=None)) as client:
        html = client.get("/experiments").text
    assert "No paper." in html
    assert "calibration protocol" in html
