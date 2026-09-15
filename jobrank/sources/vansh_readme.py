"""
Source: the Vansh & Ouckah Summer 2027 internship list.

A second aggregator covering the same season as the Simplify list, with
meaningful overlap but also roles the other one misses. Running both and
deduplicating gives better coverage than either alone.

    https://github.com/vanshb03/Summer2027-Internships

FORMAT
------
Markdown pipe tables rather than HTML:

    | Company | Role | Location | Application/Link | Date Posted |
    | Vertiv | Product Management Intern | Westerville, OH | <a..> | Aug 21 |

ONE REAL ADVANTAGE OVER THE SIMPLIFY LIST
-----------------------------------------
The last column is an ABSOLUTE date ("Aug 21"), not a relative age ("18d").
That's strictly better: no approximation, and it doesn't drift between
refreshes. Where the same posting appears in both sources, the deduplicator
prefers this one's date.
"""

from datetime import datetime, timezone

import requests

from . import markdown_table as md
from .base import Posting, Source


class VanshReadmeSource(Source):
    """Fetches and parses the Vansh & Ouckah Summer 2027 list."""

    name = "Vansh-Summer2027"

    URL = (
        "https://raw.githubusercontent.com/"
        "vanshb03/Summer2027-Internships/dev/README.md"
    )

    def __init__(self, url: str = None):
        self.url = url or self.URL

    def _download(self) -> str:
        response = requests.get(
            self.url,
            timeout=30,
            headers={"User-Agent": "personal-internship-finder"},
        )
        response.raise_for_status()
        return response.text

    def fetch(self) -> list:
        today = datetime.now(timezone.utc).date()
        rows = md.parse_rows(self._download())

        postings = []
        last_company = ""

        for row in rows:
            # Company | Role | Location | Application/Link | Date Posted
            if len(row) < 5:
                continue

            company_cell, role_cell, location_cell, apply_cell, date_cell = \
                row[:5]

            company, last_company = md.resolve_company(
                company_cell["text"], last_company
            )
            role_text = role_cell["text"]

            if not company or not role_text:
                continue

            # This list marks closed roles inline rather than moving them to
            # a separate file, so they have to be filtered here.
            if "🔒" in role_cell["raw"] or "closed" in role_text.lower():
                continue

            row_text = " ".join(cell["raw"] for cell in row)
            apply_url = apply_cell["links"][0] if apply_cell["links"] else ""
            if not apply_url:
                continue    # nothing to apply to

            postings.append(
                Posting(
                    company=company,
                    role=_clean_role(role_text),
                    # This source doesn't split by category, so everything is
                    # unlabeled. The scorer treats an unknown category as
                    # neutral rather than penalizing it.
                    category="Uncategorized",
                    location=location_cell["text"],
                    apply_url=apply_url,
                    date_posted=md.parse_absolute_date(
                        date_cell["text"], today
                    ),
                    age_text=date_cell["text"],
                    source=self.name,
                    is_faang="🔥" in row_text,
                    needs_advanced_degree="🎓" in row_text,
                    no_sponsorship="🛂" in row_text,
                    citizenship_required="🇺🇸" in row_text,
                )
            )

        return postings


def _clean_role(text: str) -> str:
    """Strip trailing status emoji from a role title."""
    import re
    return re.sub(r"\s*[🎓🛂🇺🇸🔒🔥]+\s*$", "", text).strip()
