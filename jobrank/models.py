"""
Records shared across subsystems: what a résumé parses into, what a user
states, and the derived profile the scorer reads.

All are plain dataclasses with `to_dict`/`from_dict`, so a profile on disk is
readable JSON and a schema change shows up as a diff. No contact details are
modeled: name, email and phone are never extracted, because nothing downstream
needs them and a profile without them is safe to log.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


def _from_dict(cls, data: dict[str, Any]):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in (data or {}).items() if k in names})


@dataclass
class Education:
    institution: str = ""
    degree: str = ""              # as written: "B.S. Computer Science"
    level: str = "none"           # config.taxonomy.DEGREE_ORDER
    field: str = ""
    graduation: str | None = None  # "YYYY-MM"
    expected: bool = False        # graduation is in the future as written


@dataclass
class Experience:
    title: str = ""
    organization: str = ""
    start: str | None = None      # "YYYY-MM"
    end: str | None = None        # "YYYY-MM"; None with current=True means ongoing
    current: bool = False
    is_internship: bool = False
    is_part_time: bool = False
    bullets: list[str] = field(default_factory=list)


@dataclass
class Project:
    name: str = ""
    description: str = ""
    technologies: list[str] = field(default_factory=list)
    start: str | None = None
    end: str | None = None


@dataclass
class ParsedResume:
    education: list[Education] = field(default_factory=list)
    experience: list[Experience] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)          # canonical taxonomy names
    other_skills: list[str] = field(default_factory=list)    # listed but not in taxonomy
    content_hash: str = ""
    extractor: str = ""           # "rules" or "ollama:<model>"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParsedResume":
        data = dict(data or {})
        data["education"] = [_from_dict(Education, e) for e in data.get("education", [])]
        data["experience"] = [_from_dict(Experience, e) for e in data.get("experience", [])]
        data["projects"] = [_from_dict(Project, p) for p in data.get("projects", [])]
        return _from_dict(cls, data)

    def full_text(self) -> str:
        """Everything the résumé claims, as one string — the grounding corpus for tailoring."""
        parts = []
        for e in self.education:
            parts += [e.institution, e.degree, e.field]
        for x in self.experience:
            parts += [x.title, x.organization, *x.bullets]
        for p in self.projects:
            parts += [p.name, p.description, *p.technologies]
        parts += self.skills + self.other_skills
        return "\n".join(s for s in parts if s)


@dataclass
class SeniorityEstimate:
    level: str = "entry"
    years_experience: float = 0.0
    is_student: bool = False
    graduation: str | None = None
    degree_level: str = "none"
    eligible_levels: list[str] = field(default_factory=list)


@dataclass
class Profile:
    """
    The derived profile: everything the scorer needs, computed from one résumé.

    There is deliberately nothing here about what the user *wants*. The score
    answers whether an application is worth making, and a stated wish cannot
    change that — it adds no skill and makes no posting less contested. The
    platform asks for a résumé and nothing else.
    """

    user_id: str
    skills: dict[str, float] = field(default_factory=dict)          # canonical -> 0..1
    seniority: SeniorityEstimate = field(default_factory=SeniorityEstimate)
    role_affinity: dict[str, float] = field(default_factory=dict)   # family -> 0..1
    semantic_text: str = ""       # what gets embedded for similarity
    resume_hash: str = ""
    derived_at: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)          # why each value is what it is

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        data = dict(data or {})
        data["seniority"] = _from_dict(SeniorityEstimate, data.get("seniority", {}))
        return _from_dict(cls, data)
