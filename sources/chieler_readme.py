"""
Source: the Chieler Summer 2027 SWE internship list.

    https://github.com/Chieler/Summer-2027-SWE-Internships

WHY THIS ONE IS WORTH HAVING
----------------------------
It publishes an EXACT ISO date per row. Every other source we read gives a
relative age ("3d", "1mo") that has to be turned back into a date, which is
approximate by construction and drifts between refreshes. dedupe.py prefers
an absolute date over a derived one, so adding this source improves the
dates on postings we already had from elsewhere.

Measured when it was added: 1,049 unique rows, 627 of them company+role
combinations no other source we read was carrying.

FORMAT
------
    | Company | Role | Posted | Applied | Link |
    | TikTok | AI Product Manager Intern | 2026-08-22 | — | [Apply](https://…) |

The "Applied" column is the list maintainer's own tracking, not ours, and
is ignored.
"""

from datetime import datetime, timezone

import requests

from . import markdown_table as md
from .base import Posting, Source


class ChielerReadmeSource(Source):
    """Fetches and parses the Chieler Summer 2027 SWE list."""

    name = "Chieler-Summer2027"

    URL = (
        "https://raw.githubusercontent.com/"
        "Chieler/Summer-2027-SWE-Internships/main/README.md"
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
            # Company | Role | Posted | Applied | Link
            if len(row) < 5:
                continue

            company_cell, role_cell, date_cell = row[0], row[1], row[2]
            link_cell = row[4]

            company, last_company = md.resolve_company(
                company_cell["text"], last_company
            )
            role = role_cell["text"]
            if not company or not role:
                continue

            apply_url = link_cell["links"][0] if link_cell["links"] else ""
            if not apply_url:
                continue

            row_text = " ".join(cell["raw"] for cell in row)

            postings.append(
                Posting(
                    company=company,
                    role=role,
                    # This list is SWE-only and carries no category column.
                    # Saying so beats "Uncategorized", which the role-family
                    # gate treats as unclassifiable.
                    category="Software Engineering",
                    location="",
                    apply_url=apply_url,
                    # Exact, not derived — the reason this source is here.
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
