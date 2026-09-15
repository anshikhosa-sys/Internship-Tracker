from datetime import date

import numpy as np
import pytest

from jobrank import enrich, postings
from jobrank.config import scoring as cfg
from jobrank.config import semantic as semantic_cfg
from jobrank.models import Preferences, Profile, SeniorityEstimate
from jobrank.scoring import engine, factors

TODAY = date(2026, 9, 15)


def make_profile(**overrides) -> Profile:
    base = dict(
        user_id="t",
        skills={"python": 1.0, "go": 0.9, "kubernetes": 0.8},
        seniority=SeniorityEstimate(level="intern", eligible_levels=["intern"], degree_level="bachelor",
                                    years_experience=0.5, is_student=True),
        role_affinity={"infrastructure": 0.95, "backend": 0.8, "software_engineering": 0.6,
                       "data_science": 0.1, "product_management": 0.05},
        target_families=["infrastructure", "backend"],
        factor_weights={f: 1.0 for f in cfg.FACTORS},
        preferences=Preferences(),
        semantic_text="Infrastructure engineer intern. Go, Kubernetes.",
    )
    base.update(overrides)
    return Profile(**base)


def make_posting(**overrides) -> dict:
    base = dict(id="job:1", company="Acme Robotics Inc", role="Infrastructure Engineer Intern",
                category="Software Engineering", location="Chicago, IL", date_posted="2026-09-15",
                source="", description="", salary="", is_faang=False, needs_advanced_degree=False)
    base.update(overrides)
    return base


# --- composition -------------------------------------------------------------

def test_multiplicative_near_zero_sinks_the_score():
    strong = {name: factors.FactorResult(name, 0.95) for name in ["a", "b", "c"]}
    sunk = dict(strong, c=factors.FactorResult("c", 0.03))
    weights = {"a": 1.0, "b": 1.0, "c": 1.0}
    assert engine.compose(strong, weights) > 0.85
    # Addition would average this to ~0.64; multiplication keeps it below 0.03.
    assert engine.compose(sunk, weights) < 0.03


def test_zero_factor_is_zero_even_with_zero_weight_elsewhere():
    results = {"a": factors.FactorResult("a", 0.0), "b": factors.FactorResult("b", 1.0)}
    assert engine.compose(results, {"a": 1.0, "b": 0.0}) == 0.0


def test_exponent_changes_ranking_not_just_scale():
    """A user who prioritizes freshness should get a different ORDER, not the same order rescaled."""
    old_fit = make_posting(id="old", role="Infrastructure Engineer Intern", date_posted="2026-09-01")
    new_meh = make_posting(id="new", role="Software Engineer Intern", date_posted="2026-09-15")
    rows = [old_fit, new_meh]
    fit_first = make_profile(factor_weights={**{f: 1.0 for f in cfg.FACTORS}, "role": 1.25, "freshness": 0.25})
    fresh_first = make_profile(factor_weights={**{f: 1.0 for f in cfg.FACTORS}, "role": 0.25, "freshness": 1.25})
    top = lambda p: engine.score_all(p, rows, today=TODAY, semantic=False)[0].posting_id  # noqa: E731
    assert top(fit_first) == "old"
    assert top(fresh_first) == "new"


def test_two_profiles_rank_the_same_postings_differently():
    rows = [make_posting(id="infra"), make_posting(id="pm", role="Product Manager Intern")]
    pm_person = make_profile(role_affinity={"product_management": 0.95, "infrastructure": 0.1})
    assert engine.score_all(make_profile(), rows, today=TODAY, semantic=False)[0].posting_id == "infra"
    assert engine.score_all(pm_person, rows, today=TODAY, semantic=False)[0].posting_id == "pm"


def test_result_explains_every_factor():
    result = engine.score_all(make_profile(), [make_posting()], today=TODAY, semantic=False)[0]
    data = result.to_dict()
    assert set(data["factors"]) == {"skills", "seniority", "role", "preferences", "freshness"}
    product = np.prod([f["contribution"] for f in data["factors"].values()])
    assert round(product * 100) == result.score
    assert all(f["reasons"] for f in data["factors"].values())


# --- skills --------------------------------------------------------------------

def test_skills_neutral_when_posting_names_none():
    result = factors.skills(make_posting(role="Engineering Intern"), make_profile())
    assert result.neutral and result.value == cfg.SKILLS_NEUTRAL


def test_skills_coverage_and_missing():
    posting = make_posting(role="Backend Intern (Go, Rust)")
    result = factors.skills(posting, make_profile())
    assert result.detail["matched"] == ["go"] and result.detail["missing"] == ["rust"]
    assert result.value == pytest.approx(cfg.SKILLS_FLOOR + (1 - cfg.SKILLS_FLOOR) * 0.45)


def test_enrichment_tech_stack_counts_as_posting_skills():
    result = factors.skills(make_posting(role="Engineering Intern"), make_profile(), {"tech_stack": ["kubernetes"]})
    assert not result.neutral and result.detail["matched"] == ["kubernetes"]


# --- seniority -----------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Infrastructure Engineer Intern", cfg.SENIORITY_FIT[0]),
    ("Junior Infrastructure Engineer", cfg.SENIORITY_FIT["above_1"]),
    ("Senior Infrastructure Engineer", cfg.SENIORITY_FIT["above_2"]),
])
def test_underqualification_penalized(title, expected):
    assert factors.seniority(make_posting(role=title), make_profile()).value == pytest.approx(expected)


def test_overqualification_penalized_too():
    senior = make_profile(seniority=SeniorityEstimate(level="senior", eligible_levels=["senior"], degree_level="master",
                                                      years_experience=7))
    assert factors.seniority(make_posting(role="Software Engineer Intern"), senior).value == \
        pytest.approx(cfg.SENIORITY_FIT["below_2"])


def test_source_default_level_used_when_title_is_silent(monkeypatch):
    monkeypatch.setitem(cfg.SOURCE_DEFAULT_SENIORITY, "InternList", "intern")
    result = factors.seniority(make_posting(role="Platform Engineer", source="InternList"), make_profile())
    assert result.value == 1.0 and "source" in " ".join(result.reasons)


def test_required_years_and_degree_from_enrichment():
    result = factors.seniority(make_posting(), make_profile(), {"required_years": 3, "degree_required": "phd"})
    expected = cfg.SENIORITY_MISSING_YEAR_MULTIPLIER ** 2 * cfg.DEGREE_REQUIREMENT_UNMET
    assert result.value == pytest.approx(expected)


# --- role ------------------------------------------------------------------------

def test_role_uses_derived_affinity_and_category_fallback():
    assert factors.role(make_posting(role="Site Reliability Engineer Intern"), make_profile()).value == 0.95
    unknown = factors.role(make_posting(role="Summer Intern", category="Software Engineering"), make_profile())
    assert unknown.value == 0.6
    none = factors.role(make_posting(role="Summer Intern", category="Other"), make_profile())
    assert none.neutral and none.value == cfg.ROLE_UNKNOWN


# --- preferences -------------------------------------------------------------------

def test_excluded_industry_is_near_zero():
    profile = make_profile(preferences=Preferences(exclude_industries=["quant_trading"]))
    result = factors.preferences(make_posting(company="Jane Street"), profile)
    assert result.value == pytest.approx(cfg.INDUSTRY_EXCLUDED)


def test_location_alias_and_remote_rules():
    profile = make_profile(preferences=Preferences(locations=["NYC"], remote="remote_only"))
    in_nyc_onsite = factors.preferences(make_posting(location="New York, NY"), profile)
    assert in_nyc_onsite.value == pytest.approx(cfg.REMOTE_MATRIX["remote_only"]["onsite"])
    remote = factors.preferences(make_posting(location="Remote"), make_profile(
        preferences=Preferences(locations=["Remote"], remote="remote_only")))
    assert remote.value == 1.0


def test_start_before_available():
    profile = make_profile(preferences=Preferences(earliest_start="2027-05"))
    early = factors.preferences(make_posting(role="Fall 2026 Infrastructure Intern"), profile)
    ok = factors.preferences(make_posting(role="Summer 2027 Infrastructure Intern"), profile)
    assert early.value == pytest.approx(cfg.START_BEFORE_AVAILABLE) and ok.value == 1.0


def test_unknown_facts_do_not_penalize():
    profile = make_profile(preferences=Preferences(locations=["Chicago"], company_sizes=["large"]))
    result = factors.preferences(make_posting(location=""), profile)
    # No location to compare; size is unknown-small by volume, which IS known, so only size applies.
    assert result.value == pytest.approx(cfg.COMPANY_SIZE_MISMATCH)


# --- freshness -------------------------------------------------------------------

def test_freshness_decays_faster_for_large_companies():
    small = factors.freshness(make_posting(date_posted="2026-09-10"), TODAY).value
    large = factors.freshness(make_posting(company="Google", date_posted="2026-09-10"), TODAY).value
    assert large < small
    assert factors.freshness(make_posting(date_posted=None), TODAY).neutral


# --- semantic ---------------------------------------------------------------------

def test_semantic_calibration_and_absence():
    assert factors.semantic(None, None) is None
    model = "BAAI/bge-small-en-v1.5"
    calib = cfg.SEMANTIC_CALIBRATION[model]
    assert factors.semantic(calib["ceil"], model).value == 1.0
    assert factors.semantic(0.0, model).value == cfg.SEMANTIC_MIN


def test_semantic_layer_falls_back_to_hashing_without_model(monkeypatch, tmp_path):
    from jobrank.semantic import embedder

    monkeypatch.setattr(semantic_cfg, "BACKEND", "hashing")
    monkeypatch.setattr(semantic_cfg, "VECTOR_DB_PATH", str(tmp_path / "vec.db"))
    embedder.reset()
    rows = [make_posting(id="a", role="Site Reliability Engineer Intern"),
            make_posting(id="b", role="Pastry Chef")]
    results = {r.posting_id: r for r in engine.score_all(make_profile(semantic_text="site reliability engineer"),
                                                          rows, today=TODAY)}
    assert results["a"].factors["semantic"].detail["model"] == semantic_cfg.HASHING_NAME
    assert results["a"].factors["semantic"].value > results["b"].factors["semantic"].value
    embedder.reset()


def test_semantic_failure_never_breaks_scoring(monkeypatch):
    import jobrank.semantic as semantic

    def broken(*a, **k):
        raise RuntimeError("index corrupted")
    monkeypatch.setattr("jobrank.semantic.embedder.get_embedder", broken)
    sims, model = semantic.similarities_for(make_profile(), [make_posting()])
    assert sims == {} and model is None
    result = engine.score_all(make_profile(), [make_posting()], today=TODAY)[0]
    assert "semantic" not in result.factors and result.score > 0


def test_vector_index_only_reembeds_changed_text(monkeypatch, tmp_path):
    from jobrank.semantic.embedder import HashingEmbedder
    from jobrank.semantic.index import VectorIndex

    index = VectorIndex(HashingEmbedder(), str(tmp_path / "v.db"))
    assert index.upsert({"a": "go engineer", "b": "chef"}) == 2
    assert index.upsert({"a": "go engineer", "b": "chef"}) == 0
    assert index.upsert({"a": "go engineer", "b": "sous chef"}) == 1
    sims = index.similarities("go engineer", ["a", "b"])
    assert sims["a"] == pytest.approx(1.0, abs=1e-5) and sims["b"] < 0.5
    index.close()


# --- enrichment ---------------------------------------------------------------------

def test_rules_enrichment_extracts_stated_facts():
    posting = make_posting(role="Junior Backend Engineer", location="Remote (US)",
                           description="Requirements: 4+ years of professional experience with Go and "
                                       "PostgreSQL. Bachelor's degree in Computer Science required.")
    data, extractor, _ = enrich.enrich_one(posting)
    assert extractor == "rules"
    assert data["required_years"] == 4 and data["degree_required"] == "bachelor"
    assert data["entry_level"] is False and data["remote_status"] == "remote"
    assert {"go", "postgresql"} <= set(data["tech_stack"])
    assert "requires 4+ years" in data["title_contradiction"]


def test_preferred_years_are_not_requirements():
    data = enrich.rules_extract("Title: Software Engineer Intern\n3+ years of experience preferred but not required")
    assert data["required_years"] is None and data["entry_level"] is True


def test_enrichment_is_cached_by_text_hash(monkeypatch):
    from jobrank import storage

    conn = storage.connect(":memory:")
    rows = [make_posting(id="x", description="Kubernetes required")]
    assert enrich.enrich_all(conn, rows) == {"enriched": 1, "unchanged": 0}
    assert enrich.enrich_all(conn, rows) == {"enriched": 0, "unchanged": 1}
    rows[0]["description"] = "Kubernetes and Terraform required"
    assert enrich.enrich_all(conn, rows)["enriched"] == 1
    conn.close()


def test_title_classification_of_abbreviations():
    assert postings.role_families(make_posting(role="SDE Intern")) == ["software_engineering"]
    assert postings.role_families(make_posting(role="SRE Intern")) == ["infrastructure"]
