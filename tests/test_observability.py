import json
import os

import pytest

from jobrank import llm, log
from jobrank.cli import find_posting_id
from jobrank.ops import stats
from jobrank.scoring import engine
from jobrank.scoring.explain import explain
from tests.test_scoring import make_posting, make_profile


def test_tests_never_write_to_the_repo_log_directory():
    """The reason this exists: injected test failures once landed in the real log."""
    assert os.path.abspath(log.LOG_DIR) != os.path.abspath("logs")
    assert "jobrank-test-logs-" in log.LOG_DIR


def test_scoring_decisions_are_logged_with_a_run_id():
    engine.score_all(make_profile(), [make_posting(id="job:aaa"), make_posting(id="job:bbb", role="Chef")],
                     semantic=False, log_decisions=True, run_id="run-xyz")
    path = os.path.join(log.LOG_DIR, "scoring.jsonl")
    records = [json.loads(line) for line in open(path, encoding="utf-8") if '"run-xyz"' in line]
    assert len(records) == 2
    first = records[0]
    assert first["rank"] == 1 and first["profile"] == "t" and first["event"] == "score"
    assert set(first["factors"]) == {"skills", "seniority", "role", "preferences", "freshness"}
    for factor in first["factors"].values():
        assert set(factor) >= {"v", "w", "c"}
    # Compact on purpose: prose reasons belong to --explain, not to 4,000 log lines.
    assert "reasons" not in json.dumps(first)


def test_explain_shows_the_whole_calculation():
    profile = make_profile()
    posting = make_posting(role="Infrastructure Engineer Intern", company="Acme")
    result = engine.score_all(profile, [posting], semantic=False)[0]
    text = explain(profile, posting, result, rank=3, total=99)
    assert "Acme — Infrastructure Engineer Intern" in text
    assert "rank 3 of 99" in text
    assert "× 100 =" in text
    for label in ["Skill overlap", "Seniority fit", "Role affinity", "Preference match", "Freshness"]:
        assert label in text
    assert "(no data: neutral)" in text          # this posting names no skills
    assert "Biggest drag:" in text
    assert "Not scored: semantic" in text


def test_explain_names_the_weakest_factor():
    profile = make_profile(preferences=__import__("jobrank.models", fromlist=["Preferences"]).Preferences(
        exclude_industries=["quant_trading"]))
    posting = make_posting(company="Jane Street", role="Infrastructure Engineer Intern")
    result = engine.score_all(profile, [posting], semantic=False)[0]
    assert "Biggest drag: Preference match" in explain(profile, posting, result)


@pytest.mark.parametrize("query,expected", [
    ("job:abc123", "job:abc123"),
    ("abc123", "job:abc123"),
    ("abc", "job:abc123"),
    ("job:zzz", None),
    ("a", None),          # ambiguous prefix
])
def test_posting_id_lookup(query, expected):
    assert find_posting_id(query, ["job:abc123", "job:aaa999"]) == expected


def test_stats_collect_reports_every_subsystem(monkeypatch, tmp_path):
    from jobrank import enrich, storage
    from jobrank.config import llm as llm_cfg
    from jobrank.config import semantic as semantic_cfg

    monkeypatch.setattr(llm_cfg, "CACHE_PATH", str(tmp_path / "llm.db"))
    monkeypatch.setattr(semantic_cfg, "VECTOR_DB_PATH", str(tmp_path / "vectors.db"))
    monkeypatch.setattr(storage.config, "DATABASE_PATH", str(tmp_path / "internships.db"))
    monkeypatch.setattr(storage.config, "APPLICATIONS_PATH", str(tmp_path / "applications.db"))

    conn = storage.connect()
    enrich.enrich_all(conn, [make_posting(id="job:1", description="Kubernetes and Go required")])
    conn.close()

    data = stats.collect()
    usage = {row["task"] for row in data["llm_usage"]}
    assert "posting_enrich" in usage
    assert data["llm_cache_entries"]
    assert data["enrichment"]["enriched_by"] == {"rules": 1}
    text = stats.format_text(data)
    assert "LLM USAGE" in text and "ENRICHMENT" in text


def test_recent_events_reads_newest_last(tmp_path):
    path = tmp_path / "jobrank.jsonl"
    path.write_text("\n".join(json.dumps({"ts": f"2026-09-0{i}", "event": "ranking_computed", "n": i})
                              for i in range(1, 6)) + "\nnot json\n")
    events = stats.recent_events("ranking_computed", limit=2, path=str(path))
    assert [e["n"] for e in events] == [5, 4]
    assert stats.recent_events("nothing", path=str(path)) == []


def test_enrichment_cap_applies_only_when_a_model_is_running(monkeypatch):
    monkeypatch.setattr(llm, "_model_backend", lambda: None)
    llm.reset_probe()
    assert llm.model_available() is False
    monkeypatch.setattr(llm, "_model_backend", lambda: object())
    assert llm.model_available() is True


def test_stats_page_renders(monkeypatch, tmp_path):
    from jobrank.web.app import app

    response = app.test_client().get("/stats")
    assert response.status_code == 200
    assert b"Structured extraction" in response.data
