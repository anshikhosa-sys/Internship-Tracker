"""
Rules the interface must keep: a GET never changes stored state, a setting
written through the public API is actually persisted, and the pages render
with the pieces a user needs.
"""

import io

import pytest

from jobrank import config, ranking, storage
from jobrank.config import semantic as semantic_cfg
from jobrank.profile import store
from jobrank.resume import parser


@pytest.fixture
def client(tmp_path, monkeypatch, example_resume_text, plain_resume_text):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "internships.db"))
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / "applications.db"))
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", str(tmp_path / "applications.json"))
    monkeypatch.setattr(semantic_cfg, "BACKEND", "hashing")
    monkeypatch.setattr(semantic_cfg, "VECTOR_DB_PATH", str(tmp_path / "vectors.db"))
    store.save("alpha", parser.parse_text(example_resume_text))
    store.save("beta", parser.parse_text(plain_resume_text))
    conn = storage.connect()
    storage.record_run(conn, storage.now_iso(), 0, 0)   # stops the stale-data catch-up refresh
    storage.set_state(conn, "active_profile", "alpha")
    conn.close()
    ranking.clear_cache()
    from jobrank.web.app import app
    app.config["PROPAGATE_EXCEPTIONS"] = True
    yield app.test_client()
    ranking.clear_cache()


def _active():
    conn = storage.connect()
    try:
        return storage.get_state(conn, "active_profile")
    finally:
        conn.close()


def test_public_state_writes_are_committed(tmp_path, monkeypatch):
    """set_state once left committing to the caller; settings silently vanished."""
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "a.db"))
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / "b.db"))
    conn = storage.connect()
    storage.set_state(conn, "active_profile", "written")
    conn.close()
    assert _active() == "written"


def test_viewing_another_profile_does_not_change_the_default(client):
    """A GET must never mutate: ?profile= is a view, "Make default" is a POST."""
    assert client.get("/?profile=beta", headers={"Accept": "text/html"}).status_code == 200
    assert _active() == "alpha"
    assert client.get("/", headers={"Accept": "text/html"}).status_code == 200
    assert _active() == "alpha"


def test_make_default_persists_and_unknown_profile_is_refused(client):
    assert client.post("/profile/active", data={"profile": "beta"}).status_code == 302
    assert _active() == "beta"
    assert client.post("/profile/active", data={"profile": "nobody"}).status_code == 404
    assert _active() == "beta"


def test_requested_profile_is_the_one_ranked(client):
    page = client.get("/?profile=beta", headers={"Accept": "text/html"}).data.decode()
    assert 'class="profile-chip">beta<' in page
    assert "Make default" in page          # offered, because beta is not the default


def test_dashboard_renders_the_pieces_a_user_needs(client):
    page = client.get("/", headers={"Accept": "text/html"}).data.decode()
    # The stat strip and its "Active postings" heading went when the dashboard
    # became a two-pane job board; the counts fold into one line above results.
    for fragment in ["form class=\"filters\"", "Apply filters", 'class="brand-mark"']:
        assert fragment in page


def test_profile_page_accepts_an_upload_and_shows_what_the_scorer_sees(client, example_resume_text):
    response = client.post("/profile", content_type="multipart/form-data", data={
        "user_id": "gamma", "target_roles": "Backend Engineer", "remote": "any",
        "resume": (io.BytesIO(example_resume_text.encode()), "cv.md")})
    assert response.status_code == 302
    page = client.get("/profile?id=gamma").data.decode()
    assert "What the scorer sees" in page and "skill-list" in page
