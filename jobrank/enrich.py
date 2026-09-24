"""
Optional posting enrichment: structured facts a title alone does not state.

    required_years      minimum years of experience asked for (null if unstated)
    degree_required     "none" | "bachelor" | "master" | "phd" | null
    entry_level         whether the role is genuinely open to new graduates
    tech_stack          canonical skills the posting names
    remote_status       "remote" | "hybrid" | "onsite" | null
    title_contradiction a sentence when the text contradicts the title, else null
                        ("Junior" title, "5+ years required" body)

Runs through `jobrank.llm.structured`: a local model when one is available,
rules otherwise, cached by a hash of the posting text so no posting is ever
analyzed twice. The scorer uses these facts when present and is fully
functional without them.

HONEST LIMITATION: most sources publish titles, not descriptions. On a
title-only posting enrichment can state little beyond tech named in the title
and remote status; it earns its keep when a description exists (a pasted job
description, or a source that publishes one).
"""

from __future__ import annotations

import logging
import re

from jobrank import llm, postings, storage
from jobrank.config import taxonomy
from jobrank.llm.cache import content_hash
from jobrank.log import event, get_logger
from jobrank.resume.parser import canonical_skills
from jobrank.roles import title_seniority

# The most experience any real posting asks for. Anything above this is being
# misread — almost always a company describing its own history.
_MAX_REQUIRED_YEARS = 40


log = get_logger(__name__)

ENRICH_SCHEMA = {
    "type": "object",
    "required": ["required_years", "degree_required", "entry_level", "tech_stack", "remote_status",
                 "title_contradiction"],
    "properties": {
        "required_years": {"type": ["integer", "null"], "minimum": 0, "maximum": _MAX_REQUIRED_YEARS},
        "degree_required": {"type": ["string", "null"], "enum": ["none", "bachelor", "master", "phd", None]},
        "entry_level": {"type": ["boolean", "null"]},
        "tech_stack": {"type": "array", "maxItems": 60, "items": {"type": "string"}},
        "remote_status": {"type": ["string", "null"], "enum": ["remote", "hybrid", "onsite", None]},
        "title_contradiction": {"type": ["string", "null"]},
    },
}

SYSTEM_PROMPT = """You read job postings and extract facts that are explicitly stated.
- required_years: the minimum years of experience REQUIRED (not preferred). null if not stated.
- degree_required: the minimum degree REQUIRED. null if not stated.
- entry_level: true only if new graduates or students can clearly apply; null if unclear.
- tech_stack: technologies named in the posting, as written.
- remote_status: only if stated.
- title_contradiction: one sentence if the body contradicts the title's seniority or role, else null.
Never guess. Unstated means null."""

_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)\b(?:\s+of)?(?:\s+\w+){0,3}\s+experience", re.I)
_PREFERRED = re.compile(r"\b(preferred|nice to have|bonus|plus)\b", re.I)

# An employer describing ITS OWN age, not a requirement of the candidate:
# "a leading global asset manager with over 65 years of experience helping...".
# That one line made a posting claim it wanted 65 years, which failed schema
# validation and took the whole refresh down with it. The quieter version of
# the same bug is a boast inside the schema's range — "with 20 years of
# experience" — which would silently make an internship look unreachable.
_COMPANY_TENURE = re.compile(
    r"\b(?:with|for|over|boasts?|celebrating|bringing)\s+(?:over|nearly|more than|almost)?\s*$", re.I)
_TENURE_OBJECT = re.compile(
    r"^\s*(?:helping|serving|delivering|providing|supporting|working with|in business|as a)\b", re.I)
_DEGREE = [
    ("phd", re.compile(r"\b(ph\.?d|doctorate)\b.{0,40}\brequired\b|\brequired\b.{0,40}\b(ph\.?d|doctorate)\b", re.I)),
    ("master", re.compile(r"\b(master'?s|m\.s\.|ms degree)\b.{0,40}\brequired\b|\brequired\b.{0,40}\b(master'?s|m\.s\.)", re.I)),
    ("bachelor", re.compile(r"\b(bachelor'?s|b\.s\.|bs degree)\b.{0,40}\b(required|in)\b", re.I)),
]
_ENTRY = re.compile(r"\b(new grad(uate)?s?|recent graduates?|entry[- ]level|students?|no experience required)\b", re.I)


def _prompt(text: str) -> str:
    return f"Extract facts from this job posting.\n\n<posting>\n{text}\n</posting>"


def rules_extract(text: str) -> dict:
    title = ""
    for line in text.splitlines():
        if line.startswith("Title:"):
            title = line[len("Title:"):].strip()
            break
    body = text

    years = None
    for match in _YEARS.finditer(body):
        window = body[max(0, match.start() - 60): match.end() + 60]
        if _PREFERRED.search(window):
            continue
        # Skip the employer talking about itself rather than about the
        # candidate, from either side of the phrase.
        if _COMPANY_TENURE.search(body[max(0, match.start() - 40):match.start()]):
            continue
        if _TENURE_OBJECT.match(body[match.end():match.end() + 40]):
            continue
        value = int(match.group(1))
        if value > _MAX_REQUIRED_YEARS:
            continue          # implausible as a requirement; the schema would reject it anyway
        years = value if years is None else min(years, value)

    degree = None
    for level, pattern in _DEGREE:
        if pattern.search(body):
            degree = level
            break

    level = title_seniority(title)
    entry = None
    if level in ("intern", "entry"):
        entry = True
    elif level in ("senior", "staff"):
        entry = False
    if years is not None:
        entry = years <= 1
    elif _ENTRY.search(body.replace(title, "")):
        entry = True

    contradiction = None
    if level in ("intern", "entry") and years is not None and years >= 3:
        contradiction = f"Title reads {level}-level but the posting requires {years}+ years of experience."
    elif level in ("senior", "staff") and _ENTRY.search(body.replace(title, "")):
        contradiction = "Title reads senior but the posting invites new graduates."

    mode = postings.work_mode({"location": body, "role": ""})
    return {
        "required_years": years,
        "degree_required": degree,
        "entry_level": entry,
        "tech_stack": canonical_skills(body),
        "remote_status": mode if mode in ("remote", "hybrid") else None,
        "title_contradiction": contradiction,
    }


def normalize(data: dict) -> dict:
    """Map a model's free-text tech stack onto taxonomy skills; keep everything else."""
    out = dict(data)
    found: set[str] = set()
    for item in data.get("tech_stack") or []:
        # Rules output is already canonical; model output is free text. A
        # canonical name like "go" must not be re-matched, because its alias
        # is case-sensitive ("Go") and would be silently dropped.
        if item in taxonomy.SKILLS:
            found.add(item)
        else:
            found.update(canonical_skills(item))
    out["tech_stack"] = sorted(found)
    return out


def enrich_one(posting, conn=None) -> tuple[dict, str, str]:
    """(data, extractor, text_hash) for one posting."""
    text = postings.text_for_enrichment(posting)
    extraction = llm.structured("posting_enrich", text, ENRICH_SCHEMA, SYSTEM_PROMPT, _prompt, rules_extract,
                                conn=conn)
    return normalize(extraction.data), extraction.extractor, content_hash(text)


def enrich_all(conn, rows: list, limit: int | None = None) -> dict:
    """
    Enrich every posting whose text changed since it was last enriched.
    Returns counts. `limit` bounds model calls per run; rule-based work is
    cheap and unbounded.
    """
    existing = storage.get_enrichments(conn)
    done = skipped = failed = 0
    llm_conn = llm.cache.connect()
    try:
        for posting in rows:
            posting_id = postings.field(posting, "id")
            text_hash = content_hash(postings.text_for_enrichment(posting))
            if existing.get(posting_id, {}).get("text_hash") == text_hash:
                skipped += 1
                continue
            if limit is not None and done >= limit:
                break
            try:
                data, extractor, text_hash = enrich_one(posting, conn=llm_conn)
            except Exception as exc:  # noqa: BLE001
                # One posting must never take the run down. Enrichment is an
                # optional layer (invariant 9): a posting that cannot be
                # analysed keeps no enrichment and scores on its title alone.
                # A single description reading "with over 65 years of
                # experience" — the employer's age, not a requirement — failed
                # schema validation and aborted every refresh after it.
                failed += 1
                event(log, "enrichment_failed", level=logging.WARNING,
                      posting_id=posting_id, error=f"{type(exc).__name__}: {exc}"[:200])
                continue
            storage.put_enrichment(conn, posting_id, text_hash, extractor, data)
            done += 1
        conn.commit()
    finally:
        llm_conn.close()
    event(log, "enrichment_run", enriched=done, unchanged=skipped, failed=failed)
    return {"enriched": done, "unchanged": skipped, "failed": failed}


def contradicts_title(enrichment: dict | None) -> bool:
    return bool(enrichment and enrichment.get("title_contradiction"))

