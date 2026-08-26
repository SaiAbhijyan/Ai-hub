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
    paths = ["/", "/agents", "/agents/vulcan", "/agents/ember", "/groups",
             "/groups/grp-infra", "/groups/grp-academy", "/governance", "/academy",
             "/experiments", "/publications", "/archive", "/constitution"]
    if artifact:
        paths.append(f"/publications/{artifact}")
    for path in paths:
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert "Internal Server Error" not in r.text


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
