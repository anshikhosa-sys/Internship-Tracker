"""
Source: the DereC4 internships-and-newgrad list.

    https://github.com/DereC4/internships-and-newgrad

Broad coverage — measured at 1,155 unique rows when it was added, 1,039 of
them company+role combinations no other source we read was carrying. That
is the single largest addition to the pool of any source here.

TWO THINGS TO KNOW
------------------
  - It lists NEW GRAD roles alongside internships. Those are not filtered
    out here: scorer.is_internship() is the one place that decision lives,
    and duplicating it in a source file is how the two drift apart. They
    arrive and are dropped by the gate.
  - The role cell carries the apply link as a markdown link, so the link
    comes from the ROLE column rather than a column of its own.

FORMAT
------
    | Company | Role | Location | Age |
    | Analog Devices | [Algorithm Dev Intern](https://…) | Wilmington, MA | 3d |
"""

from datetime import datetime, timezone

import requests

from . import markdown_table as md
from .base import Posting, Source


class DereC4ReadmeSource(Source):
    """Fetches and parses the DereC4 internships-and-newgrad list."""

    name = "DereC4-Internships"

    URL = (
        "https://raw.githubusercontent.com/"
        "DereC4/internships-and-newgrad/main/README.md"
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
            # Company | Role | Location | Age
            if len(row) < 4:
                continue

            company_cell, role_cell, loc_cell, age_cell = row[:4]

            company, last_company = md.resolve_company(
                company_cell["text"], last_company
            )
            role = role_cell["text"]
            if not company or not role:
                continue

            # The apply link lives inside the role cell here, not in a
            # column of its own.
            apply_url = role_cell["links"][0] if role_cell["links"] else ""
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
                    date_posted=md.parse_relative_age(
                        age_cell["text"], today
                    ),
                    age_text=age_cell["text"],
                    source=self.name,
                    is_faang="🔥" in row_text,
                    needs_advanced_degree="🎓" in row_text
                    or "PhD" in role or "Masters" in role,
                    no_sponsorship="🛂" in row_text,
                    citizenship_required="🇺🇸" in row_text,
                )
            )

        return postings
