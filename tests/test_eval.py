import json
import os
import shutil

import pytest

from jobrank.eval import golden, harness, metrics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "eval", "fixtures", "example_postings.jsonl")


# --- metrics -----------------------------------------------------------------

RANKED = ["a", "b", "c", "d", "e"]
LABELS = {"a": 0, "b": 3, "d": 2, "z": 2}   # "z" is relevant but absent from the ranking


def test_precision_and_recall():
    assert metrics.precision_at_k(RANKED, LABELS, 2) == 0.5
    assert metrics.recall_at_k(RANKED, LABELS, 4) == pytest.approx(2 / 3)


def test_reciprocal_rank_first_relevant():
    assert metrics.reciprocal_rank(RANKED, LABELS) == 0.5
    assert metrics.reciprocal_rank(RANKED, {"a": 1}) == 0.0


def test_ndcg_graded_and_bounded():
    perfect = ["b", "d", "z", "a"]
    assert metrics.ndcg_at_k(perfect, LABELS, 3) == pytest.approx(1.0)
    swapped = ["d", "b", "z", "a"]
    assert 0 < metrics.ndcg_at_k(swapped, LABELS, 3) < 1.0


def test_auc_is_pairwise_order():
    assert metrics.auc(["r1", "r2", "x", "y"], {"r1": 2, "r2": 2}) == 1.0
    assert metrics.auc(["x", "y", "r1", "r2"], {"r1": 2, "r2": 2}) == 0.0
    assert metrics.auc(["r1", "x", "r2", "y"], {"r1": 2, "r2": 2}) == 0.75
    assert metrics.auc(["x"], {}) is None


def test_random_baseline_is_near_chance():
    ranked = [str(i) for i in range(200)]
    labels = {str(i): 2 for i in range(0, 200, 10)}
    baseline = metrics.random_baseline(ranked, labels, ks=(10,), trials=300)
    assert baseline["auc"] == pytest.approx(0.5, abs=0.05)
    assert baseline["precision@10"] == pytest.approx(0.1, abs=0.03)


# --- golden labels -------------------------------------------------------------

def test_seed_grades_by_stage_and_manual_labels_win(tmp_path):
    labels = {}
    apps = [{"id": "a", "status": "applied"}, {"id": "b", "status": "interview"}, {"id": "c", "status": ""},
            {"id": "d", "status": "rejected"}]
    assert golden.seed_from_applications(labels, apps) == 3
    assert labels["a"].relevance == 2 and labels["b"].relevance == 3 and "c" not in labels
    golden.upsert(labels, {"id": "d"}, 0, note="regretted")
    assert golden.seed_from_applications(labels, apps) == 0
    assert labels["d"].relevance == 0


def test_label_roundtrip_and_validation(tmp_path):
    labels = {}
    golden.upsert(labels, {"id": "job:1", "company": "Acme", "role": "SRE Intern"}, 3)
    golden.save("t", labels, str(tmp_path))
    assert golden.load("t", str(tmp_path))["job:1"].role == "SRE Intern"
    with pytest.raises(ValueError):
        golden.upsert(labels, {"id": "x"}, 5)
    (tmp_path / "bad.jsonl").write_text('{"posting_id": "x", "relevance": 9}\n')
    with pytest.raises(ValueError, match="relevance must be 0-3"):
        golden.load("bad", str(tmp_path))


# --- harness and gate -------------------------------------------------------------

@pytest.fixture
def example_eval(tmp_path, monkeypatch):
    """The committed example profile, labels and fixture, evaluated in a temp working copy."""
    from jobrank.config import profile as profile_cfg
    from jobrank.config import semantic as semantic_cfg
    from jobrank.semantic import embedder

    shutil.copy(os.path.join(ROOT, "profiles", "example.json"), tmp_path / "example.json")
    monkeypatch.setattr(profile_cfg, "PROFILES_DIR", str(tmp_path))
    monkeypatch.setattr(golden, "GOLDEN_DIR", os.path.join(ROOT, "eval", "golden"))
    monkeypatch.setattr(harness, "BASELINE_DIR", str(tmp_path / "baselines"))
    monkeypatch.setattr(semantic_cfg, "BACKEND", "hashing")
    monkeypatch.setattr(semantic_cfg, "VECTOR_DB_PATH", str(tmp_path / "vectors.db"))
    embedder.reset()
    yield
    embedder.reset()


def test_example_ranking_beats_random(example_eval):
    result = harness.evaluate("example", fixture=FIXTURE)
    assert result["labels_usable"] == result["labels_total"] > 100
    assert result["metrics"]["auc"] > result["random_baseline"]["auc"] + 0.2
    assert result["metrics"]["ndcg@20"] > result["random_baseline"]["ndcg@20"]
    assert "freshness" in result["disabled_factors"]


def test_gate_passes_unchanged_and_fails_a_regression(example_eval, monkeypatch):
    result = harness.evaluate("example", fixture=FIXTURE)
    harness.save_baseline(result)
    baseline = harness.load_baseline("example")
    assert harness.compare(result, baseline)["regressions"] == []

    # Break the model: make role affinity meaningless for every posting.
    from jobrank.scoring import factors
    monkeypatch.setattr(factors, "role", lambda *a, **k: factors.FactorResult("role", 0.5))
    worse = harness.evaluate("example", fixture=FIXTURE)
    report = harness.compare(worse, baseline)
    assert "auc" in report["regressions"]
    assert report["config_changed"] is False


def test_evaluate_cli_exit_codes(example_eval, capsys):
    import evaluate

    args = ["--profile", "example", "--fixture", FIXTURE]
    assert evaluate.main(args + ["--compare"]) == 2           # no baseline yet
    assert evaluate.main(args + ["--save-baseline"]) == 0
    assert evaluate.main(args + ["--compare"]) == 0
    assert "PASS" in capsys.readouterr().out
    assert evaluate.main(["--profile", "nobody-labeled"]) == 2


def test_ablation_reports_every_active_factor(example_eval):
    rows = harness.ablation("example", fixture=FIXTURE, semantic=False)
    assert rows[0]["factor"] == "(all factors)"
    assert {r["factor"] for r in rows[1:]} == {"skills", "seniority", "role"}


def test_committed_example_files_contain_no_real_person():
    with open(os.path.join(ROOT, "eval", "golden", "example.jsonl"), encoding="utf-8") as handle:
        sources = {json.loads(line)["source"] for line in handle}
    assert sources == {"synthetic"}
