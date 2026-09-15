"""
Résumé text -> ParsedResume.

    parse_file("resume.pdf")      # read, extract (model or rules), canonicalize
    parse_text(text)

Extraction goes through `jobrank.llm.structured`, which caches by content hash:
re-uploading the same résumé never re-runs extraction. Whatever produced the
raw JSON, the same post-processing follows — skill canonicalization against the
taxonomy, degree levels, internship detection, date normalization — so model
and rule output land in an identical, comparable shape.
"""

from __future__ import annotations

from functools import lru_cache

from jobrank import llm, textmatch
from jobrank.config import taxonomy
from jobrank.llm.cache import content_hash
from jobrank.models import Education, Experience, ParsedResume, Project
from jobrank.resume import dates, readers, rules
from jobrank.resume.rules import ResumeParseError
from jobrank.resume.readers import ResumeReadError

__all__ = ["parse_file", "parse_bytes", "parse_text", "ResumeParseError", "ResumeReadError",
           "canonical_skills", "RESUME_SCHEMA"]

_NULLABLE_STR = {"type": ["string", "null"]}
RESUME_SCHEMA = {
    "type": "object",
    "required": ["education", "experience", "projects", "skills"],
    "properties": {
        "education": {"type": "array", "maxItems": 10, "items": {
            "type": "object",
            "required": ["institution", "degree"],
            "properties": {
                "institution": {"type": "string"}, "degree": {"type": "string"},
                "field": {"type": "string"}, "graduation": _NULLABLE_STR,
                "expected": {"type": "boolean"},
            },
        }},
        "experience": {"type": "array", "maxItems": 30, "items": {
            "type": "object",
            "required": ["title", "organization", "bullets"],
            "properties": {
                "title": {"type": "string"}, "organization": {"type": "string"},
                "start": _NULLABLE_STR, "end": _NULLABLE_STR, "current": {"type": "boolean"},
                "bullets": {"type": "array", "items": {"type": "string"}},
            },
        }},
        "projects": {"type": "array", "maxItems": 30, "items": {
            "type": "object",
            "required": ["name", "description"],
            "properties": {
                "name": {"type": "string"}, "description": {"type": "string"},
                "technologies": {"type": "array", "items": {"type": "string"}},
                "start": _NULLABLE_STR, "end": _NULLABLE_STR,
            },
        }},
        "skills": {"type": "array", "maxItems": 200, "items": {"type": "string"}},
    },
}

SYSTEM_PROMPT = """You extract structured data from résumés.
Rules:
- Copy text exactly as written. Never infer, embellish, or add anything absent from the résumé.
- Do NOT extract the person's name, email, phone, address, or links.
- Dates as "YYYY-MM". A range ending in Present/Current: end null, current true.
- skills: every technology, language, framework, or tool listed anywhere, as written.
- bullets: each accomplishment line of a job, verbatim."""


def _prompt(text: str) -> str:
    return f"Extract this résumé into the JSON schema.\n\n<resume>\n{text}\n</resume>"


def parse_file(path: str) -> ParsedResume:
    return parse_text(readers.read_path(path))


def parse_bytes(data: bytes, filename: str) -> ParsedResume:
    return parse_text(readers.read_bytes(data, filename))


def parse_text(text: str, conn=None) -> ParsedResume:
    if not text or not text.strip():
        raise ResumeReadError("The résumé is empty.")
    extraction = llm.structured(
        task="resume_extract", text=text, schema=RESUME_SCHEMA, system=SYSTEM_PROMPT,
        build_prompt=_prompt, rules=rules.extract, conn=conn,
    )
    resume = _normalize(extraction.data)
    resume.content_hash = content_hash(text)
    resume.extractor = extraction.extractor
    resume.warnings = _warnings(resume)
    if not resume.skills and not resume.experience and not resume.projects:
        raise ResumeParseError("Extraction produced no skills, experience, or projects.")
    return resume


def canonical_skills(text: str) -> list[str]:
    """Taxonomy skills mentioned in free text, in taxonomy order."""
    return list(_canonical_skills(text or ""))


@lru_cache(maxsize=65536)
def _canonical_skills(text: str) -> tuple[str, ...]:
    found = []
    for name, spec in taxonomy.SKILLS.items():
        if textmatch.find_all(text, spec.get("aliases", [])) or \
                textmatch.find_all(text, spec.get("case_sensitive", []), case_sensitive=True):
            found.append(name)
    return tuple(found)


def _ym(value) -> str | None:
    if not value:
        return None
    return dates.parse_point(str(value)) or (value if isinstance(value, str) and len(value) == 7 and value[4] == "-" else None)


def _normalize(data: dict) -> ParsedResume:
    today = dates.today_ym()
    education = []
    for e in data.get("education", []):
        graduation = _ym(e.get("graduation"))
        education.append(Education(
            institution=e.get("institution", "").strip(),
            degree=e.get("degree", "").strip(),
            level=rules.degree_level(e.get("degree", "")),
            field=(e.get("field") or rules.degree_field(e.get("degree", ""))).strip(),
            graduation=graduation,
            expected=bool(e.get("expected")) or bool(graduation and graduation > today),
        ))

    experience = []
    for x in data.get("experience", []):
        title = x.get("title", "").strip()
        experience.append(Experience(
            title=title,
            organization=x.get("organization", "").strip(),
            start=_ym(x.get("start")),
            end=_ym(x.get("end")),
            current=bool(x.get("current")),
            is_internship=bool(textmatch.find_all(title, taxonomy.INTERNSHIP_TITLE_MARKERS)),
            is_part_time=bool(textmatch.find_all(title, taxonomy.PART_TIME_TITLE_MARKERS)),
            bullets=[b.strip() for b in x.get("bullets", []) if b and b.strip()],
        ))

    projects = [Project(
        name=p.get("name", "").strip(),
        description=p.get("description", "").strip(),
        technologies=[t.strip() for t in p.get("technologies", []) if t and t.strip()],
        start=_ym(p.get("start")),
        end=_ym(p.get("end")),
    ) for p in data.get("projects", [])]

    listed = [s for s in data.get("skills", []) if s and s.strip()]
    corpus = "\n".join(
        listed
        + [b for x in experience for b in x.bullets]
        + [x.title for x in experience]
        + [p.description + " " + " ".join(p.technologies) for p in projects]
    )
    skills = canonical_skills(corpus)
    other = [s for s in listed if not canonical_skills(s)]
    return ParsedResume(education=education, experience=experience, projects=projects,
                        skills=skills, other_skills=other)


def _warnings(resume: ParsedResume) -> list[str]:
    notes = []
    if not resume.education:
        notes.append("No education section found; seniority is estimated from experience alone.")
    undated = [x.title or x.organization for x in resume.experience if not x.start]
    if undated:
        notes.append(f"No dates found for: {', '.join(undated)}. They count toward skills but not years of experience.")
    if not resume.experience:
        notes.append("No work experience found; role affinity comes from projects and stated targets.")
    if not resume.skills:
        notes.append("No recognized technical skills found; skill-overlap scoring will be neutral.")
    return notes
