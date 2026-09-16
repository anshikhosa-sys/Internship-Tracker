"""
What changed between two derived profiles.

Uploading a new résumé silently rewrites everything the ranking is built on.
Showing the difference makes that legible: which skills appeared, which
dropped out, whether seniority moved, and which role families the ranking will
now favour. It is also the fastest way to catch a bad parse — a résumé that
extracted poorly shows up as a dozen skills vanishing at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobrank.models import ParsedResume, Profile


@dataclass
class ProfileDiff:
    skills_added: list[str] = field(default_factory=list)
    skills_removed: list[str] = field(default_factory=list)
    seniority_before: str = ""
    seniority_after: str = ""
    years_before: float = 0.0
    years_after: float = 0.0
    experience_before: int = 0
    experience_after: int = 0
    projects_before: int = 0
    projects_after: int = 0
    affinity_moves: list[tuple[str, float, float]] = field(default_factory=list)
    extractor: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def seniority_changed(self) -> bool:
        return self.seniority_before != self.seniority_after

    @property
    def is_empty(self) -> bool:
        return not (self.skills_added or self.skills_removed or self.seniority_changed
                    or self.affinity_moves or self.experience_before != self.experience_after)

    def looks_like_a_worse_parse(self) -> bool:
        """A drop this large is usually a parse failure, not a rewritten résumé."""
        return len(self.skills_removed) >= 5 and len(self.skills_removed) > 2 * len(self.skills_added)


def compare(before: Profile | None, after: Profile, resume: ParsedResume,
            previous_resume: ParsedResume | None = None, top_moves: int = 4,
            move_threshold: float = 0.05) -> ProfileDiff:
    diff = ProfileDiff(
        seniority_after=after.seniority.level,
        years_after=after.seniority.years_experience,
        experience_after=len(resume.experience),
        projects_after=len(resume.projects),
        extractor=resume.extractor,
        warnings=list(resume.warnings),
    )
    if before is None:
        diff.skills_added = sorted(after.skills)
        diff.seniority_before = after.seniority.level
        diff.years_before = after.seniority.years_experience
        return diff

    diff.skills_added = sorted(set(after.skills) - set(before.skills))
    diff.skills_removed = sorted(set(before.skills) - set(after.skills))
    diff.seniority_before = before.seniority.level
    diff.years_before = before.seniority.years_experience
    if previous_resume is not None:
        diff.experience_before = len(previous_resume.experience)
        diff.projects_before = len(previous_resume.projects)

    moves = []
    for family, value in after.role_affinity.items():
        was = before.role_affinity.get(family, 0.0)
        if abs(value - was) >= move_threshold:
            moves.append((family, was, value))
    diff.affinity_moves = sorted(moves, key=lambda m: -abs(m[2] - m[1]))[:top_moves]
    return diff
