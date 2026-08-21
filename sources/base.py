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
from dataclasses import dataclass, field
from typing import Optional


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
    age_text: str = ""       # the raw "18d" / "1mo" string, kept for display

    # --- Flags the source repo marks with emoji ---
    is_faang: bool = False               # 🔥
    needs_advanced_degree: bool = False  # 🎓 Master's / PhD / MBA required
    no_sponsorship: bool = False         # 🛂 does NOT offer sponsorship
    citizenship_required: bool = False   # 🇺🇸 requires U.S. citizenship

    # --- Filled in later by scorer.py, not by the source ---
    fit_score: int = 0
    score_reasons: list = field(default_factory=list)

    @property
    def id(self) -> str:
        """
        A stable unique ID for this posting.

        Stability matters a lot here: it's how we know on the next run whether
        we've seen a posting before (which drives the NEW flag) and which
        posting your "applied" checkbox belongs to. If IDs changed between
        runs, everything would look new every time and your applied marks
        would detach.

        Simplify gives each posting a UUID in its URL, like
            https://simplify.jobs/p/6b73883a-ab74-444e-93ce-5fd790148177
        That UUID is the best ID available — it survives the company renaming
        the role or editing the location.

        If there's no Simplify URL, we fall back to hashing the fields that
        identify the posting. That fallback is slightly fragile (if the company
        edits the role title, it looks like a brand-new posting), which is
        exactly why we prefer the UUID when we can get it.
        """
        if self.simplify_url and "/p/" in self.simplify_url:
            uuid = self.simplify_url.split("/p/")[1].split("?")[0].strip("/")
            if uuid:
                return f"simplify:{uuid}"

        # Fallback: hash the identifying fields.
        raw = "|".join(
            [self.source, self.company, self.role, self.location]
        ).lower()
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return f"hash:{digest}"


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
