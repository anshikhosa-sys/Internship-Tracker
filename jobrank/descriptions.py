"""
Fetch the real job description for a posting.

WHY THIS EXISTS
---------------
Every aggregated source publishes a title, a company and a link — and nothing
else. Measured on the live database, 0 of 9,404 postings carried a description.
That is why the skills factor was indistinguishable from noise in the ablation
(-0.007 AUC, i.e. removing it *helped*): it was matching a résumé against a
seven-word title. "Software Engineer Intern" names no skills, so every one of
roughly a thousand such postings scored the same neutral value and nothing in
the model could separate them.

A description is the only place the market states what it actually wants. With
one, skill overlap becomes a real measurement, the embedding compares two
comparable documents instead of a paragraph against a headline, and
requirements (years, degree, citizenship) can be read rather than guessed.

HOW IT DEGRADES
---------------
Fetching is best-effort and always optional (invariant 7). A posting whose
description cannot be fetched keeps an empty one and scores neutral on skills
(invariant 4) — never penalised for a fetch failure, which is our problem and
not the posting's.

COST
----
Every endpoint is public and unauthenticated (config/descriptions.py). Board
endpoints are preferred because one request covers an entire employer.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import requests

from jobrank.config import descriptions as cfg
from jobrank.log import get_logger

log = get_logger(__name__)


@dataclass
class Target:
    """Where one posting's description can be read from."""
    vendor: str
    board: str
    job: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.vendor, self.board)


def _strip_html(html: str) -> str:
    """HTML to readable text. Descriptions arrive as HTML from most vendors."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    text = re.sub(r"(?i)<(br|/p|/li|/div|/h\d|/tr)\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&rsquo;", "'"), ("&ldquo;", '"'),
                         ("&rdquo;", '"'), ("&mdash;", "—"), ("&ndash;", "–")):
        text = text.replace(entity, char)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"[ \t ]+", " ", text)
    # An inline tag becomes a space, so "<b>Python</b>." would read "Python ."
    # and that stray space lands in every skill match and every embedding.
    text = re.sub(r" +([,.;:!?%)\]])", r"\1", text)
    text = re.sub(r"([(\[]) +", r"\1", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def identify(apply_url: str) -> Target | None:
    """
    Work out which board serves this posting, from the URL alone.

    Redirectors are decoded rather than followed: zapply.jobs names its target
    vendor and board in its own path, so ~1,800 postings resolve with no
    network request at all.
    """
    if not apply_url:
        return None

    redirect = re.search(cfg.REDIRECT_SLUG_PATTERN, apply_url)
    if redirect:
        vendor, board, job = redirect.group(1), redirect.group(2), redirect.group(3)
        vendor = cfg.REDIRECT_VENDOR_ALIASES.get(vendor, vendor)
        if vendor in cfg.VENDORS and vendor != "workday":
            return Target(vendor, board, job)
        return None

    workday = re.search(cfg.VENDORS["workday"]["url_pattern"], apply_url)
    if workday:
        tenant, centre, site, path = workday.groups()
        return Target("workday", f"{tenant}.{centre}", f"{site}{path}")

    for vendor, spec in cfg.VENDORS.items():
        match = re.search(spec["url_pattern"], apply_url)
        if match:
            return Target(vendor, match.group(1), match.group(2))
    return None


def _text_of(value) -> str:
    """
    Flatten whatever a vendor put under its content key into readable text.

    SmartRecruiters nests {"jobAd": {"sections": {"companyDescription":
    {"title": ..., "text": ...}}}}, so a naive str() of the dict writes Python
    repr punctuation into the description and every brace ends up in the
    embedding. Walk it and keep only the strings.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(filter(None, (_text_of(v) for v in value.values())))
    if isinstance(value, list):
        return "\n".join(filter(None, (_text_of(v) for v in value)))
    return ""


def _extract(payload, keys: list[str]) -> str:
    for key in keys:
        text = _text_of(payload.get(key)).strip()
        if text:
            return _strip_html(text)
    return ""


def _job_id_of(record: dict) -> set[str]:
    """Every identifier a board record might be matched on."""
    found = set()
    for key in ("id", "shortcode", "jobId"):
        value = record.get(key)
        if value is not None:
            found.add(str(value))
    for key in ("jobUrl", "hostedUrl", "applyUrl", "absolute_url"):
        url = record.get(key)
        if isinstance(url, str):
            found.update(re.findall(r"([\w-]{36}|\d{4,})", url))
    return found


class Fetcher:
    """
    Fetches descriptions, preferring one request per board over one per job.

    A host that fails repeatedly is dropped for the rest of the run: a board
    that is down or rate-limiting should cost us three requests, not 1,800.
    """

    def __init__(self, session: requests.Session | None = None, delay: float | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": cfg.USER_AGENT})
        self.delay = cfg.DELAY_BETWEEN_REQUESTS_SECONDS if delay is None else delay
        self._boards: dict[tuple[str, str], dict[str, str]] = {}
        self._failures: dict[str, int] = {}
        self.requests_made = 0

    # -- plumbing ----------------------------------------------------------
    def _get(self, url: str, host_key: str):
        if self._failures.get(host_key, 0) >= cfg.MAX_ATTEMPTS_PER_HOST:
            return None
        if self.delay and self.requests_made:
            time.sleep(self.delay)
        try:
            response = self.session.get(url, timeout=cfg.REQUEST_TIMEOUT_SECONDS)
            self.requests_made += 1
            if response.status_code != 200:
                self._failures[host_key] = self._failures.get(host_key, 0) + 1
                return None
            self._failures.pop(host_key, None)
            return response
        except requests.RequestException as exc:
            self._failures[host_key] = self._failures.get(host_key, 0) + 1
            log.debug("description fetch failed", extra={"fields": {"url": url, "error": str(exc)}})
            return None

    # -- board-level -------------------------------------------------------
    def _load_board(self, target: Target) -> dict[str, str]:
        """job id -> description for an entire employer, in one request."""
        if target.key in self._boards:
            return self._boards[target.key]
        spec = cfg.VENDORS[target.vendor]
        table: dict[str, str] = {}
        if spec["board_api"]:
            response = self._get(spec["board_api"].format(board=target.board), target.vendor)
            if response is not None:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                records = payload if isinstance(payload, list) else \
                    (payload or {}).get("jobs") or (payload or {}).get("content") or []
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    text = _extract(record, spec["content_keys"])
                    if len(text) >= cfg.MIN_DESCRIPTION_CHARS:
                        for identifier in _job_id_of(record):
                            table[identifier] = text
        self._boards[target.key] = table
        return table

    # -- public ------------------------------------------------------------
    def description_for(self, apply_url: str) -> str:
        """The posting's description, or "" when it cannot be had for free."""
        target = identify(apply_url)
        if target is None:
            return ""
        board = self._load_board(target)
        text = board.get(target.job, "")
        if not text and target.vendor == "workday":
            tenant = target.board.split(".")[0]
            site, path = target.job.split("/job/", 1)
            response = self._get(f"https://{target.board}.myworkdayjobs.com"
                                 f"/wday/cxs/{tenant}/{site}/job/{path}", "workday")
            if response is not None:
                try:
                    text = _extract(response.json().get("jobPostingInfo") or {},
                                    cfg.VENDORS["workday"]["content_keys"])
                except ValueError:
                    text = ""
        if not text:
            spec = cfg.VENDORS[target.vendor]
            if spec["job_api"]:
                response = self._get(spec["job_api"].format(board=target.board, job=target.job),
                                     target.vendor)
                if response is not None:
                    try:
                        text = _extract(response.json(), spec["content_keys"])
                    except ValueError:
                        text = ""
        if len(text) < cfg.MIN_DESCRIPTION_CHARS:
            return ""
        return text[:cfg.MAX_DESCRIPTION_CHARS]
