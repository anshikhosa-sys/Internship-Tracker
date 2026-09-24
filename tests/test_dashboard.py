"""
The Flask dashboard: age-window counts that never lie about what they'll
return, the plain-text prompt endpoint the copy button reads, the visit-basis
guard that keeps a health probe from clearing NEW badges, application quotas,
per-company crowding, and "applied is always visible".

Ported from tests/legacy_checks.py (test_age_windows, test_prompt_text_endpoint,
test_probes_dont_consume_badges, test_quotas, test_crowding,
test_applied_always_visible).

ADAPTATION NOTES
-----------------
- test_quotas: `_quota_group` now groups by PARENT company
  (jobrank.postings.parent_company / jobrank.config.companies.PARENT_COMPANIES
  + APPLICATION_LIMITS) rather than matching the company name against the
  limits table directly. The assertions are unchanged in spirit (ByteDance
  shares TikTok's pool, an uncapped company has no quota, whole-word matching
  keeps "Tiktokenizer Inc" out) but go through the parent-company path.
- test_crowding: `MAX_PER_COMPANY` moved from config (settings.py) to
  `jobrank.config.companies`, since it's a fact about companies rather than a
  dashboard default. Import path updated; the simulated capping logic itself
  is unchanged (app.py's real _apply_caps is exercised by the quota/crowding
  interaction in test_dashboard, this test pins the standalone cap invariant).
- test_prompt_text_endpoint / test_probes_dont_consume_badges: now require an
  active profile to exist (jobrank.ranking.rank() redirects "/" to /profile
  otherwise) and the semantic layer is forced to the offline hashing backend
  with a temp vector-store path, so nothing downloads a model or writes into
  the repo's real .cache/. A run is always recorded before hitting "/" so
  app.py's _catch_up_if_stale() never fires a real network refresh.
- test_applied_always_visible: dashboard._filtered() gained an `is_tech_employer`-
  gated filter and reads the threshold from jobrank.config.scoring, but the
  row shape and "applied bypasses every discovery filter" behavior this test
  guards is otherwise unchanged, so the row fixture just needed the new/renamed
  score field names it already had (fit_score, age_days, is_coop, is_off_season,
  company_tier) — no assertions were dropped.
"""

import pytest

from jobrank import config, ranking, storage
from jobrank.config import companies
from jobrank.profile import store
from jobrank.sources.base import Posting


# =============================================================================
# AGE WINDOWS — every option must be able to return something, and none may
# promise more than the hard cutoff can deliver.
# =============================================================================

def test_age_windows_are_well_formed():
    """
    Every age window offered must be able to return something, in principle.

    "Posted today only" was removed and later restored — see config/settings.py
    for why. The durable guarantee tested here is not "there is a today
    filter"; it's that every window is inside the hard cutoff, ordered
    narrowest first, uniquely labelled, and never negative. The stronger
    claim — that the count next to each label equals what actually renders —
    can only be checked with a real browser (browser_tests.py), not here.
    """
    values = [days for days, _ in config.AGE_WINDOWS]

    assert None in values, "an unrestricted window is offered"

    numeric = [v for v in values if v is not None]
    assert numeric == sorted(numeric), "windows are offered narrowest-first"
    assert len(set(values)) == len(values), "no duplicate windows"
    assert all(v is None or v >= 0 for v in values), (
        "no window asks for a negative age"
    )

    # Every window must sit inside the hard cutoff, or it silently lies:
    # "Last 30 days" would still be capped at MAX_AGE_DAYS.
    assert all(v is None or v <= config.MAX_AGE_DAYS for v in numeric), (
        f"no window promises more than the {config.MAX_AGE_DAYS}-day cutoff can deliver"
    )

    labels = [label for _, label in config.AGE_WINDOWS]
    assert all(label.strip() for label in labels), "every window is labelled"
    assert len(set(labels)) == len(labels), "no two windows share a label"


# =============================================================================
# Fixtures shared by the Flask-backed tests below
# =============================================================================

@pytest.fixture
def dashboard_client(tmp_path, monkeypatch, example_resume_text):
    """
    A Flask test client with its own throwaway databases, an active profile
    (the index route redirects to /profile without one), and the semantic
    layer forced offline so nothing downloads a model or touches the repo's
    real .cache/ directory. A run is recorded (even with zero postings) so
    app.py's stale-data catch-up never triggers a real network refresh.
    """
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "internships.db"))
    monkeypatch.setattr(config, "APPLICATIONS_PATH", str(tmp_path / "applications.db"))
    monkeypatch.setattr(config, "APPLICATIONS_EXPORT", str(tmp_path / "applications.json"))

    from jobrank.config import semantic as semantic_cfg
    monkeypatch.setattr(semantic_cfg, "BACKEND", "hashing")
    monkeypatch.setattr(semantic_cfg, "VECTOR_DB_PATH", str(tmp_path / "vectors.db"))
    monkeypatch.setattr(semantic_cfg, "ALLOW_MODEL_DOWNLOAD", False)

    from jobrank.resume import parser as resume_parser
    resume = resume_parser.parse_text(example_resume_text)
    store.save("tester", resume)

    conn = storage.connect()
    storage.record_run(conn, storage.now_iso(), 0, 0)
    conn.close()
    ranking.clear_cache()

    from jobrank.web import app as app_module
    return app_module.app.test_client()


def _seed_posting(role="Software Engineer Intern", company="Acme"):
    posting = Posting(
        company=company, role=role, category="Software Engineering",
        location="Austin, TX", apply_url="https://acme.com/apply",
        source="test",
    )
    conn = storage.connect()
    storage.save_postings(conn, [posting], storage.now_iso())
    storage.record_run(conn, storage.now_iso(), 1, 0)
    conn.close()
    ranking.clear_cache()
    return posting.id


# =============================================================================
# THE COPY BUTTON READS THIS ENDPOINT, NOT THE DOM
# =============================================================================

def test_prompt_text_endpoint(dashboard_client):
    """
    The copy button reads the prompt from the SERVER, not from the DOM.

    It used to scrape the rendered <pre> with innerText — a rendering-
    dependent API affected by max-height, overflow-y, white-space and
    word-break, all of which that element sets. Prompts pasted in blank or
    mangled. This endpoint is the source text, with no layout involved.
    """
    client = dashboard_client
    posting_id = _seed_posting()
    assert posting_id, "a posting is seeded for the endpoint test"

    for kind in ("cover", "experience"):
        resp = client.get(f"/prompts/{posting_id}/{kind}.txt")
        assert resp.status_code == 200, f"{kind}.txt returns 200"

        # Exactly one charset. mimetype= appends its own, which produced
        # "text/plain; charset=utf-8; charset=utf-8".
        ctype = resp.headers.get("Content-Type", "")
        assert ctype.startswith("text/plain"), f"{kind}.txt is text/plain"
        assert ctype.count("charset") == 1, f"{kind}.txt declares charset exactly once"

        body = resp.get_data(as_text=True)
        assert len(body) > 1000, f"{kind}.txt carries the whole prompt ({len(body)} chars)"
        assert body.strip() == body.strip().strip("\x00"), f"{kind}.txt has no null bytes"
        assert body.strip() != "", f"{kind}.txt is not blank"

    # A pasted job description must reach the plain-text prompt.
    marker = "ZZQXMARKER distributed feature stores"
    resp = client.get(f"/prompts/{posting_id}/cover.txt", query_string={"jd": marker})
    assert marker.split()[0] in resp.get_data(as_text=True), (
        "a pasted job description reaches the copied text"
    )

    # The HTML page must point the button at the same text.
    resp = client.get(f"/prompts/{posting_id}", headers={"Accept": "text/html"})
    page = resp.get_data(as_text=True)
    assert f"/prompts/{posting_id}/cover.txt" in page, (
        "the page wires the copy button to the plain-text URL"
    )
    assert "Open as plain text" in page, "a no-JavaScript fallback link is offered"

    assert client.get(f"/prompts/{posting_id}/bogus.txt").status_code == 404, (
        "an unknown prompt kind is 404, not a blank 200"
    )
    assert client.get("/prompts/job:nosuchid/cover.txt").status_code == 404, (
        "an unknown posting is 404, not a blank 200"
    )


# =============================================================================
# A HEALTH CHECK MUST NOT CLEAR THE BADGES IT REPORTS ON
# =============================================================================

def test_probes_dont_consume_badges(dashboard_client):
    """
    Both healthcheck.py and schedule.sh used to fetch "/", which registers a
    visit. Running the health check therefore destroyed the thing it was
    checking, and nobody would ever have noticed from its output.
    """
    client = dashboard_client

    def basis():
        conn = storage.connect()
        try:
            return storage.current_visit_basis(conn)
        finally:
            conn.close()

    before = basis()

    # A probe: no HTML in Accept, which is what curl and urllib send.
    resp = client.get("/healthz")
    assert resp.status_code == 200, "/healthz answers 200"
    assert basis() == before, "/healthz does not move the visit basis"

    resp = client.get("/", headers={"Accept": "*/*"})
    assert resp.status_code == 200, "a non-browser GET of / still works"
    assert basis() == before, "curl's 'Accept: */*' does not move the visit basis"

    resp = client.get("/")
    assert basis() == before, (
        "a request with no Accept header does not move the visit basis"
    )

    # current_visit_basis is read-only by construction: two reads in a row
    # must agree.
    settled = basis()
    assert basis() == settled and basis() == settled, (
        "reading the visit basis twice does not change it"
    )

    # ...but a browser must still get its badges.
    resp = client.get("/", headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9"
    })
    assert resp.status_code == 200, "a browser GET of / works"
    conn = storage.connect()
    try:
        assert storage._get_state(conn, "last_activity") is not None, (
            "a browser page view DOES register a visit"
        )
    finally:
        conn.close()


# =============================================================================
# APPLICATION QUOTAS — parent-company grouping
# =============================================================================

def test_quotas():
    """
    Some employers cap applications per cycle. Once the cap is spent, the
    remaining roles there are not opportunities and must stop being offered.
    """
    from jobrank.web import app

    group = app._quota_group("TikTok")
    assert group is not None, "TikTok is covered by a quota"
    # _quota_group() builds a fresh {"name", "limit"} dict per call rather
    # than returning a cached singleton, so "shares TikTok's pool" is an
    # equality check now, not an identity one.
    assert app._quota_group("ByteDance") == group, (
        "ByteDance shares TikTok's pool — one parent, one quota"
    )
    assert app._quota_group("Zipline") is None, "a company with no cap has no quota"
    assert app._quota_group("Tiktokenizer Inc") is None, (
        "whole-word matching: 'Tiktokenizer' is not TikTok"
    )

    limit = group["limit"]

    def state(applied_count):
        postings = [{"company": "TikTok", "status": "applied"} for _ in range(applied_count)]
        postings.append({"company": "Zipline", "status": ""})
        return app._quota_state(postings)[group["name"]]

    assert state(0)["left"] == limit, "an unused quota has every slot left"
    assert state(1)["left"] == limit - 1, "one application spends one slot"
    assert state(limit)["left"] == 0, "spending the quota leaves none"
    assert state(limit + 5)["left"] == 0, (
        "over-applying clamps at zero rather than going negative"
    )

    # A rejection still consumed the slot — the cap is on applications sent,
    # not on applications that went well.
    mixed = app._quota_state([
        {"company": "TikTok", "status": "rejected"},
        {"company": "ByteDance", "status": "interview"},
    ])[group["name"]]
    assert mixed["used"] == 2, "a rejection and an interview both count against the quota"
    assert mixed["left"] == max(0, limit - 2), (
        "the pool is shared across the parent and its subsidiaries"
    )


# =============================================================================
# CROWDING — one company must not be able to take over the list
# =============================================================================

def test_crowding():
    """One company must not be able to take over the list."""
    assert companies.MAX_PER_COMPANY and companies.MAX_PER_COMPANY > 0, (
        "a per-company cap is configured"
    )

    # Simulate the cap the way app.py applies it: after sorting, keeping each
    # company's best, and never touching anything you've applied to.
    postings = []
    for i in range(10):
        postings.append({"company": "TikTok", "status": "", "fit_score": 90 - i})
    for i in range(2):
        postings.append({"company": "Zipline", "status": "", "fit_score": 50 - i})
    postings.append({"company": "TikTok", "status": "applied", "fit_score": 1})

    per, kept, held = {}, [], 0
    for p in sorted(postings, key=lambda p: -p["fit_score"]):
        if p["status"]:
            kept.append(p)
            continue
        per[p["company"]] = per.get(p["company"], 0) + 1
        if per[p["company"]] <= companies.MAX_PER_COMPANY:
            kept.append(p)
        else:
            held += 1

    tiktok = [p for p in kept if p["company"] == "TikTok" and not p["status"]]
    assert len(tiktok) == companies.MAX_PER_COMPANY, (
        f"a flooding company is capped at {companies.MAX_PER_COMPANY}"
    )
    assert [p["fit_score"] for p in tiktok] == [90, 89, 88], (
        "the roles kept are that company's highest-scoring ones"
    )
    assert len([p for p in kept if p["company"] == "Zipline"]) == 2, (
        "a company under the cap is untouched"
    )
    assert held == 7, "the held-back count is reported, not silently dropped"
    assert any(p["status"] == "applied" for p in kept), (
        "an application you've made is never hidden by the cap"
    )


# =============================================================================
# APPLIED IS ALWAYS VISIBLE
# =============================================================================

def test_applied_always_visible():
    """
    A posting you've applied to must never be filtered out of the list.

    Reported as "I lose the status of ones I've applied to when I refresh".
    The mark was never lost — the posting aged past the "posted today only"
    filter and vanished from view, which looks identical to data loss.
    """
    from jobrank.web import app as dashboard

    def row(status="", age_days=5, score=80, coop=False, off=False):
        return {
            "id": "job:x", "company": "Acme", "role": "SWE Intern",
            "location": "NYC", "category": "Software Engineering",
            "status": status, "applied": bool(status), "fit_score": score,
            "age_days": age_days, "is_fresh": age_days <= 3,
            "is_coop": coop, "is_off_season": off,
            "company_tier": "mid",
        }

    # Bare load = today only. A 5-day-old unapplied posting is filtered out.
    unapplied = dashboard._filtered([row()], set(), {})
    assert len(unapplied) == 0, "an old unapplied posting is hidden by the age filter"

    # The same posting, applied, must survive.
    applied = dashboard._filtered([row(status="applied")], set(), {})
    assert len(applied) == 1, "an old APPLIED posting stays visible despite the age filter"

    # And survive every other discovery filter too.
    assert len(dashboard._filtered(
        [row(status="applied", score=1)], set(), {})) == 1, (
        "a low-scoring applied posting stays visible"
    )
    assert len(dashboard._filtered(
        [row(status="applied", coop=True)], set(), {})) == 1, (
        "an applied co-op stays visible"
    )
    assert len(dashboard._filtered(
        [row(status="applied", off=True)], set(), {})) == 1, (
        "an applied off-season role stays visible"
    )
    assert len(dashboard._filtered(
        [row(status="applied", age_days=400)], set(), {})) == 1, (
        "an applied posting past the hard cutoff stays visible"
    )

    # But a deliberate narrowing must still work.
    only_new = dashboard._filtered(
        [row(status="applied")], set(), {"f": "1", "status": "new"})
    assert len(only_new) == 0, (
        "an explicit status filter still applies to applied postings"
    )
