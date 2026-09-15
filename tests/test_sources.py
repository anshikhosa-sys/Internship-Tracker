"""
Parsing the raw aggregator pages into Posting objects.

Ported from tests/legacy_checks.py (test_parser, test_markdown_sources).
Nothing here touches scoring, so these needed no adaptation beyond imports —
the HTML/markdown table readers are unchanged by the v1 -> v2 rebuild.
"""

from datetime import date, datetime, timezone

from jobrank.sources.simplify_readme import _TableParser, _age_to_date, _cell_text


# =============================================================================
# Simplify's HTML <table> reader
# =============================================================================

def test_reads_html_table_rows():
    """
    The Simplify README embeds HTML tables inside markdown, not markdown pipe
    tables — a naive `line.split("|")` reader would return nothing here.
    """
    html = """
    <table><tbody>
    <tr>
      <td><strong><a href="https://simplify.jobs/c/Acme">Acme</a></strong></td>
      <td>Solutions Engineer Intern</td>
      <td>Austin, TX</td>
      <td><a href="https://acme.com/apply">A</a>
          <a href="https://simplify.jobs/p/abc-123">S</a></td>
      <td>3d</td>
    </tr>
    <tr>
      <td>↳</td>
      <td>Product Manager Intern</td>
      <td><details><summary><strong>2 locations</strong></summary>
          Seattle, WA<br>Remote</details></td>
      <td><a href="https://acme.com/apply2">A</a>
          <a href="https://simplify.jobs/p/def-456">S</a></td>
      <td>1mo</td>
    </tr>
    </tbody></table>
    """

    parser = _TableParser()
    parser.feed(html)
    rows = parser.rows

    assert len(rows) == 2, "reads both rows"
    assert _cell_text(rows[0][0]) == "Acme", "reads the company name"
    assert _cell_text(rows[1][0]) == "↳", "continuation marker reaches the row"

    location = _cell_text(rows[1][2])
    assert "locations" not in location, "drops the '2 locations' summary label"
    assert location == "Seattle, WA | Remote", (
        f"joins multiple locations cleanly (got {location!r})"
    )
    assert any("simplify.jobs/p/" in link for link in rows[0][3]["links"]), (
        "captures the Simplify posting URL"
    )


def test_age_to_date_parsing():
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    assert _age_to_date("0d", now) == "2026-08-21", "'0d' is today"
    assert _age_to_date("3d", now) == "2026-08-18", "'3d' is three days ago"
    assert _age_to_date("1mo", now) == "2026-07-22", "'1mo' is ~30 days ago"
    assert _age_to_date("", now) is None, "empty age returns None, not a guess"
    assert _age_to_date("???", now) is None, "unparseable age returns None"


# =============================================================================
# The markdown-table reader shared by the newer sources
# =============================================================================

def test_markdown_table_parsing():
    """The markdown table reader that Chieler/DereC4-style sources share."""
    from jobrank.sources import markdown_table as md

    header = "| Company | Role | Location | Link | Date Posted |"
    sep = "| --- | --- | --- | --- | --- |"
    row1 = ('| Acme | Solutions Engineer Intern | TX '
            '| <a href="https://a.com/x">A</a> | Aug 21 |')
    row2 = ('| ↳ | Product Manager Intern | Remote '
            '| <a href="https://a.com/y">A</a> | Aug 20 |')
    table = "\n".join([header, sep, row1, row2])
    rows = md.parse_rows(table)
    assert len(rows) == 2, "reads the data rows and skips header/separator"
    assert rows[0][0]["text"] == "Acme", "strips HTML to the visible text"
    assert rows[0][3]["links"] == ["https://a.com/x"], "pulls the href out of a cell"

    company, last = md.resolve_company(rows[0][0]["text"], "")
    assert company == "Acme", "reads a real company name"
    company2, _ = md.resolve_company(rows[1][0]["text"], last)
    assert company2 == "Acme", "the arrow inherits the company above it"

    today = date(2026, 8, 22)
    assert md.parse_absolute_date("Aug 21", today) == "2026-08-21", (
        "an absolute date parses without approximation"
    )
    # A date that would be in the future must belong to last year.
    assert md.parse_absolute_date("Dec 15", today) == "2025-12-15", (
        "a future-looking date rolls back a year"
    )
    assert md.parse_relative_age("3d", today) == "2026-08-19", (
        "a relative age still parses"
    )
    assert md.parse_absolute_date("", today) is None, (
        "an empty date returns None rather than guessing"
    )


def test_strip_html_unwraps_markdown_links_to_their_label():
    """
    DereC4 puts the apply link inside the ROLE cell. Deleting the raw markdown
    link would be simpler but wrong: _extract_links() has already pulled the
    href out by the time _strip_html runs, so the label is unwrapped rather
    than dropped — otherwise the URL becomes part of the job title and reaches
    the scorer, the dashboard and the letter prompts.
    """
    from jobrank.sources.markdown_table import _strip_html

    text = "[Algorithm Development Engineer Intern](https://analogdevices.example/apply)"
    assert _strip_html(text) == "Algorithm Development Engineer Intern"
