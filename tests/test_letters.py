"""
Copy-paste application prompts: cover letter and work-experience, adapted per
role family, honest about what the profile does and doesn't support.

Ported from tests/legacy_checks.py (test_prompts).

ADAPTATION: the legacy version loaded a single hardcoded résumé from
`config.PROFILE_PATH` (profile.md / profile_example.md) via a module-level
cache (scorer._PROFILE_CACHE) that has since been deleted along with
scorer.py. Profiles are now per-user, stored on disk via jobrank.profile.store
and resolved through jobrank.profile.active; letters.load_profile(user_id)
reads one of those and renders it with letters.format_resume() instead of
slurping a markdown file. The "PROFILE LOADING" section is rewritten against
that API: a LetterError when no profile exists (instead of pointing at
profile_example.md, it now points at `/profile` / `run.py profile create`),
and a LetterError when the résumé has too little content once formatted
(instead of a near-empty profile.md).

Everything else — the honesty rules, the per-role-family guidance text, the
pasted-job-description handling, and "no paid API" — is unchanged, since
letters.py's prompt-building itself was not touched by the rebuild beyond
where it gets its profile text from.
"""

import os

import pytest

from jobrank import config, letters
from jobrank.models import Education, ParsedResume, Preferences
from jobrank.profile import store


POSTING = {
    "company": "Acme", "role": "Solutions Engineer Intern",
    "category": "Software Engineering", "location": "Austin, TX",
    "apply_url": "https://acme.com/apply", "age_text": "2d",
    "role_family": "forward_deployed",
}
PROFILE_TEXT = "MY-UNIQUE-PROFILE-MARKER"


# =============================================================================
def test_prompt_carries_profile_and_posting():
    cover = letters.cover_letter_prompt(POSTING, PROFILE_TEXT)
    experience = letters.work_experience_prompt(POSTING, PROFILE_TEXT)

    for name, text in (("cover letter", cover), ("work experience", experience)):
        flat = " ".join(text.split())
        assert "MY-UNIQUE-PROFILE-MARKER" in text, f"the {name} prompt carries the profile"
        assert "Acme" in text and "Solutions Engineer Intern" in text, (
            f"the {name} prompt carries the posting"
        )
        assert "Use only what the candidate profile below states" in flat, (
            f"the {name} prompt keeps the honesty rule"
        )
        assert "do not invent requirements that were never stated" in flat, (
            f"the {name} prompt forbids inventing requirements"
        )


def test_role_family_changes_the_pitch():
    """
    The whole point of role families: different documents for different
    audiences, from the same profile. Taxonomy family ids are used directly
    now ("forward_deployed", "software_engineering"), not the old free-text
    labels ("Forward-Deployed / Solutions").
    """
    fde = letters.cover_letter_prompt(POSTING, PROFILE_TEXT)
    swe_posting = dict(POSTING, role_family="software_engineering")
    swe = letters.cover_letter_prompt(swe_posting, PROFILE_TEXT)

    assert fde != swe, "an FDE letter prompt differs from a SWE one"
    assert "customers" in fde and "customers" not in swe, (
        "the FDE prompt asks for customer evidence; the SWE one doesn't"
    )
    assert "hardest engineering problem" in swe, (
        "the SWE prompt asks for technical depth"
    )


def test_unknown_family_falls_back_gracefully():
    unknown = letters.cover_letter_prompt(
        dict(POSTING, role_family="Nonexistent"), PROFILE_TEXT)
    assert len(unknown) > 500, "an unrecognized family falls back gracefully"


def test_pasted_job_description_reaches_the_prompt():
    """The pasted job description is the biggest quality lever available."""
    with_jd = letters.cover_letter_prompt(
        POSTING, PROFILE_TEXT, job_description="Must know Kubernetes and Go.")
    assert "Kubernetes" in with_jd, "a pasted job description reaches the prompt"
    assert "prefer it over inference" in " ".join(with_jd.split()), (
        "the prompt says to prefer the real description over the title"
    )

    without = letters.cover_letter_prompt(POSTING, PROFILE_TEXT)
    assert "No job description was pasted" in without, (
        "the prompt says when it's working from the title alone"
    )


def test_letters_module_makes_no_api_calls():
    """These prompts are free by design — nothing should reach for an API."""
    source = open(letters.__file__, encoding="utf-8").read()
    assert "anthropic" not in source.lower(), (
        "letters.py makes no API calls and costs nothing"
    )


# =============================================================================
# Profile loading: now backed by the per-user profile store, not a hardcoded
# profile.md.
# =============================================================================

def _point_at_temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "internships.db"))
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / "applications.db"))
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", str(tmp_path / "applications.json"))


def test_missing_profile_raises_with_pointer_to_creating_one(monkeypatch, tmp_path):
    """No profile has ever been created: load_profile must say so, and say how
    to fix it — the current equivalent of the old 'points at profile_example.md'
    check."""
    _point_at_temp_db(monkeypatch, tmp_path)
    with pytest.raises(letters.LetterError) as exc:
        letters.load_profile("nobody-has-this-id")
    assert "No profile exists yet" in str(exc.value), (
        "a missing profile explains that none exists"
    )
    assert "profile create" in str(exc.value), (
        "a missing profile points at how to create one"
    )


def test_near_empty_profile_is_rejected_not_used(monkeypatch, tmp_path):
    """
    A résumé with almost nothing in it — here, a bare education line and no
    experience, projects or skills — formats to well under load_profile()'s
    200-character floor and must be rejected rather than handed to the model
    as if it were real signal.
    """
    _point_at_temp_db(monkeypatch, tmp_path)
    thin = ParsedResume(
        education=[Education(institution="X", degree="BS", level="bachelor")],
        content_hash="h",
    )
    store.save("thin", thin, Preferences())

    with pytest.raises(letters.LetterError) as exc:
        letters.load_profile("thin")
    assert "almost no experience" in str(exc.value), (
        "a near-empty profile is rejected, not used"
    )


def test_real_profile_loads_and_formats(monkeypatch, tmp_path, example_resume_text):
    """A real résumé loads through the profile store and reaches the prompt."""
    from jobrank.resume import parser

    _point_at_temp_db(monkeypatch, tmp_path)
    resume = parser.parse_text(example_resume_text)
    store.save("tester", resume, Preferences())

    text = letters.load_profile("tester")
    assert len(text) >= 200, "a real résumé clears the content floor"

    cover = letters.cover_letter_prompt(POSTING, text)
    assert "Acme" in cover, "the loaded profile flows into a real prompt"
