"""
Source: the SimplifyJobs Summer 2027 internships README.

This is the messiest file in the project, and that's on purpose — all the
"this specific website is weird" knowledge is quarantined here so nothing else
has to deal with it.

WHAT THE PAGE ACTUALLY LOOKS LIKE
---------------------------------
The README is markdown, but the listings themselves are HTML tables embedded
inside it. Structure:

    ## 💻 Software Engineering Internship Roles     <- markdown heading
    <table>
      <thead><tr><th>Company</th><th>Role</th>...</tr></thead>
      <tbody>
        <tr>
          <td><strong><a href="...">Zipline</a></strong></td>
          <td>Software Engineer Intern - Summer 2027</td>
          <td>South SF</td>
          <td><div><a href="APPLY_URL">..</a>
                   <a href="SIMPLIFY_URL">..</a></div></td>
          <td>0d</td>
        </tr>

FOUR THINGS THAT BREAK NAIVE PARSERS (all handled below):

  1. It's HTML, not markdown tables. The usual `line.split("|")` trick that
     works on most GitHub job lists returns nothing here.

  2. Continuation rows. When one company posts several roles, rows after the
     first use "↳" as the company name, meaning "same as above". Parsed
     literally you'd get hundreds of postings from a company called "↳".
     We carry the last real company name forward.

  3. There's no date column. Only a relative "Age" like "18d" or "1mo". We
     derive an approximate date from it, but the NEW flag deliberately uses
     our own first_seen timestamp instead (see storage.py) because that's
     exact and doesn't drift as the page updates.

  4. Emoji carry meaning. 🔥 = FAANG+ company, 🎓 = advanced degree required.
     We read them into real boolean fields rather than leaving them in the
     role title.

     NOTE: the repo's legend also documents 🛂 (no sponsorship) and 🇺🇸 (U.S.
     citizenship required), but as of this writing it does not actually mark
     any individual row with them — they appear exactly once each, in the
     legend itself. So Posting.no_sponsorship and .citizenship_required are
     parsed but always come back False from THIS source. They're kept on the
     Posting because other sources may populate them, and because if this repo
     starts using them, this parser will pick them up with no changes.
"""

import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import requests

from jobrank import config
from .base import Posting, Source


# =============================================================================
# Step 1: a tiny HTML table reader
# =============================================================================

class _TableParser(HTMLParser):
    """
    Reads an HTML table and produces a list of rows, where each row is a list
    of cells, and each cell is a dict: {"text": str, "links": [str, ...]}.

    We keep links separately because we need the href from the Apply column,
    and the visible text there is just an image (there IS no visible text).

    Why hand-roll this instead of using BeautifulSoup? Two reasons: it keeps
    the project to two dependencies, and html.parser ships with Python. For a
    table this regular, it's about 40 lines.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None      # cells of the row currently being read
        self._cell = None     # the cell currently being read
        self._in_summary = 0  # depth counter for <summary> (see below)

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = {"text": [], "links": []}
        elif tag == "summary":
            # Cells with several locations are collapsed like this:
            #   <details><summary>6 locations</summary>
            #   Austin, TX<br>...</details>
            # The summary is just a label ("6 locations"), and gluing it onto
            # the front produces "6 locationsAustin, TX". We have the real list
            # right after it, so we skip the summary text entirely.
            self._in_summary += 1
        elif tag == "details" and self._cell is not None:
            # Make sure the collapsed content doesn't run into earlier text.
            self._cell["text"].append(" ")
        elif tag == "a" and self._cell is not None:
            # Record the href so we can pull out apply / Simplify URLs later.
            for key, value in attrs:
                if key == "href" and value:
                    self._cell["links"].append(value)
        elif tag == "br" and self._cell is not None:
            # Multiple locations are separated by <br>. Turn that into a
            # readable separator instead of gluing words together.
            self._cell["text"].append(" | ")

    def handle_endtag(self, tag):
        if tag == "summary":
            self._in_summary = max(0, self._in_summary - 1)
        elif tag in ("td", "th") and self._cell is not None:
            if self._row is not None:
                self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        # Ignore text inside <summary> — see handle_starttag for why.
        if self._cell is not None and not self._in_summary:
            self._cell["text"].append(data)


def _cell_text(cell) -> str:
    """Join a cell's text fragments and tidy up the whitespace."""
    joined = "".join(cell["text"])
    joined = re.sub(r"\s+", " ", joined)          # collapse runs of whitespace
    # Normalize the separator we inserted for <br>, then drop any stray
    # leading or trailing one.
    joined = re.sub(r"\s*\|\s*", " | ", joined)
    return joined.strip(" |")


# =============================================================================
# Step 2: turn the relative "Age" column into an approximate date
# =============================================================================

def _age_to_date(age_text: str, now: datetime):
    """
    Convert "0d" / "18d" / "1mo" / "3h" into an approximate ISO date.

    This is genuinely approximate — "1mo" could be anywhere from 30 to 59 days
    old, and we treat a month as 30 days. That's fine for display ("posted
    about a month ago") but it is NOT what we use to decide what's new. See
    the module docstring, point 3.

    Returns None if the age can't be understood, rather than guessing.
    """
    if not age_text:
        return None

    match = re.match(r"(\d+)\s*(h|d|w|mo|y)", age_text.strip().lower())
    if not match:
        return None

    amount, unit = int(match.group(1)), match.group(2)
    days = {
        "h": amount / 24,
        "d": amount,
        "w": amount * 7,
        "mo": amount * 30,   # approximation, see above
        "y": amount * 365,
    }[unit]

    return (now - timedelta(days=days)).date().isoformat()


# =============================================================================
# Step 3: the source itself
# =============================================================================

class SimplifyReadmeSource(Source):
    """Fetches and parses the Summer 2027 internships README."""

    name = config.SOURCE_NAME

    def __init__(self, url: str = None, categories: list = None):
        # Defaults come from config.py, but can be overridden — which is what
        # makes this class reusable for the New-Grad repo later, since that
        # repo uses the same table format at a different URL.
        self.url = url or config.SOURCE_URL
        self.categories = categories or config.INGEST_CATEGORIES

    # -- fetching ------------------------------------------------------------

    def _download(self) -> str:
        """Download the README. Raises on network failure or a bad status."""
        response = requests.get(
            self.url,
            timeout=30,
            # GitHub is friendlier to requests that identify themselves.
            headers={"User-Agent": "personal-internship-finder"},
        )
        response.raise_for_status()   # turns a 404/500 into a clear exception
        return response.text

    # -- parsing -------------------------------------------------------------

    def _split_sections(self, markdown: str) -> list:
        """
        Split the README into (category_name, html_chunk) pairs, one per
        '## ' heading. Only sections matching config.INGEST_CATEGORIES are
        returned.
        """
        sections = []

        # Split on level-2 markdown headings. re.M makes ^ match line starts.
        chunks = re.split(r"^##\s+", markdown, flags=re.M)

        for chunk in chunks[1:]:          # [0] is everything before the first
            heading, _, body = chunk.partition("\n")     # heading — intro text

            # "💻 Software Engineering Internship Roles"
            #   -> "Software Engineering"
            clean = re.sub(r"\s*Internship Roles\s*$", "", heading).strip()
            # Strip any leading emoji / non-letter characters.
            clean = re.sub(r"^[^A-Za-z]+", "", clean).strip()

            # Keep only the sections we care about (case-insensitive match).
            wanted = any(
                want.lower() in clean.lower() for want in self.categories
            )
            if wanted:
                sections.append((clean, body))

        return sections

    def _parse_section(self, category: str, html: str, now: datetime) -> list:
        """Turn one section's HTML table into Posting objects."""
        parser = _TableParser()
        parser.feed(html)

        postings = []
        last_company = ""     # for resolving "↳" continuation rows

        for row in parser.rows:
            # Expect: Company | Role | Location | Application | Age
            # Header rows and any malformed rows get skipped rather than
            # crashing the run.
            if len(row) < 5:
                continue

            (company_cell, role_cell, location_cell,
             apply_cell, age_cell) = row[:5]

            company = _cell_text(company_cell)
            role = _cell_text(role_cell)

            # Skip the header row (it uses <th>, but be defensive).
            if company.lower() == "company" and role.lower() == "role":
                continue

            # --- Gotcha #2: continuation rows ---------------------------
            # "↳" means "same company as the row above".
            if company.startswith("↳") or company in ("↳", ""):
                company = last_company
            else:
                # Strip the 🔥 FAANG marker off the front before storing.
                company = re.sub(r"^[^A-Za-z0-9(]+", "", company).strip()
                last_company = company

            if not company or not role:
                continue

            # --- Gotcha #4: read the emoji flags ------------------------
            # Check the whole row's text, since which cell carries a given
            # marker isn't guaranteed.
            row_text = " ".join(_cell_text(c) for c in row)

            # --- pull the two URLs out of the Application cell ----------
            # The first link is the employer's own apply page; the second is
            # Simplify's posting page (which gives us a stable ID).
            apply_url, simplify_url = "", ""
            for link in apply_cell["links"]:
                if "simplify.jobs/p/" in link:
                    simplify_url = link
                elif not apply_url:
                    apply_url = link

            # If the only link was the Simplify one, use it to apply too.
            if not apply_url:
                apply_url = simplify_url

            age_text = _cell_text(age_cell)

            postings.append(
                Posting(
                    company=company,
                    # Clean trailing emoji off the role title for display.
                    role=re.sub(r"\s*[🎓🛂🇺🇸🔒🔥]+\s*$", "", role).strip(),
                    category=category,
                    location=_cell_text(location_cell),
                    apply_url=apply_url,
                    simplify_url=simplify_url,
                    age_text=age_text,
                    date_posted=_age_to_date(age_text, now),
                    source=self.name,
                    is_faang="🔥" in row_text,
                    needs_advanced_degree="🎓" in row_text,
                    no_sponsorship="🛂" in row_text,
                    citizenship_required="🇺🇸" in row_text,
                )
            )

        return postings

    # -- the public entry point ---------------------------------------------

    def fetch(self) -> list:
        """Download, parse, and return every posting in the wanted sections."""
        now = datetime.now(timezone.utc)
        markdown = self._download()

        postings = []
        for category, html in self._split_sections(markdown):
            postings.extend(self._parse_section(category, html, now))

        return postings
