"""
Fetching job descriptions, and the disambiguation that stops a season from
being read as a Java framework.

No network: every vendor payload here is a fixture. The point of the tests is
the URL algebra and the text handling, which is where the bugs were.
"""

import pytest

from jobrank import descriptions, textmatch
from jobrank.config import descriptions as cfg
from jobrank.config import taxonomy
from jobrank.resume.parser import canonical_skills


class TestIdentify:
    @pytest.mark.parametrize("url,vendor,board,job", [
        ("https://job-boards.greenhouse.io/acme/jobs/4567890", "greenhouse", "acme", "4567890"),
        ("https://boards.greenhouse.io/acme/jobs/4567890", "greenhouse", "acme", "4567890"),
        ("https://jobs.lever.co/ifm-us/5342e333-61b9-406d-bfea-61a687a94d1f",
         "lever", "ifm-us", "5342e333-61b9-406d-bfea-61a687a94d1f"),
        ("https://jobs.ashbyhq.com/exa/a9e01521-66f1-481b-89da-ec01d4620f16?utm_source=x",
         "ashby", "exa", "a9e01521-66f1-481b-89da-ec01d4620f16"),
        ("https://jobs.smartrecruiters.com/AbbVie/3743990014860391", "smartrecruiters", "AbbVie", "3743990014860391"),
    ])
    def test_reads_vendor_board_and_job_from_the_url(self, url, vendor, board, job):
        target = descriptions.identify(url)
        assert (target.vendor, target.board, target.job) == (vendor, board, job)

    def test_decodes_a_redirector_without_following_it(self):
        """
        zapply.jobs names its target in its own path, so ~1,800 postings
        resolve with no network request at all.
        """
        target = descriptions.identify(
            "https://zapply.jobs/l/d/lever-ifm-us-5342e333-61b9-406d-bfea-61a687a94d1f?s=gh")
        assert (target.vendor, target.board) == ("lever", "ifm-us")

    def test_redirector_vendor_aliases(self):
        assert descriptions.identify("https://zapply.jobs/l/d/sr-abbvie-3743990014860391").vendor \
            == "smartrecruiters"

    def test_workday_url_becomes_its_json_path(self):
        target = descriptions.identify(
            "https://blackstone.wd1.myworkdayjobs.com/en-US/Campus/job/Miami/Analyst_45022")
        assert target.vendor == "workday" and target.board == "blackstone.wd1"
        assert target.job == "Campus/job/Miami/Analyst_45022"

    def test_an_unknown_host_is_not_guessed_at(self):
        assert descriptions.identify("https://careers.example.com/jobs/123") is None
        assert descriptions.identify("") is None


class TestText:
    def test_html_becomes_readable_text(self):
        text = descriptions._strip_html(
            "<div><p>We use <b>Python</b>.</p><script>evil()</script><li>5&nbsp;years</li></div>")
        assert "evil" not in text and "<" not in text
        assert "We use Python." in text and "5 years" in text

    def test_nested_sections_are_flattened_not_repr_ed(self):
        """
        SmartRecruiters nests {"jobAd": {"sections": {...{"text": ...}}}}. A
        naive str() writes Python punctuation into the description, and every
        brace ends up in the embedding.
        """
        payload = {"jobAd": {"sections": {"company": {"title": "About", "text": "We build things."},
                                          "quals": {"title": "You", "text": "Python and SQL."}}}}
        text = descriptions._extract(payload, ["jobAd"])
        assert "Python and SQL." in text and "We build things." in text
        assert "{" not in text and "'title'" not in text


class TestAmbiguousSkills:
    @pytest.mark.parametrize("text", [
        "Software Engineering Intern - Design Automation (Spring 2027 Co-Op)",
        "Internship, Computer Vision Engineer (Winter/Spring 2027)",
        "Spring semester internship",
    ])
    def test_a_season_is_not_the_java_framework(self, text):
        """153 live postings were credited with Spring for saying when they start."""
        assert "spring" not in canonical_skills(text)

    @pytest.mark.parametrize("text", [
        "Backend engineer using Java and Spring Boot",
        "Built REST services with Spring and Hibernate in Java",
    ])
    def test_the_real_framework_still_matches(self, text):
        assert "spring" in canonical_skills(text)

    def test_swift_the_language_versus_the_adjective(self):
        assert "swift" in canonical_skills("iOS developer, Swift and SwiftUI")
        assert "swift" not in canonical_skills("Ensured swift resolution of customer issues")

    def test_every_rule_names_a_real_skill(self):
        assert set(taxonomy.AMBIGUOUS_SKILLS) <= set(taxonomy.SKILLS)


class TestFetcher:
    def test_a_short_description_is_treated_as_no_description(self, monkeypatch):
        """"Apply on our website" is boilerplate, not content."""
        fetcher = descriptions.Fetcher(delay=0)
        monkeypatch.setattr(fetcher, "_load_board", lambda target: {target.job: "Apply here."})
        assert fetcher.description_for("https://jobs.ashbyhq.com/exa/"
                                       "a9e01521-66f1-481b-89da-ec01d4620f16") == ""

    def test_a_failing_host_is_dropped_rather_than_retried_forever(self):
        """A board that is down should cost three requests, not eighteen hundred."""
        class Dead:
            headers = {}
            def get(self, *a, **k):
                raise __import__("requests").RequestException("down")
        fetcher = descriptions.Fetcher(session=Dead(), delay=0)
        for _ in range(8):
            fetcher.description_for("https://jobs.lever.co/acme/"
                                    "5342e333-61b9-406d-bfea-61a687a94d1f")
        assert fetcher._failures["lever"] <= cfg.MAX_ATTEMPTS_PER_HOST

    def test_every_configured_endpoint_is_unauthenticated(self):
        """Invariant 6: nothing in this repo can spend money or need a key."""
        for spec in cfg.VENDORS.values():
            for template in (spec["board_api"], spec["job_api"]):
                if template:
                    assert "key" not in template.lower() and "token=" not in template.lower()
                    assert template.startswith("https://")
