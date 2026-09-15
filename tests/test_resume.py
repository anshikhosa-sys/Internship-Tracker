import json

import pytest

from jobrank.resume import dates, parser, readers, rules
from tests.conftest import make_docx, make_pdf


@pytest.mark.parametrize("line,start,end,current", [
    ("New York, NY · August 2026 – Present", "2026-08", None, True),
    ("Jun 2025 - Aug 2025", "2025-06", "2025-08", False),
    ("06/2024 – 08/2024", "2024-06", "2024-08", False),
    ("2022-2024", "2022-01", "2024-12", False),
    ("Sept. 2023 to Dec 2023", "2023-09", "2023-12", False),
    ("Summer 2025", "2025-06", "2025-06", False),
])
def test_date_ranges(line, start, end, current):
    found = dates.find_range(line)
    assert (found.start, found.end, found.current) == (start, end, current)


def test_phone_numbers_are_not_dates():
    assert dates.find_range("(952) 555-3106") is None


class TestExampleResume:
    @pytest.fixture
    def resume(self, example_resume_text):
        return parser.parse_text(example_resume_text)

    def test_education(self, resume):
        (edu,) = resume.education
        assert edu.level == "bachelor" and edu.field == "Computer Science"
        assert edu.graduation == "2027-05"

    def test_experience(self, resume):
        intern, sysadmin = resume.experience
        assert (intern.title, intern.organization) == ("Software Engineer Intern", "Northwind Logistics")
        assert (intern.start, intern.end, intern.is_internship) == ("2026-06", "2026-08", True)
        assert sysadmin.is_part_time and not sysadmin.is_internship
        assert len(intern.bullets) == 3 and "40%" in intern.bullets[0]

    def test_projects(self, resume):
        names = [p.name for p in resume.projects]
        assert names == ["Tiny Raft", "Rate Limiter as a Service"]
        assert "gRPC" in resume.projects[0].technologies

    def test_skills_are_canonical(self, resume):
        for skill in ["go", "kubernetes", "terraform", "kafka", "grpc", "fastapi", "redis"]:
            assert skill in resume.skills
        assert "c" not in resume.skills  # no bare capital C anywhere

    def test_no_contact_details_are_stored(self, resume):
        blob = json.dumps(resume.to_dict())
        assert "Jordan" not in blob and "example.com" not in blob

    def test_extractor_and_hash_recorded(self, resume, example_resume_text):
        assert resume.extractor == "rules"
        assert len(resume.content_hash) == 64


def test_plain_text_all_caps_layout(plain_resume_text):
    resume = parser.parse_text(plain_resume_text)
    titles = [(x.title, x.organization, x.start, x.current) for x in resume.experience]
    assert titles[0] == ("Senior Software Engineer", "Brightline Health", "2024-01", True)
    assert titles[1][:3] == ("Software Engineer", "Harbor Data", "2022-07")
    assert "3M requests per day" in resume.experience[0].bullets[0]
    assert resume.education[0].level == "bachelor"
    assert not resume.education[0].expected
    assert {"snowflake", "java", "django", "fastapi"} <= set(resume.skills)


def test_docx_reader(example_resume_text):
    paragraphs = ["EXPERIENCE", "Acme Corp — Data Engineer Intern", "Jun 2025 – Aug 2025",
                  "Built Airflow DAGs loading Snowflake tables", "SKILLS", "Python, SQL, Airflow"] + ["filler"] * 30
    text = readers.read_bytes(make_docx(paragraphs, bullets={3}), "resume.docx")
    assert "- Built Airflow DAGs" in text
    resume = parser.parse_text(text)
    assert resume.experience[0].title == "Data Engineer Intern"
    assert "airflow" in resume.skills


def test_pdf_reader():
    lines = ["EXPERIENCE", "Acme Corp    Backend Engineer Intern    Jun 2025 - Aug 2025",
             "- Wrote Go services behind gRPC", "SKILLS", "Go, gRPC, PostgreSQL, Docker, Kubernetes"]
    lines += ["Additional detail line to exceed the minimum text length for parsing."] * 4
    text = readers.read_bytes(make_pdf(lines), "resume.pdf")
    resume = parser.parse_text(text)
    assert resume.experience[0].title == "Backend Engineer Intern"
    assert {"go", "grpc", "postgresql"} <= set(resume.skills)


@pytest.mark.parametrize("data,filename,message", [
    (b"", "r.pdf", "empty"),
    (b"too short", "r.txt", "characters of text"),
    (b"x" * 500, "r.png", "Unsupported file type"),
    (b"PK\x03\x04broken" + b"x" * 300, "r.docx", "damaged"),
])
def test_read_errors_are_explicit(data, filename, message):
    with pytest.raises(readers.ResumeReadError, match=message):
        readers.read_bytes(data, filename)


def test_scanned_pdf_explains_itself():
    with pytest.raises(readers.ResumeReadError, match="scanned PDF"):
        readers.read_bytes(make_pdf([""]), "scan.pdf")


def test_unstructured_text_is_refused_not_silently_empty():
    prose = "I am a hard worker who loves building things and learning new ideas every day. " * 5
    with pytest.raises(rules.ResumeParseError, match="No experience, projects, or skills"):
        parser.parse_text(prose)


def test_reparsing_same_resume_hits_cache(monkeypatch, example_resume_text):
    parser.parse_text(example_resume_text)
    monkeypatch.setattr(rules, "extract", lambda text: pytest.fail("extraction re-ran"))
    # parser holds a reference captured at call time, so patch where it is looked up
    monkeypatch.setattr(parser.rules, "extract", lambda text: pytest.fail("extraction re-ran"))
    assert parser.parse_text(example_resume_text).skills
