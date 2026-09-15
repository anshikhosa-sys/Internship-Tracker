"""
Rule-based résumé extraction: the free, deterministic fallback.

Produces the same JSON shape as the model extraction (see schema.py), so the
rest of the pipeline cannot tell which one ran except by the `extractor` tag.

The approach is structural rather than linguistic:
  1. Split into sections by headings (markdown `#`, ALL-CAPS lines, or a known
     section name alone on a line).
  2. In experience and projects, an entry is a header block (1-3 lines holding
     organization, title, location, dates) followed by bullets.
  3. Dates come from resume/dates.py; titles are the header segment that
     contains a title noun ("engineer", "intern", ...).

It never extracts a name, email, or phone number.
"""

from __future__ import annotations

import re

from jobrank import textmatch
from jobrank.config import taxonomy
from jobrank.resume import dates


class ResumeParseError(ValueError):
    pass


_BULLET = re.compile(r"^\s*(?:[-*•◦▪●‣∙]|•|\d+[.)])\s+")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_SEPARATORS = re.compile(r"\s+[—–|·@]\s+|\s+-\s+|\t+|\s{3,}|\s+at\s+")
_LOCATION = re.compile(
    r"^(?:remote|hybrid|on-?site|[A-Z][A-Za-z .'-]+,\s*(?:[A-Z]{2}|[A-Z][a-z]+)(?:,\s*[A-Z][A-Za-z]+)?)$"
)
_LABEL = re.compile(r"^\s*[A-Za-z][A-Za-z &/+-]{1,30}:\s*")


def _clean(text: str) -> str:
    text = re.sub(r"\*\*|__|`", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return text.strip()


def _section_for(line: str) -> tuple[str | None, str]:
    """Return (section, remainder) if the line is a heading; remainder is inline content."""
    raw = _clean(line)
    md = _MD_HEADING.match(raw)
    text = md.group(2).strip() if md else raw
    key = textmatch.squash(text.rstrip(":"))
    for section, aliases in taxonomy.RESUME_SECTIONS.items():
        if key in aliases:
            if md or text.isupper() or len(key.split()) <= 4:
                return section, ""
    # "Technical Skills: Python, Go" — heading and content on one line.
    label = _LABEL.match(raw)
    if label:
        lkey = textmatch.squash(label.group(0).rstrip(": "))
        if lkey in taxonomy.RESUME_SECTIONS["skills"]:
            return "skills", raw[label.end():]
    if md and len(md.group(1)) == 1:
        return "header", ""
    if md and len(md.group(1)) == 2:
        return "other", ""
    return None, ""


def split_sections(text: str) -> tuple[dict[str, list[str]], list[str]]:
    sections: dict[str, list[str]] = {}
    headings: list[str] = []
    current = "header"
    in_comment = False
    for line in text.splitlines():
        if "<!--" in line:
            in_comment = True
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        section, remainder = _section_for(line) if line.strip() else (None, "")
        if section:
            current = section
            headings.append(_clean(line).lstrip("#").strip())
            if remainder:
                sections.setdefault(current, []).append(remainder)
            continue
        sections.setdefault(current, []).append(line.rstrip())
    return sections, headings


# ---------------------------------------------------------------------------
# Entries (experience, projects)
# ---------------------------------------------------------------------------

def _entries(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    last_was_bullet = False

    def start() -> dict:
        entry = {"header": [], "bullets": []}
        entries.append(entry)
        return entry

    for index, raw in enumerate(lines):
        if not raw.strip():
            last_was_bullet = False
            continue
        line = _clean(raw)
        md = _MD_HEADING.match(line)
        if md:
            current = start()
            current["header"].append(md.group(2).strip())
            last_was_bullet = False
            continue

        bullet = _BULLET.match(raw)
        if bullet:
            if current is None:
                current = start()
            current["bullets"].append(_clean(raw[bullet.end():]))
            last_was_bullet = True
            continue

        if current is not None and current["bullets"] and last_was_bullet and _is_continuation(raw, lines, index):
            current["bullets"][-1] += " " + line
            continue

        if current is None or current["bullets"] or len(current["header"]) >= 3:
            current = start()
        current["header"].append(line)
        last_was_bullet = False
    return [e for e in entries if e["header"] or e["bullets"]]


def _is_continuation(raw: str, lines: list[str], index: int) -> bool:
    stripped = raw.strip()
    if raw[:1] in (" ", "\t"):
        return True
    if dates.find_range(stripped) and dates.find_range(stripped).start:
        return False
    nxt = next((n for n in lines[index + 1:index + 2] if n.strip()), "")
    if nxt and dates.find_range(nxt) and not _BULLET.match(nxt):
        return False
    return stripped[:1].islower() or stripped[:1] in "(&,;" or stripped[:1].isdigit()


def _segments(header_lines: list[str]) -> tuple[list[str], dates.DateRange | None]:
    date_range = None
    segments: list[str] = []
    for line in header_lines:
        found = dates.find_range(line)
        if found and found.start and date_range is None:
            date_range = found
        for part in _SEPARATORS.split(dates.strip_dates(line)):
            part = part.strip(" ,|·•-–—()")
            if part and not _LOCATION.match(part):
                segments.append(part)
    return segments, date_range


def _has_title_noun(text: str) -> bool:
    return bool(textmatch.find_all(text, taxonomy.TITLE_HEAD_NOUNS))


def _experience(lines: list[str]) -> list[dict]:
    out = []
    for entry in _entries(lines):
        segments, span = _segments(entry["header"])
        if not segments and not entry["bullets"]:
            continue
        title_index = next((i for i, s in enumerate(segments) if _has_title_noun(s)), None)
        if title_index is None:
            title_index = 1 if len(segments) > 1 else 0
        title = segments[title_index] if segments else ""
        organization = next((s for i, s in enumerate(segments) if i != title_index), "")
        out.append({
            "title": title,
            "organization": organization,
            "start": span.start if span else None,
            "end": (span.end if span else None),
            "current": bool(span and span.current),
            "bullets": entry["bullets"],
        })
    return out


def _projects(lines: list[str]) -> list[dict]:
    out = []
    for entry in _entries(lines):
        segments, span = _segments(entry["header"])
        name = segments[0] if segments else (entry["bullets"][0][:60] if entry["bullets"] else "")
        technologies: list[str] = []
        for segment in segments[1:]:
            parts = [s.strip() for s in segment.split(",") if s.strip()]
            if len(parts) > 1 or _mentions_skill(segment):
                technologies += parts
            else:
                name = f"{name} — {segment}"
        out.append({
            "name": name,
            "description": " ".join(entry["bullets"]),
            "technologies": technologies,
            "start": span.start if span else None,
            "end": span.end if span else None,
        })
    return out


def _mentions_skill(text: str) -> bool:
    return any(
        textmatch.find_all(text, spec.get("aliases", []))
        or textmatch.find_all(text, spec.get("case_sensitive", []), case_sensitive=True)
        for spec in taxonomy.SKILLS.values()
    )


def _education(lines: list[str]) -> list[dict]:
    blocks: list[list[str]] = []
    for raw in lines:
        line = _clean(raw).lstrip("-*• ").strip()
        if not line:
            continue
        if textmatch.find_all(line, taxonomy.INSTITUTION_MARKERS) or not blocks:
            blocks.append([line])
        else:
            blocks[-1].append(line)
    out = []
    for block in blocks:
        text = " ".join(block)
        institution = ""
        for part in _SEPARATORS.split(dates.strip_dates(block[0])):
            if textmatch.find_all(part, taxonomy.INSTITUTION_MARKERS):
                institution = part.strip(" ,")
                break
        degree = ""
        for part in _SEPARATORS.split(dates.strip_dates(text)):
            if degree_level(part) != "none":
                degree = re.sub(r"\b(expected|anticipated)\b.*$", "", part, flags=re.I).strip(" ,")
                break
        points = dates.all_points(text)
        out.append({
            "institution": institution,
            "degree": degree,
            "field": degree_field(degree),
            "graduation": max(points) if points else None,
            "expected": bool(re.search(r"\b(expected|anticipated|candidate)\b", text, re.I)),
        })
    return [e for e in out if e["institution"] or e["degree"]]


def degree_level(text: str) -> str:
    for level in ("phd", "master", "bachelor", "associate"):
        if textmatch.find_all(text, taxonomy.DEGREE_PATTERNS[level]):
            return level
    return "none"


def degree_field(degree: str) -> str:
    """"B.S. Computer Science" / "Bachelor of Science in Computer Science" -> "Computer Science"."""
    m = re.search(r"\bin\s+(.+)$", degree or "")
    if m:
        return m.group(1).strip(" ,.")
    patterns = sorted((p for ps in taxonomy.DEGREE_PATTERNS.values() for p in ps), key=len, reverse=True)
    for p in patterns:
        match = textmatch.pattern_for(p).search(degree or "")
        if match:
            rest = re.sub(r"^\.?\s*(?:of\s+(?:science|arts|engineering)\s*)?", "", degree[match.end():], flags=re.I)
            return rest.strip(" ,.")
    return ""


def _skills(lines: list[str]) -> list[str]:
    items: list[str] = []
    for raw in lines:
        line = _clean(raw).lstrip("-*• ")
        line = _LABEL.sub("", line)
        for part in re.split(r"[,;|•·]", line):
            part = re.sub(r"\([^)]*\)", "", part).strip(" .")
            if part and len(part) <= 40:
                items.append(part)
    seen, unique = set(), []
    for item in items:
        if item.lower() not in seen:
            seen.add(item.lower())
            unique.append(item)
    return unique


def extract(text: str) -> dict:
    sections, headings = split_sections(text)
    result = {
        "education": _education(sections.get("education", [])),
        "experience": _experience(sections.get("experience", [])),
        "projects": _projects(sections.get("projects", [])),
        "skills": _skills(sections.get("skills", [])),
    }
    if not (result["experience"] or result["projects"] or result["skills"]):
        found = ", ".join(headings) or "none"
        raise ResumeParseError(
            "No experience, projects, or skills could be found, so no profile was created. "
            f"Headings detected: {found}. Give each section a heading such as 'Experience', "
            "'Projects', 'Skills'."
        )
    return result
