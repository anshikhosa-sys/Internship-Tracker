import pytest

from jobrank import textmatch
from jobrank.config import taxonomy


@pytest.mark.parametrize("text,keyword,expected", [
    ("Senior C++ developer", "c++", True),
    ("Experience with .NET Core", ".net", True),
    ("ASP.NET MVC", ".net", False),
    ("Built services in Node.js", "node.js", True),
    ("Node.js services", "node", False),
    ("maintain the pipeline", "ai", False),
    ("AI Engineer", "ai", True),
    ("Texas Instruments", "exa", False),
    ("Python.", "python", True),
    ("C# and F#", "c#", True),
])
def test_whole_word_matching(text, keyword, expected):
    assert textmatch.contains(text, keyword) is expected


def test_case_sensitive_aliases_do_not_fire_on_english():
    assert textmatch.contains("Go, Rust, Python", "Go", case_sensitive=True)
    assert not textmatch.contains("go to market strategy", "Go", case_sensitive=True)


def test_dead_matcher_detection():
    assert textmatch.is_valid_keyword("c++")
    assert not textmatch.is_valid_keyword(" padded ")
    assert not textmatch.is_valid_keyword("")


def test_every_taxonomy_keyword_can_match_itself():
    keywords = [kw for spec in taxonomy.SKILLS.values() for kw in spec.get("aliases", [])]
    keywords += [kw for spec in taxonomy.ROLE_FAMILIES.values() for kw in spec["titles"]]
    dead = [kw for kw in keywords if not textmatch.is_valid_keyword(kw)]
    assert dead == []


def test_abbreviation_expansion():
    assert textmatch.expand_abbreviations("SDE Intern", taxonomy.TITLE_ABBREVIATIONS) == \
        "software development engineer intern"
