"""
Shared fixtures. Every test runs against temporary paths: no test reads or
writes the real profiles, caches, logs or databases, and none touches the
network (the LLM backend is forced to rules unless a test fakes a model).
"""

import io
import os
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBRANK_LOG_DIR", str(tmp_path / "logs"))
    from jobrank import llm
    from jobrank.config import llm as llm_cfg
    from jobrank.config import profile as profile_cfg

    monkeypatch.setattr(llm_cfg, "CACHE_PATH", str(tmp_path / "llm.db"))
    monkeypatch.setattr(llm_cfg, "BACKEND", "rules")
    monkeypatch.setattr(profile_cfg, "PROFILES_DIR", str(tmp_path / "profiles"))
    llm.reset_probe()
    yield tmp_path
    llm.reset_probe()


@pytest.fixture
def example_resume_text():
    with open(os.path.join(EXAMPLES, "resume_example.md"), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture
def example_prefs():
    import json

    with open(os.path.join(EXAMPLES, "preferences_example.json"), encoding="utf-8") as handle:
        return json.load(handle)


PLAIN_RESUME = """ALEX KIM
alex@example.com | (555) 010-2000

EDUCATION
Lakeside University    Aug 2018 - May 2022
Bachelor of Science in Computer Science

EXPERIENCE
Brightline Health    Boston, MA
Senior Software Engineer    Jan 2024 - Present
- Led migration of patient scheduling APIs from Django to FastAPI, serving
3M requests per day
- Designed PostgreSQL partitioning for appointment history
Harbor Data    Remote
Software Engineer    Jul 2022 - Dec 2023
- Built Kafka consumers in Java that fed a Snowflake warehouse
- Wrote Terraform modules for AWS networking

SKILLS
Languages: Python, Java, SQL, TypeScript
Tools: Docker, Kubernetes, Terraform, AWS, Kafka, Snowflake
"""


@pytest.fixture
def plain_resume_text():
    return PLAIN_RESUME


def make_docx(paragraphs: list[str], bullets: set[int] = frozenset()) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = []
    for i, text in enumerate(paragraphs):
        num = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>' if i in bullets else ""
        escaped = text.replace("&", "&amp;").replace("<", "&lt;")
        body.append(f"<w:p>{num}<w:r><w:t xml:space=\"preserve\">{escaped}</w:t></w:r></w:p>")
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body>{"".join(body)}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def make_pdf(lines: list[str]) -> bytes:
    """A minimal single-page PDF with real text objects (no dependency needed)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = "BT /F1 10 Tf 12 TL 50 780 Td " + " ".join(f"({esc(line)}) '" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out
