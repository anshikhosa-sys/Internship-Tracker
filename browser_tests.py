"""
browser_tests.py — drive the real UI in a real browser.

WHY THIS FILE EXISTS
--------------------
Two copy-button bugs shipped in a row, and tests.py passed through both of
them. It had to: tests.py exercises Python. Neither bug was in Python.

  1. The button copied `innerText`, which returns text AS LAID OUT and is
     shaped by the element's CSS. The clipboard got mangled text.
  2. The replacement awaited a fetch and then called
     navigator.clipboard.writeText(), which a browser denies without a
     pre-granted permission. The clipboard got nothing.

Both were invisible: the button said "Copied" either way. The only thing
that could have caught them is a browser, so this runs one.

  python3 browser_tests.py

It serves a COPY of your database on a spare port, so nothing here can
touch your real postings or applications.

Requires playwright (`pip install playwright && playwright install
chromium`). If it is missing this exits 2 and says so — it does not
silently pass, which is the failure mode that let the bugs through.
"""

import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time

PASS, FAIL = "PASS", "FAIL"
_results = []


def check(condition, label, detail=""):
    _results.append(bool(condition))
    mark = PASS if condition else FAIL
    line = f"  [{mark}] {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return bool(condition)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve_a_copy():
    """Start the app against a throwaway copy of the database."""
    import config

    folder = tempfile.mkdtemp(prefix="internship-browser-tests-")
    for attr, name in (("DATABASE_PATH", "internships.db"),
                       ("APPLICATIONS_PATH", "applications.db")):
        source = getattr(config, attr)
        target = os.path.join(folder, name)
        if os.path.exists(source):
            shutil.copy(source, target)
        setattr(config, attr, target)
    config.APPLICATIONS_EXPORT = os.path.join(folder, "applications.json")

    # Never let a test run fetch the network or mutate anything upstream.
    config.STALE_DATA_HOURS = 10 ** 6

    import app as app_module

    # Werkzeug logs every request; the test output is the point here.
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app_module.app.logger.setLevel(logging.ERROR)

    port = _free_port()
    thread = threading.Thread(
        target=lambda: app_module.app.run(
            host="127.0.0.1", port=port, debug=False,
            use_reloader=False, threaded=True),
        daemon=True,
    )
    thread.start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            import urllib.request
            urllib.request.urlopen(base + "/healthz", timeout=1).read()
            break
        except Exception:
            time.sleep(0.1)
    return base, config.DATABASE_PATH


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed, so the UI was NOT tested.")
        print("  pip install playwright && python3 -m playwright install "
              "chromium")
        return 2

    base, db_path = _serve_a_copy()

    row = sqlite3.connect(db_path).execute(
        "SELECT id FROM postings WHERE is_active=1 "
        "ORDER BY fit_score DESC LIMIT 1").fetchone()
    if not row:
        print("No postings stored — run refresh.py first.")
        return 2
    posting_id = row[0]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            _test_copy_buttons(browser, base, posting_id)
            _test_pages_render(browser, base, posting_id)
            _test_stage_saves(browser, base)
            _test_notes_save(browser, base)
            _test_age_window_counts(browser, base)
        finally:
            browser.close()

    print()
    failed = _results.count(False)
    print(f"{len(_results)} browser checks, {failed} failed.")
    return 1 if failed else 0


def _errors_on(page):
    found = []
    page.on("pageerror", lambda e: found.append(str(e)))
    return found


def _test_copy_buttons(browser, base, posting_id):
    """
    The regression that has now shipped broken twice.

    Runs WITHOUT granting clipboard permission, because that is what a real
    browser does. Granting it hides the exact failure this must catch.
    """
    print("\nCOPY BUTTONS (no clipboard permission — as your browser)")
    url = f"{base}/prompts/{posting_id}"

    context = browser.new_context()
    page = context.new_page()
    errors = _errors_on(page)
    page.goto(url)

    for kind in ("cover", "experience"):
        expected = page.evaluate(
            f"document.getElementById('{kind}') "
            f"&& document.getElementById('{kind}').value")
        check(expected and len(expected) > 1000,
              f"{kind}: the page holds the full prompt",
              f"{len(expected or '')} chars")

        button = page.locator(f'.btn-copy[data-copy="{kind}"]')
        button.click()
        page.wait_for_timeout(800)
        label = (button.text_content() or "").strip()

        # Read the clipboard from a separate permissioned context, so the
        # page under test never gets a permission it wouldn't really have.
        reader = browser.new_context()
        reader.grant_permissions(["clipboard-read", "clipboard-write"])
        rpage = reader.new_page()
        rpage.goto(url)
        got = rpage.evaluate("navigator.clipboard.readText()")
        reader.close()

        check(got == expected,
              f"{kind}: the clipboard matches the prompt EXACTLY",
              f"{len(got)} chars copied, button said {label!r}")
        check("Copied" in label,
              f"{kind}: the button reports success only when it succeeded",
              label)

    check(not errors, "no JavaScript errors on the prep page",
          "; ".join(errors[:2]) or "none")
    context.close()


def _test_pages_render(browser, base, posting_id):
    """Every page returns something a person can use, with no JS errors."""
    print("\nPAGES RENDER")
    context = browser.new_context()
    page = context.new_page()
    errors = _errors_on(page)

    for label, path in (("dashboard", "/"),
                        ("applications", "/applications"),
                        ("prep page", f"/prompts/{posting_id}")):
        response = page.goto(base + path)
        page.wait_for_timeout(300)
        check(response.status == 200, f"{label} returns 200",
              f"HTTP {response.status}")
        body = page.locator("body").inner_text()
        check(len(body.strip()) > 200, f"{label} is not a blank page",
              f"{len(body.strip())} chars of text")

    check(not errors, "no JavaScript errors on any page",
          "; ".join(errors[:3]) or "none")
    context.close()


def _test_stage_saves(browser, base):
    """Changing a stage must persist, since it is the one irreplaceable bit."""
    print("\nAPPLICATION STAGE SAVES")
    context = browser.new_context()
    page = context.new_page()
    errors = _errors_on(page)
    page.goto(base + "/")

    select = page.locator(".stage-select").first
    if select.count() == 0:
        check(False, "a stage dropdown exists on the dashboard")
        context.close()
        return

    posting_id = select.get_attribute("data-id")
    original = select.input_value()
    target = "interview" if original != "interview" else "applied"

    select.select_option(target)
    page.wait_for_timeout(700)

    page.reload()
    page.wait_for_timeout(300)
    after = page.locator(
        f'.stage-select[data-id="{posting_id}"]').first.input_value()
    check(after == target, "a stage change survives a reload",
          f"set {target!r}, reloaded as {after!r}")

    # Put it back so the copied database ends how it started.
    page.locator(f'.stage-select[data-id="{posting_id}"]').first \
        .select_option(original)
    page.wait_for_timeout(500)

    check(not errors, "no JavaScript errors while saving",
          "; ".join(errors[:2]) or "none")
    context.close()


def _test_age_window_counts(browser, base):
    """
    The number beside each age window must equal the number of cards you
    get when you pick it.

    A filter whose own label disagrees with its result is the same class of
    problem as the filter that could never match anything: you cannot tell
    a broken page from an empty one. This is also the check that would have
    settled it faster than counting <li> tags by hand.
    """
    print("\nAGE WINDOW COUNTS")
    context = browser.new_context()
    page = context.new_page()
    errors = _errors_on(page)
    page.goto(base + "/")
    page.wait_for_timeout(300)

    options = page.evaluate("""() => {
      const sel = document.querySelector('form.filters select[name="within"]');
      if (!sel) return null;
      return Array.from(sel.options).map(o => ({
        value: o.value, label: o.textContent.trim()
      }));
    }""")

    if not options:
        check(False, "the age dropdown exists")
        context.close()
        return

    check(all("(" in o["label"] for o in options),
          "every window shows a count", f"{len(options)} windows")
    # NOT "there is no today window". That assertion was written when no
    # source published an age of 0, and it went stale the moment two
    # sources that do were added. The durable property is that a window
    # never disagrees with itself — checked per option below — and that an
    # empty one says so instead of looking like a broken page.
    check(len({o["value"] for o in options}) == len(options),
          "no two windows share a value")

    for option in options:
        promised = int(option["label"].rsplit("(", 1)[1].rstrip(")"))

        # form.filters, not just button[type=submit]: the header carries
        # "Mark all as seen" and "Refresh listings", both POST forms, and a
        # bare selector clicks the first of those instead. Every window then
        # rendered the same number and looked like an app bug.
        page.select_option('form.filters select[name="within"]',
                           option["value"])
        page.click('form.filters button[type="submit"]')
        page.wait_for_load_state("load")
        page.wait_for_timeout(200)

        shown = page.locator("li.posting").count()
        check(shown == promised,
              f"{option['label']!r} shows exactly what it promises",
              f"promised {promised}, rendered {shown}")

        if promised == 0:
            body = page.locator("body").inner_text()
            check("No postings match" in body,
                  "an empty window explains itself rather than going blank")

        page.goto(base + "/")
        page.wait_for_timeout(150)

    check(not errors, "no JavaScript errors while filtering",
          "; ".join(errors[:2]) or "none")
    context.close()


def _test_notes_save(browser, base):
    """
    Notes are the one thing here you cannot regenerate.

    Everything else in the database rebuilds from the sources in under a
    second. A recruiter's name or an OA deadline does not, so the path that
    saves it gets tested.
    """
    print("\nNOTES SAVE")
    context = browser.new_context()
    page = context.new_page()
    errors = _errors_on(page)
    page.goto(base + "/applications")
    page.wait_for_timeout(300)

    box = page.locator(".notes").first
    if box.count() == 0:
        check(False, "a notes box exists",
              "no applications recorded — apply to something first")
        context.close()
        return

    posting_id = box.get_attribute("data-id")
    original = box.input_value()
    marker = "browser-test marker 4815162342"

    box.fill(marker)
    box.blur()
    page.wait_for_timeout(700)

    page.reload()
    page.wait_for_timeout(300)
    after = page.locator(f'.notes[data-id="{posting_id}"]').first.input_value()
    check(after == marker, "a note survives a reload",
          f"reloaded as {after[:40]!r}")

    # Restore, so the copied database ends how it started.
    restore = page.locator(f'.notes[data-id="{posting_id}"]').first
    restore.fill(original)
    restore.blur()
    page.wait_for_timeout(500)

    check(not errors, "no JavaScript errors while saving a note",
          "; ".join(errors[:2]) or "none")
    context.close()


if __name__ == "__main__":
    sys.exit(main())
