"""
Source: the sndsh404 Summer 2027 tech internship list.

    https://github.com/sndsh404/summer-2027-internships

Adds coverage the bigger aggregators miss — it's smaller and
hand-curated, which in practice means different companies rather than
fewer.

FORMAT
------
    | Company | Role | Location | Apply | Added |
    | Acme | SWE Intern | NYC | [apply](https://...) | 2026-07-21 |

Two differences from the other markdown sources, both handled here:

  - The apply link is MARKDOWN, not an HTML anchor. A parser looking only
    for href= would find nothing and drop every row.
  - The date is a full ISO date, which is the best form available: no
    approximation and no drift between refreshes.
"""

from datetime import datetime, timezone

import requests

from . import markdown_table as md
from .base import Posting, Source


class SndshReadmeSource(Source):
    """Fetches and parses the sndsh404 Summer 2027 list."""

    name = "Sndsh-Summer2027"

    URL = (
        "https://raw.githubusercontent.com/"
        "sndsh404/summer-2027-internships/main/README.md"
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
        postings = []
        last_company = ""

        for row in md.parse_rows(self._download()):
            # Company | Role | Location | Apply | Added
            if len(row) < 5:
                continue

            company_cell, role_cell, loc_cell, apply_cell, date_cell = row[:5]

            company, last_company = md.resolve_company(
                company_cell["text"], last_company
            )
            role = role_cell["text"]
            if not company or not role:
                continue

            apply_url = apply_cell["links"][0] if apply_cell["links"] else ""
            if not apply_url:
                continue

            row_text = " ".join(cell["raw"] for cell in row)

            postings.append(
                Posting(
                    company=company,
                    role=role,
                    category="Uncategorized",
                    location=loc_cell["text"],
                    apply_url=apply_url,
                    date_posted=md.parse_absolute_date(
                        date_cell["text"], today
                    ),
                    age_text=date_cell["text"],
                    source=self.name,
                    is_faang="🔥" in row_text,
                    needs_advanced_degree="🎓" in row_text
                    or "PhD" in role or "Masters" in role,
                    no_sponsorship="🛂" in row_text,
                    citizenship_required="🇺🇸" in row_text,
                )
            )

        return postings
