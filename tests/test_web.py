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
