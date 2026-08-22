"""
The contract every source must satisfy.

This file defines two things:

  Posting  — the shape of one job listing. Every source must produce these,
             no matter what the underlying website looks like.
  Source   — the interface a source implements: "call .fetch(), get back a
             list of Posting objects".

WHY BOTHER WITH THIS?
    Without it, the scorer would need to know that the Summer 2027 repo calls
    a field "Age" while some other site calls it "posted_date". By forcing
    every source to translate into the SAME Posting shape, the rest of the app
    only has to understand one format. This is the single most useful habit in
    a project that's meant to grow: put the messy, site-specific translation
    at the edges, keep the middle clean.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional


# Noise words the aggregators sprinkle through titles that carry no identity.
_NOISE = re.compile(
    r"\b("
    r"summer|fall|winter|spring|intern|internship|co-?op|"
    r"20\d\d|program|opportunity|student|"
    r"remote|hybrid|onsite|us|usa|"
    r"early career|university|new grad"
    r")\b",
    re.IGNORECASE,
)
_REQ = re.compile(r"\b[a-z]{0,3}\d{4,}\b", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """
    Reduce a company or role to a comparable core.

    Used for BOTH deduplication and posting identity, so the two can never
    disagree about whether two records are the same job.
    """
    text = (text or "").lower()
    text = _REQ.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    text = _NOISE.sub(" ", text)
    text = _SPACE.sub(" ", text)
    return text.strip()


@dataclass
class Posting:
    """One internship listing, normalized into a consistent shape."""

    # --- Core fields, required by the spec ---
    company: str
    role: str
    category: str            # which section of the source it came from
    location: str
    apply_url: str           # the actual "Apply" link
    date_posted: Optional[str] = None   # ISO date string, may be approximate

    # --- Provenance ---
    source: str = ""         # which source produced this posting
    simplify_url: str = ""   # Simplify's own posting page, when available
    age_text: str = ""       # the raw "18d" / "Aug 21" string, for display
    salary: str = ""         # only some sources publish this

    # Every list this job appeared in, filled in by dedupe.py. Appearing in
    # several is mild evidence the posting is real and still current.
    sources: list = field(default_factory=list)

    # --- Flags the source repo marks with emoji ---
    is_faang: bool = False               # 🔥
    needs_advanced_degree: bool = False  # 🎓 Master's / PhD / MBA required
    no_sponsorship: bool = False         # 🛂 does NOT offer sponsorship
    citizenship_required: bool = False   # 🇺🇸 requires U.S. citizenship

    # --- Filled in later by scorer.py, not by the source ---
    # Three separate numbers rather than one, because they answer different
    # questions: do you want it, would they take you, and how fast is it
    # going stale. Blending them into a single stored score would hide the
    # cases that matter most.
    # Three separate numbers, not one. They answer different questions —
    # do you want it, would they take you, and is it still open — and
    # blending them would hide the cases that matter most.
    preference: float = 0.0
    preference_reasons: list = field(default_factory=list)
    candidacy_score: float = 0.0
    candidacy_reasons: list = field(default_factory=list)
    role_family: str = ""     # matched ROLE_FAMILIES entry; drives letters
    company_tier: str = ""    # big / mid / niche — sets the freshness curve
    fit_score: int = 0        # the three multiplied, 0-100

    @property
    def id(self) -> str:
        """
        A stable unique ID for this posting.

        THIS IS THE MOST SAFETY-CRITICAL FUNCTION IN THE PROJECT. The ID is
        what your "applied" mark is filed under. If it changes between runs,
        the mark is orphaned and the posting looks brand new — silent data
        loss, with nothing to alert you.

        WHAT AN EARLIER VERSION GOT WRONG
        ---------------------------------
        It preferred Simplify's UUID and otherwise hashed
        source|company|role|location. Both halves were fragile once several
        sources were merged:

          - The SOURCE was part of the hash. When a job dropped off Simplify
            but another list still carried it, the merged record's primary
            source changed and the hash changed with it.
          - The LOCATION was part of the hash, so a company editing "NYC" to
            "New York, NY" minted a new ID.
          - A UUID-based ID and a hash-based ID for the same job never
            matched, so a posting appearing in a different list first would
            change identity.

        WHAT IT DOES NOW
        ----------------
        Hash the normalized company and role, and nothing else. Same rule the
        deduplicator uses, so identity and merging can never disagree. It
        survives a source dropping the posting, a location edit, a
        requisition-number change, and "Summer 2027" being added to a title.
        """
        raw = f"{normalize(self.company)}|{normalize(self.role)}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return f"job:{digest}"


class Source:
    """
    Base class for a posting source.

    A subclass only needs to implement fetch(). The `name` attribute is stored
    on each posting so the dashboard can show where a listing came from once
    you have more than one source.
    """

    name: str = "unnamed-source"

    def fetch(self) -> list:
        """Return a list of Posting objects. Subclasses must override this."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement fetch()"
        )
