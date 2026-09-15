import os
from datetime import date

import pytest

from jobrank.config import profile as cfg
from jobrank.models import Education, Experience, ParsedResume, Preferences, Project
from jobrank.profile import derive as derive_mod
from jobrank.profile import preferences as prefs
from jobrank.profile import store
from jobrank.resume import parser

TODAY = date(2026, 9, 15)


def _resume(experience=(), education=(), projects=(), skills=()):
    return ParsedResume(experience=list(experience), education=list(education),
                        projects=list(projects), skills=list(skills), content_hash="h")


class TestPreferences:
    def test_valid(self, example_prefs):
        p = prefs.validate(example_prefs)
        assert p.priorities["seniority"] == 5 and p.earliest_start == "2027-05"

    def test_all_problems_reported_together(self):
        with pytest.raises(prefs.PreferenceError) as exc:
            prefs.validate({"target_roles": "Pastry Chef", "seniority": ["wizard"], "remote": "mars",
                            "company_sizes": ["huge"], "exclude_industries": ["piracy"],
                            "earliest_start": "someday", "priorities": {"skills": 9, "vibes": 3}})
        assert len(exc.value.problems) == 8

    def test_lists_are_deduplicated_and_bounded(self):
        p = prefs.validate({"locations": ", ".join(["NYC", "nyc"] + [f"City{i}" for i in range(40)])})
        assert p.locations[0] == "NYC" and len(p.locations) == cfg.MAX_LIST_ITEMS

    def test_interactive_prompt_reasks_invalid_answers(self):
        answers = iter(["Chef", "", "", "", "", "", "", "", "", "", "", "", "", "Backend Engineer"])
        said = []
        result = prefs.prompt(ask=lambda q: next(answers), say=said.append)
        assert result.target_roles == ["Backend Engineer"]
        assert any("Unrecognized target roles" in s for s in said)


class TestSeniority:
    def test_student_near_graduation_is_eligible_for_entry(self):
        resume = _resume(education=[Education(level="bachelor", graduation="2027-05", expected=True)])
        profile = derive_mod.derive("u", resume, Preferences(), today=TODAY)
        assert profile.seniority.level == "intern" and profile.seniority.eligible_levels == ["intern", "entry"]

    def test_student_far_from_graduation_is_intern_only(self):
        resume = _resume(education=[Education(level="bachelor", graduation="2029-05", expected=True)])
        assert derive_mod.derive("u", resume, Preferences(), today=TODAY).seniority.eligible_levels == ["intern"]

    def test_years_drive_level_and_overlaps_count_once(self):
        jobs = [Experience(title="Software Engineer", start="2020-01", end="2022-12"),
                Experience(title="Consultant", start="2022-06", end="2023-12")]
        profile = derive_mod.derive("u", _resume(jobs), Preferences(), today=TODAY)
        assert profile.seniority.years_experience == 4.0 and profile.seniority.level == "mid"

    def test_internship_and_part_time_are_discounted(self):
        jobs = [Experience(title="SWE Intern", start="2025-01", end="2025-12", is_internship=True),
                Experience(title="Tutor", start="2024-01", end="2024-12", is_part_time=True)]
        profile = derive_mod.derive("u", _resume(jobs), Preferences(), today=TODAY)
        assert profile.seniority.years_experience == 1.0


class TestSkills:
    def test_demonstrated_beats_listed_and_recency_decays(self):
        jobs = [Experience(title="Engineer", start="2016-01", end="2017-01", bullets=["Wrote Java services"]),
                Experience(title="Engineer", start="2025-01", current=True, bullets=["Wrote Python services"])]
        profile = derive_mod.derive("u", _resume(jobs, skills=["python", "java", "rust"]), Preferences(),
                                    today=TODAY)
        assert profile.skills["python"] == 1.0
        assert profile.skills["java"] == cfg.SKILL_RECENCY_FLOOR
        assert profile.skills["rust"] == cfg.SKILL_EVIDENCE_WEIGHT["skills_section"]


class TestAffinity:
    def test_evidence_only_without_stated_targets(self):
        jobs = [Experience(title="Data Engineer", start="2023-01", end="2025-01",
                           bullets=["Built Spark and Airflow ETL jobs"])]
        profile = derive_mod.derive("u", _resume(jobs, skills=["spark", "airflow"]), Preferences(), today=TODAY)
        top = max(profile.role_affinity, key=profile.role_affinity.get)
        assert top == "data_engineering"
        assert min(profile.role_affinity.values()) >= cfg.AFFINITY_FLOOR

    def test_stated_targets_blend_with_evidence(self):
        jobs = [Experience(title="Data Engineer", start="2023-01", end="2025-01")]
        profile = derive_mod.derive("u", _resume(jobs), Preferences(target_roles=["Security Engineer"]),
                                    today=TODAY)
        assert profile.target_families == ["security"]
        assert profile.role_affinity["security"] > profile.role_affinity["data_engineering"] > \
            profile.role_affinity["mobile"]

    def test_no_family_is_ever_zero(self):
        profile = derive_mod.derive("u", _resume(), Preferences(target_roles=["Backend Engineer"]), today=TODAY)
        assert all(v >= cfg.AFFINITY_FLOOR for v in profile.role_affinity.values())


def test_priorities_become_exponents():
    weights = derive_mod.factor_weights(Preferences(priorities={"skills": 5, "freshness": 1}))
    assert weights["skills"] == cfg.PRIORITY_EXPONENTS[5]
    assert weights["freshness"] == cfg.PRIORITY_EXPONENTS[1]
    assert weights["role"] == cfg.PRIORITY_EXPONENTS[cfg.DEFAULT_PRIORITY]


def test_two_users_same_posting_world_different_profiles(example_resume_text, plain_resume_text):
    """The generalization claim: nothing about the output is fixed per codebase."""
    a = derive_mod.derive("a", parser.parse_text(example_resume_text), Preferences(), today=TODAY)
    b = derive_mod.derive("b", parser.parse_text(plain_resume_text), Preferences(), today=TODAY)
    assert a.seniority.level != b.seniority.level
    assert max(a.role_affinity, key=a.role_affinity.get) != max(b.role_affinity, key=b.role_affinity.get) or \
        a.skills != b.skills


class TestStore:
    def test_roundtrip_and_rederive(self, example_resume_text, example_prefs):
        resume = parser.parse_text(example_resume_text)
        saved = store.save("alex", resume, prefs.validate(example_prefs))
        loaded = store.load("alex")
        assert loaded.skills == saved.skills and loaded.target_families == saved.target_families
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
            "user_id": "web", "target_roles": "Backend Engineer", "remote": "any", "priority_skills": "5",
            "resume": (io.BytesIO(example_resume_text.encode()), "resume.md")})
        assert response.status_code == 302
        page = client.get("/profile?id=web")
        assert page.status_code == 200 and b"What the scorer sees" in page.data
        assert store.load("web").factor_weights["skills"] == cfg.PRIORITY_EXPONENTS[5]

    def test_errors_all_shown_and_nothing_saved(self, client):
        response = client.post("/profile", content_type="multipart/form-data",
                               data={"user_id": "Bad Id", "target_roles": "Chef"})
        assert response.status_code == 400
        assert b"Profile id must" in response.data and b"Unrecognized target roles" in response.data
        assert store.list_ids() == []

    def test_cross_site_post_refused(self, client):
        response = client.post("/profile", data={"user_id": "x"}, headers={"Origin": "https://evil.example"})
        assert response.status_code == 403


class TestCli:
    def test_create_and_show(self, tmp_path, capsys):
        from jobrank.cli import main
        examples = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples")
        code = main(["profile", "create", "--id", "cli", "--resume", os.path.join(examples, "resume_example.md"),
                     "--prefs", os.path.join(examples, "preferences_example.json")])
        assert code == 0 and "Saved" in capsys.readouterr().out
        assert main(["profile", "show", "--id", "cli"]) == 0

    def test_bad_resume_exits_nonzero_with_reason(self, tmp_path, capsys):
        from jobrank.cli import main
        path = tmp_path / "empty.txt"
        path.write_text("hi")
        assert main(["profile", "create", "--id", "cli", "--resume", str(path)]) == 2
        assert "characters of text" in capsys.readouterr().err
