"""
The term filter: one mutually-exclusive control replacing the old
"Summer only" + "No co-ops" pair, which overlapped and could contradict.
"""

import pytest

from jobrank.web import app as web_app


class TestEffectiveTerm:
    @pytest.mark.parametrize("value,expected", [
        ("summer", "summer"), ("coop", "coop"), ("offseason", "offseason"),
        ("", ""), ("COOP", "coop"), ("nonsense", ""), (None, ""),
    ])
    def test_only_known_terms_are_honoured(self, value, expected):
        args = {} if value is None else {"term": value}
        assert web_app._effective_term(args) == expected

    def test_the_old_overlapping_pair_is_gone(self):
        """
        Both boxes could be ticked at once, and most co-ops are summer terms,
        so the combination hid rows neither label mentioned.
        """
        assert not hasattr(web_app, "_effective_hide_coop")
        assert not hasattr(web_app, "_effective_hide_offseason")


def test_coop_keywords_match_how_the_corpus_writes_them():
    """Live titles use "Co-op", "Co-Op" and "Coop" — all must match."""
    from jobrank import textmatch
    from jobrank.config import settings
    for title in ["Software Engineer Intern/Co-op", "Full Stack Developer Co-Op",
                  "Machine Learning Coop"]:
        assert textmatch.find_all(title, settings.COOP_KEYWORDS), title

    # ...and an ordinary internship must not.
    assert not textmatch.find_all("Software Engineer Intern", settings.COOP_KEYWORDS)
