"""
Parsed résumé -> derived Profile. The résumé is the only input.

This is where "tuned to one person" becomes "computed for any person". Every
value the scorer reads is produced here from the user's own evidence, using
rules in config/profile.py that are identical for everyone:

  skills          where each skill was demonstrated, and how recently
  seniority       months of dated experience (internships credited at a
                  discount) plus education status — never self-reported
  role_affinity   past titles weighted by tenure, topic evidence in bullets
                  and projects, blended with stated target roles
  factor_weights  the user's 1-5 priority ratings mapped to exponents

`evidence` records the inputs behind each number, so `--explain` can show why
a profile looks the way it does.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

from jobrank import textmatch
from jobrank.config import profile as cfg
from jobrank.config import taxonomy
from jobrank.models import ParsedResume, Profile, SeniorityEstimate
from jobrank.resume import dates
from jobrank.resume.parser import canonical_skills
from jobrank.roles import classify_title


def derive(user_id: str, resume: ParsedResume, today: date | None = None) -> Profile:
    """
    A résumé becomes everything the scorer needs — and nothing else is asked.

    The platform used to also take stated preferences: target roles, locations,
    remote preference, company sizes, excluded industries and a 1-5 priority
    per factor. All of it is gone. Measured on a real golden set, blending
    stated targets into role affinity scored a family with 0.393 of evidence
    above one with 0.713, purely because the first had been typed into a form.
    Dropping the same résumé in for anyone now produces the same ranking it
    would for its owner.
    """
    today = today or date.today()
    now_ym = dates.today_ym(today)
    skills, skill_evidence = _skills(resume, now_ym)
    seniority, months = _seniority(resume, now_ym)
    affinity, affinity_evidence = _affinity(resume, skills, now_ym)
    return Profile(
        user_id=user_id,
        skills=skills,
        seniority=seniority,
        role_affinity=affinity,
        semantic_text=semantic_text(resume),
        resume_hash=resume.content_hash,
        derived_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        evidence={"skills": skill_evidence, "experience_months": months, "role_affinity": affinity_evidence},
    )


# ---------------------------------------------------------------------------

def _years_since(end: str | None, now_ym: str) -> float:
    if not end:
        return 0.0
    return max(0, dates.months_between(end, now_ym) - 1) / 12


def _recency(end: str | None, current: bool, now_ym: str) -> float:
    if current or not end:
        return 1.0
    decay = 0.5 ** (_years_since(end, now_ym) / cfg.SKILL_RECENCY_HALF_LIFE_YEARS)
    return max(cfg.SKILL_RECENCY_FLOOR, decay)


def _skills(resume: ParsedResume, now_ym: str) -> tuple[dict[str, float], dict[str, list[str]]]:
    weights: dict[str, float] = {}
    sources: dict[str, list[str]] = {}

    def credit(skill: str, value: float, where: str) -> None:
        if value > weights.get(skill, 0.0):
            weights[skill] = value
        sources.setdefault(skill, []).append(where)

    for job in resume.experience:
        text = " ".join([job.title, *job.bullets])
        recency = _recency(job.end or job.start, job.current, now_ym)
        for skill in canonical_skills(text):
            credit(skill, cfg.SKILL_EVIDENCE_WEIGHT["experience"] * recency, f"experience: {job.title}")
    for project in resume.projects:
        text = " ".join([project.name, project.description, *project.technologies])
        recency = _recency(project.end or project.start, False, now_ym)
        for skill in canonical_skills(text):
            credit(skill, cfg.SKILL_EVIDENCE_WEIGHT["project"] * recency, f"project: {project.name}")
    for skill in resume.skills:
        if skill not in weights:
            credit(skill, cfg.SKILL_EVIDENCE_WEIGHT["skills_section"], "skills section")
    return {k: round(v, 3) for k, v in sorted(weights.items())}, sources


def _merged_months(spans: list[tuple[str, str]]) -> int:
    """Total months covered, counting overlapping jobs once."""
    ordered = sorted(spans)
    total, cur_start, cur_end = 0, None, None
    for start, end in ordered:
        if cur_end is None or start > cur_end:
            if cur_end is not None:
                total += dates.months_between(cur_start, cur_end)
            cur_start, cur_end = start, end
        elif end > cur_end:
            cur_end = end
    if cur_end is not None:
        total += dates.months_between(cur_start, cur_end)
    return total


def _seniority(resume: ParsedResume, now_ym: str) -> tuple[SeniorityEstimate, dict[str, int]]:
    spans: dict[str, list[tuple[str, str]]] = {"full_time": [], "internship": [], "part_time": []}
    for job in resume.experience:
        if not job.start:
            continue
        end = min(now_ym if (job.current or not job.end) else job.end, now_ym)
        if end < job.start:
            continue
        kind = "internship" if job.is_internship else "part_time" if job.is_part_time else "full_time"
        spans[kind].append((job.start, end))
    months = {kind: _merged_months(s) for kind, s in spans.items()}
    years = (months["full_time"]
             + months["internship"] * cfg.INTERNSHIP_MONTH_CREDIT
             + months["part_time"] * cfg.PART_TIME_MONTH_CREDIT) / 12

    degree_level = max((e.level for e in resume.education), key=taxonomy.DEGREE_ORDER.index, default="none")
    graduations = [e.graduation for e in resume.education if e.graduation]
    graduation = max(graduations) if graduations else None
    is_student = any(e.expected for e in resume.education) or bool(graduation and graduation > now_ym)

    if is_student:
        level = "intern"
        eligible = ["intern"]
        if graduation and dates.months_between(now_ym, graduation) <= cfg.NEW_GRAD_WINDOW_MONTHS:
            eligible.append("entry")
    else:
        level = "entry"
        for threshold, name in cfg.SENIORITY_YEARS_THRESHOLDS:
            if years >= threshold:
                level = name
        eligible = [level]

    estimate = SeniorityEstimate(level=level, years_experience=round(years, 2), is_student=is_student,
                                 graduation=graduation, degree_level=degree_level, eligible_levels=eligible)
    return estimate, months


def _affinity(resume: ParsedResume, skills: dict[str, float], now_ym: str):
    corpus = "\n".join(
        [b for job in resume.experience for b in job.bullets]
        + [p.description + " " + " ".join(p.technologies) for p in resume.projects]
    )
    evidence_score: dict[str, float] = {}
    detail: dict[str, dict] = {}
    for family, spec in taxonomy.ROLE_FAMILIES.items():
        score, titles, topics = 0.0, [], []
        for job in resume.experience:
            if family in classify_title(job.title):
                months = dates.months_between(job.start, job.end or now_ym) if job.start else 0
                score += max(cfg.AFFINITY_TITLE_MIN_WEIGHT, months / 12 * cfg.AFFINITY_TITLE_WEIGHT_PER_YEAR)
                titles.append(job.title)
        for term in spec["evidence"]:
            if term in taxonomy.SKILLS:
                if term in skills:
                    topics.append(term)
            elif textmatch.contains(corpus, term):
                topics.append(term)
        score += len(topics) * cfg.AFFINITY_TOPIC_HIT_WEIGHT
        evidence_score[family] = 1 - math.exp(-score / cfg.AFFINITY_SATURATION)
        detail[family] = {"titles": titles, "topics": topics, "evidence": round(evidence_score[family], 3)}

    affinity = {family: round(max(cfg.AFFINITY_FLOOR, min(1.0, evidence_score[family])), 3)
                for family in taxonomy.ROLE_FAMILIES}
    return affinity, detail


def semantic_text(resume: ParsedResume) -> str:
    """
    What the user *is* for embedding: their work, projects and skills.

    Stated targets used to be prepended here. Embedding "Target roles: AI
    Engineer" makes every posting with that title similar to the résumé
    whether or not the résumé supports it — the stated-preference bug in
    vector form.
    """
    parts = []
    for job in resume.experience:
        parts.append(f"{job.title}. " + " ".join(job.bullets))
    for project in resume.projects:
        parts.append(f"{project.name}. {project.description}")
    if resume.skills:
        parts.append("Skills: " + ", ".join(resume.skills))
    return "\n".join(parts)[:4000]
