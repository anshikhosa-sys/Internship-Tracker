import os
from datetime import date

import pytest

from jobrank.config import profile as cfg
from jobrank.models import Education, Experience, ParsedResume, Project
from jobrank.profile import derive as derive_mod
from jobrank.profile import store
from jobrank.resume import parser

TODAY = date(2026, 9, 15)


def _resume(experience=(), education=(), projects=(), skills=()):
    return ParsedResume(experience=list(experience), education=list(education),
                        projects=list(projects), skills=list(skills), content_hash="h")


class TestSeniority:
    def test_student_near_graduation_is_eligible_for_entry(self):
        resume = _resume(education=[Education(level="bachelor", graduation="2027-05", expected=True)])
        profile = derive_mod.derive("u", resume, today=TODAY)
        assert profile.seniority.level == "intern" and profile.seniority.eligible_levels == ["intern", "entry"]

    def test_student_far_from_graduation_is_intern_only(self):
        resume = _resume(education=[Education(level="bachelor", graduation="2029-05", expected=True)])
        assert derive_mod.derive("u", resume, today=TODAY).seniority.eligible_levels == ["intern"]

    def test_years_drive_level_and_overlaps_count_once(self):
        jobs = [Experience(title="Software Engineer", start="2020-01", end="2022-12"),
                Experience(title="Consultant", start="2022-06", end="2023-12")]
        profile = derive_mod.derive("u", _resume(jobs), today=TODAY)
        assert profile.seniority.years_experience == 4.0 and profile.seniority.level == "mid"

    def test_internship_and_part_time_are_discounted(self):
        jobs = [Experience(title="SWE Intern", start="2025-01", end="2025-12", is_internship=True),
                Experience(title="Tutor", start="2024-01", end="2024-12", is_part_time=True)]
        profile = derive_mod.derive("u", _resume(jobs), today=TODAY)
        assert profile.seniority.years_experience == 1.0


class TestSkills:
    def test_demonstrated_beats_listed_and_recency_decays(self):
        jobs = [Experience(title="Engineer", start="2016-01", end="2017-01", bullets=["Wrote Java services"]),
                Experience(title="Engineer", start="2025-01", current=True, bullets=["Wrote Python services"])]
        profile = derive_mod.derive("u", _resume(jobs, skills=["python", "java", "rust"]), today=TODAY)
        assert profile.skills["python"] == 1.0
        assert profile.skills["java"] == cfg.SKILL_RECENCY_FLOOR
        assert profile.skills["rust"] == cfg.SKILL_EVIDENCE_WEIGHT["skills_section"]


class TestAffinity:
    def test_evidence_only_without_stated_targets(self):
        jobs = [Experience(title="Data Engineer", start="2023-01", end="2025-01",
                           bullets=["Built Spark and Airflow ETL jobs"])]
        profile = derive_mod.derive("u", _resume(jobs, skills=["spark", "airflow"]), today=TODAY)
        top = max(profile.role_affinity, key=profile.role_affinity.get)
        assert top == "data_engineering"
        assert min(profile.role_affinity.values()) >= cfg.AFFINITY_FLOOR

    def test_affinity_is_evidence_only(self):
        """
        The whole point of the model. Two years as a Data Engineer decides the
        ranking; there is no longer any way to tell the platform you would
        rather be a Security Engineer, because a wish cannot change the odds.
        """
        jobs = [Experience(title="Data Engineer", start="2023-01", end="2025-01")]
        profile = derive_mod.derive("u", _resume(jobs), today=TODAY)
        assert profile.role_affinity["data_engineering"] > profile.role_affinity["security"]

    def test_a_profile_is_a_resume_and_nothing_else(self):
        """Anyone's résumé can be dropped in and scored, with no setup."""
        import inspect
        parameters = set(inspect.signature(derive_mod.derive).parameters)
        assert parameters == {"user_id", "resume", "today"}

    def test_no_family_is_ever_zero(self):
        profile = derive_mod.derive("u", _resume(), today=TODAY)
        assert all(v >= cfg.AFFINITY_FLOOR for v in profile.role_affinity.values())


def test_exponents_are_global_not_per_user():
    """
    A 1-5 priority rating was a preference wearing a weight's clothes: it
    reordered the list with no evidence it improved the odds. Exponents are now
    the same for everyone.
    """
    from jobrank.config import scoring as scoring_cfg
    from jobrank.scoring import engine
    a = derive_mod.derive("a", _resume(), today=TODAY)
    assert engine.effective_weights(a) == scoring_cfg.FACTOR_EXPONENTS


def test_two_users_same_posting_world_different_profiles(example_resume_text, plain_resume_text):
    """The generalization claim: nothing about the output is fixed per codebase."""
    a = derive_mod.derive("a", parser.parse_text(example_resume_text), today=TODAY)
    b = derive_mod.derive("b", parser.parse_text(plain_resume_text), today=TODAY)
    assert a.seniority.level != b.seniority.level
    assert max(a.role_affinity, key=a.role_affinity.get) != max(b.role_affinity, key=b.role_affinity.get) or \
        a.skills != b.skills


class TestStore:
    def test_roundtrip_and_rederive(self, example_resume_text):
        resume = parser.parse_text(example_resume_text)
        saved = store.save("alex", resume)
        loaded = store.load("alex")
        assert loaded.skills == saved.skills and loaded.role_affinity == saved.role_affinity
        assert store.list_ids() == ["alex"]

    @pytest.mark.parametrize("bad", ["../etc", "UPPER", "a/b", "", "x" * 41, ".hidden"])
    def test_user_id_cannot_escape_profiles_dir(self, bad):
        with pytest.raises(ValueError):
            store.path_for(bad)

    def test_missing_profile_points_at_the_fix(self):
        with pytest.raises(store.ProfileNotFound, match="run.py profile create"):
            store.load("nobody")


class TestWebForm:
    @pytest.fixture
    def client(self):
        from jobrank.web.app import app
        return app.test_client()

    def test_create_then_view(self, client, example_resume_text):
        import io
        response = client.post("/profile", content_type="multipart/form-data", data={
            "user_id": "web", "target_roles": "Backend Engineer", "remote": "any",
            "resume": (io.BytesIO(example_resume_text.encode()), "resume.md")})
        assert response.status_code == 302
        page = client.get("/profile?id=web")
        assert page.status_code == 200 and b"What the scorer sees" in page.data
        # A résumé alone is a complete profile: the form asks for nothing the
        # scorer needs, so anyone's résumé can be dropped in and ranked.
        assert store.load("web").skills

    def test_errors_all_shown_and_nothing_saved(self, client):
        response = client.post("/profile", content_type="multipart/form-data",
                               data={"user_id": "Bad Id"})
        assert response.status_code == 400
        assert b"Profile id must" in response.data

    def test_the_form_asks_for_nothing_but_a_resume(self, client):
        """
        The platform does not ask what kind of job you want. If these ever come
        back, scoring stops being a statement about the odds.
        """
        page = client.get("/profile").data.decode()
        for gone in ["target_roles", "exclude_industries", "company_sizes",
                     "earliest_start", "priority_", "What you want"]:
            assert gone not in page
        assert store.list_ids() == []

    def test_cross_site_post_refused(self, client):
        response = client.post("/profile", data={"user_id": "x"}, headers={"Origin": "https://evil.example"})
        assert response.status_code == 403


class TestCli:
    def test_create_and_show(self, tmp_path, capsys):
        from jobrank.cli import main
        examples = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples")
        code = main(["profile", "create", "--id", "cli", "--resume", os.path.join(examples, "resume_example.md"),
                     ])
        assert code == 0 and "Saved" in capsys.readouterr().out
        assert main(["profile", "show", "--id", "cli"]) == 0

    def test_bad_resume_exits_nonzero_with_reason(self, tmp_path, capsys):
        from jobrank.cli import main
        path = tmp_path / "empty.txt"
        path.write_text("hi")
        assert main(["profile", "create", "--id", "cli", "--resume", str(path)]) == 2
        assert "characters of text" in capsys.readouterr().err
