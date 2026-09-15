"""
Résumé file -> plain text.

PDF via pypdf (pure Python). DOCX is a zip of XML, read with the standard
library, so no extra dependency. Every failure raises ResumeReadError with a
message a user can act on; nothing returns empty text silently, because an
empty résumé would derive a profile with no skills and rank every posting as a
poor fit without saying why.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

from jobrank.config import profile as profile_config


class ResumeReadError(ValueError):
    pass


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}


def read_bytes(data: bytes, filename: str) -> str:
    if not data:
        raise ResumeReadError("The file is empty.")
    if len(data) > profile_config.MAX_RESUME_BYTES:
        raise ResumeReadError(f"The file is larger than {profile_config.MAX_RESUME_BYTES // 1_000_000} MB.")
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        text = _pdf_text(data)
    elif name.endswith(".docx") or (data[:2] == b"PK" and not name.endswith(tuple(TEXT_SUFFIXES))):
        text = _docx_text(data)
    elif name.endswith(tuple(TEXT_SUFFIXES)) or not name:
        text = _plain_text(data)
    else:
        raise ResumeReadError(f"Unsupported file type '{filename}'. Use PDF, DOCX, TXT or Markdown.")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    if len(text.strip()) < profile_config.MIN_RESUME_CHARS:
        raise ResumeReadError(
            f"Only {len(text.strip())} characters of text could be read. If this is a scanned PDF, "
            "export it from the original document instead — images contain no text to parse."
        )
    return text


def read_path(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            data = handle.read(profile_config.MAX_RESUME_BYTES + 1)
    except OSError as exc:
        raise ResumeReadError(f"Could not open {path}: {exc.strerror or exc}") from exc
    return read_bytes(data, path)


def _plain_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ResumeReadError("The text file is not UTF-8 or UTF-16 encoded.")


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - dependency is in requirements
        raise ResumeReadError("PDF support needs pypdf: pip install pypdf") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ResumeReadError("The PDF is password-protected. Export an unlocked copy.")
        pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    except PdfReadError as exc:
        raise ResumeReadError(f"The PDF could not be read: {exc}") from exc
    # Layout mode preserves columns with runs of spaces; collapse long runs but
    # keep line structure, which the section parser depends on.
    lines = []
    for page in pages:
        for line in page.splitlines():
            # Runs of 3+ spaces are column gaps ("Acme   Engineer   2025"); keep
            # them as tabs, which the header splitter treats as separators.
            lines.append(re.sub(r"\s{3,}", "\t", line.strip()))
    return "\n".join(lines)


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ResumeReadError("The DOCX file is damaged or is not a Word document.") from exc
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ResumeReadError("The DOCX document body could not be parsed.") from exc
    lines = []
    for paragraph in root.iter(f"{_W}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag == f"{_W}br":
                parts.append("\n")
        text = "".join(parts).strip()
        is_list = paragraph.find(f"{_W}pPr/{_W}numPr") is not None
        lines.append(f"- {text}" if is_list and text else text)
    return "\n".join(lines)
