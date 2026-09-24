from datetime import date

import numpy as np
import pytest

from jobrank import enrich, postings
from jobrank.config import scoring as cfg
from jobrank.config import semantic as semantic_cfg
from jobrank.models import Profile, SeniorityEstimate
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


def test_exponent_changes_ranking_not_just_scale(monkeypatch):
    """
    An exponent must genuinely reorder. In a product a coefficient cannot: it
    rescales every score by the same ratio and leaves the order untouched.
    """
    old_fit = make_posting(id="old", role="Infrastructure Engineer Intern", date_posted="2026-09-01")
    new_meh = make_posting(id="new", role="Software Engineer Intern", date_posted="2026-09-15")
    rows = [old_fit, new_meh]

    def top(**exponents):
        monkeypatch.setattr(cfg, "FACTOR_EXPONENTS", {**{f: 1.0 for f in cfg.FACTORS}, **exponents})
        return engine.score_all(make_profile(), rows, today=TODAY, semantic=False, use_market=False)[0].posting_id

    assert top(role=1.25, freshness=0.25) == "old"
    assert top(role=0.25, freshness=1.25) == "new"


def test_no_factor_reads_a_stated_preference():
    """
    The scorer must not consult what the user said they want. Two profiles
    that differ only in their stated preferences must score identically.
    """
    silent = make_profile()
    opinionated = make_profile()
    rows = [make_posting(), make_posting(id="2", company="Jane Street", role="Quant Intern")]
    scored = lambda p: [(r.posting_id, r.score) for r in   # noqa: E731
                        engine.score_all(p, rows, today=TODAY, semantic=False, use_market=False)]
    assert scored(silent) == scored(opinionated)


def test_two_profiles_rank_the_same_postings_differently():
    rows = [make_posting(id="infra"), make_posting(id="pm", role="Product Manager Intern")]
    pm_person = make_profile(role_affinity={"product_management": 0.95, "infrastructure": 0.1})
    assert engine.score_all(make_profile(), rows, today=TODAY, semantic=False)[0].posting_id == "infra"
    assert engine.score_all(pm_person, rows, today=TODAY, semantic=False)[0].posting_id == "pm"


def test_result_explains_every_factor():
    result = engine.score_all(make_profile(), [make_posting()], today=TODAY, semantic=False)[0]
    data = result.to_dict()
    assert set(data["factors"]) == {"skills", "seniority", "role", "freshness"}
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


# --- market model: what the corpus demands ------------------------------------

def test_idf_weights_rare_skills_above_common_ones():
    """
    Matching Python says little — almost every posting asks for it. Matching
    CUDA says a great deal. A plain average counts them the same.
    """
    from jobrank import market
    model = market.build([make_posting(id=str(n), role="Software Engineer Intern, Python")
                          for n in range(200)] +
                         [make_posting(id="rare", role="Engineer Intern, CUDA")])
    assert model.idf("cuda") > model.idf("python")


def test_market_affinity_credits_transferable_skills():
    """
    The hand-written evidence list for data engineering is all specialist
    tools, so a Python-and-SQL résumé floored at 0.05. The corpus knows those
    postings ask for Python and SQL too.
    """
    from jobrank import market
    rows = [make_posting(id=f"de{n}", role="Data Engineer Intern",
                         description="We use Python and SQL every day.")
            for n in range(40)]
    model = market.build(rows)
    affinity = model.affinity({"python": 1.0, "sql": 1.0})
    assert affinity["data_engineering"] > 0.5


def test_market_model_is_ignored_when_the_sample_is_too_small():
    """Too few postings is no information, not zero demand (invariant 7)."""
    from jobrank import market
    model = market.build([make_posting(id="1", role="Data Engineer Intern", description="Python")])
    assert model.demand("data_engineering") == {}
    assert model.affinity({"python": 1.0}).get("data_engineering") is None


def test_role_blends_evidence_with_market_but_evidence_leads():
    profile = make_profile(role_affinity={"infrastructure": 0.9})
    posting = make_posting(role="Infrastructure Engineer Intern")
    alone = factors.role(posting, profile).value
    with_market = factors.role(posting, profile, market=object(),
                               market_affinity={"infrastructure": 0.1}).value
    assert alone == pytest.approx(0.9)
    assert 0.1 < with_market < 0.9          # market drags it down, evidence still leads
    assert with_market > 0.5


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
    assert enrich.enrich_all(conn, rows) == {"enriched": 1, "unchanged": 0, "failed": 0}
    assert enrich.enrich_all(conn, rows) == {"enriched": 0, "unchanged": 1, "failed": 0}
    rows[0]["description"] = "Kubernetes and Terraform required"
    assert enrich.enrich_all(conn, rows)["enriched"] == 1
    conn.close()


def test_title_classification_of_abbreviations():
    assert postings.role_families(make_posting(role="SDE Intern")) == ["software_engineering"]
    assert postings.role_families(make_posting(role="SRE Intern")) == ["infrastructure"]


# --- discipline vetoes ---------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Water Infrastructure Engineering Intern - Summer 2027", []),
    ("Civil Engineering Intern - Coastal Infrastructure", []),
    ("Mechanical Design Engineer Intern", []),
    ("Hardware Engineering Intern - Infrastructure Solutions Group", ["embedded_hardware"]),
    ("Private Equity Infrastructure & Real Assets Summer Analyst", []),
    ("Cloud Infrastructure Engineer Intern", ["infrastructure"]),
    ("Site Reliability Engineer Intern", ["infrastructure"]),
    ("Software Engineer Intern", ["software_engineering"]),
])
def test_non_software_disciplines_do_not_match_software_families(title, expected):
    """"Infrastructure" is a word two unrelated professions share."""
    from jobrank.roles import classify_title
    assert classify_title(title) == expected


@pytest.mark.parametrize("title,category,expected", [
    ("Structural Design Intern", "Software Engineering", []),
    ("Reservoir Engineer Intern", "Software Engineering", []),
    ("Materials Engineer Intern", "Data Science, AI & Machine Learning", []),
    ("Transportation Systems Analysis Intern", "Software Engineering", []),
    # A title the veto says nothing about still takes the category.
    ("Software Development Intern", "Software Engineering", ["software_engineering"]),
    ("Summer Technology Intern", "Software Engineering", ["software_engineering"]),
])
def test_category_fallback_respects_the_discipline_veto(title, category, expected):
    """
    classify_title() vetoes a software family when the title names another
    discipline, but the category fallback below it did not -- so a civil or
    mechanical role that an aggregator had filed under "Software Engineering"
    came back as software by the back door. 141 postings in the live database
    took that path, and several reached the top 40 of the ranked list
    ("Structural Design Intern", "Water Resources Design Intern").
    """
    from jobrank import postings as posting_facts
    assert posting_facts.role_families({"role": title, "category": category}) == expected


# --- enrichment robustness ---------------------------------------------------

def test_a_company_describing_its_own_age_is_not_a_requirement():
    """
    "a leading global asset manager with over 65 years of experience helping..."
    is the employer's history, not something it wants from a candidate. That
    one line failed schema validation and aborted every refresh after it; the
    quieter version is a boast inside the schema's range, which would silently
    make an internship look unreachable.
    """
    from jobrank import enrich
    boast = ("Title: Software Engineer Intern\n"
             "American Century Investments is a global asset manager with over "
             "65 years of experience helping clients.")
    assert enrich.rules_extract(boast)["required_years"] is None

    in_range = ("Title: Software Engineer Intern\n"
                "We are a firm with 20 years of experience serving customers.")
    assert enrich.rules_extract(in_range)["required_years"] is None

    # A genuine requirement still reads.
    real = "Title: Backend Engineer\nRequires 5 years of experience in distributed systems."
    assert enrich.rules_extract(real)["required_years"] == 5


def test_one_unenrichable_posting_does_not_abort_the_run(monkeypatch, tmp_path):
    """Enrichment is optional (invariant 9); a bad row is skipped, not fatal."""
    from jobrank import enrich, storage
    conn = storage.connect()
    rows = [make_posting(id="good:1", role="Backend Engineer Intern"),
            make_posting(id="bad:1", role="Data Engineer Intern")]

    real = enrich.enrich_one

    def explode(posting, conn=None):
        if postings.field(posting, "id") == "bad:1":
            raise ValueError("$.required_years: 65 > maximum 40")
        return real(posting, conn=conn)

    monkeypatch.setattr(enrich, "enrich_one", explode)
    stats = enrich.enrich_all(conn, rows)
    assert stats["failed"] == 1 and stats["enriched"] == 1
