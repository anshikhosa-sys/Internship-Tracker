"""
Uploading a replacement résumé: it must re-score the profile, report what
changed, and never damage the profile already on file when a parse fails.
"""

import io

import pytest

from jobrank import config, ranking
from jobrank.config import semantic as semantic_cfg
from jobrank.profile import store
from jobrank.profile.diff import compare
from jobrank.resume import parser


@pytest.fixture
def client(tmp_path, monkeypatch, example_resume_text):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "internships.db"))
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / "applications.db"))
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", str(tmp_path / "applications.json"))
    monkeypatch.setattr(semantic_cfg, "BACKEND", "hashing")
    monkeypatch.setattr(semantic_cfg, "VECTOR_DB_PATH", str(tmp_path / "vectors.db"))
    store.save("alex", parser.parse_text(example_resume_text))
    ranking.clear_cache()
    from jobrank.web.app import app
    app.config["PROPAGATE_EXCEPTIONS"] = True
    yield app.test_client()
    ranking.clear_cache()


def upload(client, text: str, filename: str = "resume.md", user_id: str = "alex"):
    return client.post(f"/profile/{user_id}/resume", content_type="multipart/form-data",
                       data={"resume": (io.BytesIO(text.encode()), filename)})


def test_upload_replaces_the_resume_and_rescore_reflects_it(client, plain_resume_text):
    before = store.load("alex")
    response = upload(client, plain_resume_text)
    assert response.status_code == 200
    after = store.load("alex")
    assert after.skills != before.skills
    assert "snowflake" in after.skills      # named only by the new résumé
    assert "go" not in after.skills         # named only by the old one


def test_page_reports_what_changed(client, plain_resume_text):
    page = upload(client, plain_resume_text).data.decode()
    assert "What changed" in page
    assert "Skills added" in page and "No longer found" in page
    assert "Role affinity moved" in page
    assert "See the new ranking" in page


def test_seniority_change_is_reported(client, plain_resume_text):
    """
    The example résumé is a student; the replacement has 4.25 credited years,
    which is mid (senior starts at 5.5).

    This once asserted `"senior" in page`, which passed on the substring inside
    "Seniority levels you want" in the preferences form — not on the reported
    level at all. The form is gone, so the assertion now names the transition.
    """
    page = upload(client, plain_resume_text).data.decode()
    assert "intern →" in page and "<strong>mid</strong>" in page


def test_a_bad_parse_changes_nothing(client):
    before = store.load("alex")
    response = upload(client, "hello", "resume.txt")
    assert response.status_code == 400
    assert b"characters of text" in response.data
    assert store.load("alex").skills == before.skills


def test_unparseable_prose_changes_nothing(client):
    before = store.load("alex")
    prose = "I am a hard working person who enjoys solving problems and learning new things. " * 6
    assert upload(client, prose).status_code == 400
    assert store.load("alex").skills == before.skills


def test_missing_file_is_refused(client):
    response = client.post("/profile/alex/resume", content_type="multipart/form-data", data={})
    assert response.status_code == 400 and b"Choose a r" in response.data


def test_unknown_or_invalid_profile_is_refused(client, plain_resume_text):
    assert upload(client, plain_resume_text, user_id="nobody").status_code == 404
    assert upload(client, plain_resume_text, user_id="../etc").status_code in (404, 308)


def test_ranking_uses_the_new_resume_immediately(client, plain_resume_text):
    from jobrank import storage
    from jobrank.sources.base import Posting

    conn = storage.connect()
    storage.save_postings(conn, [Posting(company="Acme", role="Senior Data Engineer", category="",
                                         location="Remote", apply_url="http://x", date_posted="2026-09-15")],
                          storage.now_iso())
    conn.close()
    first = ranking.rank("alex").results
    upload(client, plain_resume_text)
    second = ranking.rank("alex").results
    assert list(first.values())[0].score != list(second.values())[0].score


class TestDiff:
    def test_first_profile_lists_every_skill_as_added(self, example_resume_text):
        resume = parser.parse_text(example_resume_text)
        profile = store.save("solo", resume)
        changes = compare(None, profile, resume)
        assert changes.skills_added == sorted(profile.skills) and not changes.skills_removed

    def test_detects_a_likely_bad_parse(self, example_resume_text, plain_resume_text):
        rich = parser.parse_text(example_resume_text)
        before = store.save("a", rich)
        thin = parser.parse_text("## Skills\n\nPython\n\n## Experience\n\n### Acme — Engineer\n"
                                 "Jan 2020 - Jan 2021\n\n- Did work with Python for a while here\n")
        after = store.save("a", thin)
        changes = compare(before, after, thin, rich)
        assert changes.looks_like_a_worse_parse()

    def test_no_change_is_reported_as_no_change(self, example_resume_text):
        resume = parser.parse_text(example_resume_text)
        profile = store.save("b", resume)
        assert compare(profile, profile, resume, resume).is_empty
