"""
Source: the SpeedyApply 2027 SWE college jobs list.

    https://github.com/speedyapply/2027-SWE-College-Jobs

WHY THIS ONE IS WORTH HAVING
----------------------------
It publishes a SALARY column, which neither of the other sources does. That's
genuinely new information — not a nicer presentation of something we already
had. It also organizes by company tier (FAANG+, Quant, Other), which is a
cleaner competitiveness signal than guessing from a company name.

FORMAT
------
Markdown tables, one per section:

    | Company | Position | Location | Salary | Posting | Age |
    | <a..><strong>Databricks</strong></a> | SWE Intern | ... | $72/hr |
    | <a..> | 0d |

Note the column order differs from the other sources — salary sits in the
middle — which is exactly why each source owns its own column mapping instead
of sharing one.
"""

import re
from datetime import datetime, timezone

import requests

from . import markdown_table as md
from .base import Posting, Source


# Section heading -> whether that section is a hyper-competitive tier.
# The list groups companies this way, which is better evidence than inferring
# from the company name.
COMPETITIVE_SECTIONS = ("faang", "quant")


class SpeedyApplyReadmeSource(Source):
    """Fetches and parses the SpeedyApply 2027 list."""

    name = "SpeedyApply-2027"

    URL = (
        "https://raw.githubusercontent.com/"
        "speedyapply/2027-SWE-College-Jobs/main/README.md"
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
        markdown = self._download()

        postings = []

        # Walk section by section so each row knows which tier it came from.
        for section_name, body in _split_sections(markdown):
            competitive = any(
                tag in section_name.lower() for tag in COMPETITIVE_SECTIONS
            )
            # Only the internship sections; this repo also lists new-grad
            # roles, which aren't relevant until graduation.
            if "new grad" in section_name.lower():
                continue

            last_company = ""
            for row in md.parse_rows(body):
                # Company | Position | Location | Salary | Posting | Age
                if len(row) < 6:
                    continue

                company_cell, role_cell, loc_cell, salary_cell, \
                    apply_cell, age_cell = row[:6]

                company, last_company = md.resolve_company(
                    company_cell["text"], last_company
                )
                role_text = role_cell["text"]
                if not company or not role_text:
                    continue

                apply_url = (
                    apply_cell["links"][0] if apply_cell["links"] else ""
                )
                if not apply_url:
                    continue

                postings.append(
                    Posting(
                        company=company,
                        role=role_text,
                        category="Uncategorized",
                        location=loc_cell["text"],
                        apply_url=apply_url,
                        date_posted=md.parse_relative_age(
                            age_cell["text"], today
                        ),
                        age_text=age_cell["text"],
                        source=self.name,
                        is_faang=competitive,
                        salary=_clean_salary(salary_cell["text"]),
                    )
                )

        return postings


def _split_sections(markdown: str) -> list:
    """Split on level-3 headings, returning (heading, body) pairs."""
    parts = re.split(r"^###\s+", markdown, flags=re.M)
    sections = []
    for part in parts[1:]:
        heading, _, body = part.partition("\n")
        sections.append((heading.strip(), body))
    return sections


def _clean_salary(text: str) -> str:
    """
    Normalize the salary cell, or return "" when there's nothing useful.

    The column is often a dash or an em-dash when unknown, which would
    otherwise show up in the dashboard as a meaningless value.
    """
    text = (text or "").strip()
    if text in ("", "-", "–", "—", "N/A", "n/a", "?"):
        return ""
    return text
