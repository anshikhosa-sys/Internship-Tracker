"""
letters.py — drafts a tailored application packet for one posting.

For a given job posting it produces four things, using your profile.md:

    cover_letter    a draft, in your voice, citing specific work
    talking_points  bullets to reuse in the application's free-text fields
    gaps            what the role wants that your profile doesn't show
    fit_summary     one honest line on how well you actually match

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not submit anything. Every application portal's terms prohibit
automated submission, and being flagged doesn't fail one application — it can
follow your email address across every company using that ATS. More
importantly, an application can't be unsent. A draft you skim for ten seconds
before pasting has a recoverable failure mode; an auto-submitted one doesn't.

So this generates, you review, you send.

THE RULE THAT MATTERS MOST
--------------------------
The model is instructed to use ONLY what appears in profile.md, and to report
gaps rather than paper over them. A cover letter that invents a credential is
far worse than no cover letter: it goes out under your real name, and if it
gets caught in an interview you can't walk it back. That constraint is in the
system prompt, and the `gaps` field exists so the model has an honest place to
put "the posting wants X and you don't have it" instead of quietly inventing X.
"""

import json
import os
import re

import config

# The SDK is imported lazily inside _client() so that importing this module —
# which app.py does at startup — never fails just because the API key isn't
# set yet. You should be able to browse the dashboard without credentials.


class LetterError(Exception):
    """Something went wrong that the user needs to read and act on."""


# =============================================================================
# Loading your profile
# =============================================================================

def load_profile() -> str:
    """
    Read profile.md.

    Raises LetterError with instructions rather than a bare FileNotFoundError,
    because "no such file: profile.md" doesn't tell you what to do about it.
    """
    path = config.PROFILE_PATH
    if not os.path.exists(path):
        raise LetterError(
            f"No {path} found.\n\n"
            f"Create it from the template:\n"
            f"    cp profile_example.md {path}\n\n"
            f"Then fill in your own details. It is gitignored, so it\n"
            f"stays local."
        )

    with open(path, encoding="utf-8") as handle:
        text = handle.read().strip()

    if len(text) < 200:
        raise LetterError(
            f"{path} looks empty or barely filled in. The letters are only as "
            f"specific as this file is — fill in your real experience first."
        )

    return text


# =============================================================================
# Talking to Claude
# =============================================================================

def _client():
    """
    Build the API client, with a readable error if credentials are missing.

    The SDK resolves credentials from ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
    or a saved `ant auth login` profile — so we don't check for the env var
    ourselves. We just let the SDK try, and translate its failure into
    something actionable.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise LetterError(
            "The anthropic package isn't installed. Run:\n"
            "    .venv/bin/pip install -r requirements.txt"
        ) from exc

    try:
        return anthropic.Anthropic()
    except Exception as exc:
        raise LetterError(
            "Couldn't create the Claude client — usually a missing key.\n\n"
            "Get one at https://console.anthropic.com/settings/keys:\n"
            "    export ANTHROPIC_API_KEY='sk-ant-...'\n\n"
            "To make it permanent, add that line to ~/.zshrc.\n"
            f"\n(underlying error: {exc})"
        ) from exc


SYSTEM_PROMPT = """\
You are helping a candidate prepare a job application. You write in their \
voice, for them to review and send themselves.

THE ABSOLUTE RULE: use only what appears in the candidate profile you are \
given. Never invent, embellish, or imply experience, coursework, employers, \
metrics, or skills that are not stated there. If the posting asks for \
something the profile does not show, that belongs in `gaps` — never papered \
over in the letter. A letter that overstates goes out under a real person's \
name and can be caught in an interview; an honest one that names fewer \
matches is strictly better.

Writing the cover letter:
- 200-300 words. Three or four short paragraphs. No postal-address header, no \
"To Whom It May Concern".
- Open with why this specific role and company, not a summary of the resume.
- The middle must cite CONCRETE work from the profile — name the system, the \
technical problem, the result. Generic enthusiasm is filler; cut it.
- Choose the one or two experiences that genuinely match THIS posting. Do not \
inventory everything the candidate has done.
- Plain, direct, human. No "I am writing to express my keen interest", no \
"passionate about leveraging", no "thrilled at the opportunity". Contractions \
are fine. Read it back and cut any sentence that survives being deleted.
- Do not fill in details you weren't given: no hiring manager's name, no \
invented company facts. If the company's specific work isn't in the posting, \
speak to the role instead of guessing.

talking_points: 3-5 reusable bullets for free-text application fields ("why \
this company", "relevant experience"). Each self-contained and specific.

gaps: what the posting asks for that the profile does not demonstrate. For \
each, an honest suggestion for addressing it — a related experience to lean \
on, or an admission it's a genuine gap. Empty list only if there really are \
none.

fit_summary: one sentence, honest. If this is a weak match, say so plainly — \
the candidate uses this to decide where to spend their time."""


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "cover_letter": {
            "type": "string",
            "description": "The draft letter, 200-300 words.",
        },
        "talking_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-5 reusable bullets for application fields.",
        },
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "how_to_address": {"type": "string"},
                },
                "required": ["requirement", "how_to_address"],
                "additionalProperties": False,
            },
        },
        "fit_summary": {"type": "string"},
    },
    "required": ["cover_letter", "talking_points", "gaps", "fit_summary"],
    "additionalProperties": False,
}


def _build_prompt(posting: dict, profile: str) -> str:
    """Assemble the user message: the posting, then the candidate profile."""
    reasons = posting.get("score_reasons") or []
    why = "\n".join(
        f"  {r['points']:+d}  {r['label']}" for r in reasons
    ) or "  (none recorded)"

    return f"""\
Here is the job posting.

  Company:   {posting['company']}
  Role:      {posting['role']}
  Category:  {posting['category']}
  Location:  {posting['location']}
  Link:      {posting.get('apply_url', '')}

The candidate's own ranking tool scored this {posting['fit_score']}, for these
reasons:

{why}

Those reasons are keyword matches, not judgment — treat them as a hint about
why this role surfaced, not as facts about the candidate.

NOTE: only the job title and category are available, not the full job
description. Write from what the title implies about the role, and don't
invent specific requirements the posting never stated.

--- CANDIDATE PROFILE (the only source of facts about them) ---

{profile}

--- END PROFILE ---

Draft the application packet."""


def generate(posting: dict, profile: str = None) -> dict:
    """
    Generate a packet for one posting. Returns the parsed dict.

    Raises LetterError on anything the user needs to fix.
    """
    profile = profile or load_profile()
    client = _client()

    try:
        response = client.messages.create(
            model=config.LETTER_MODEL,
            max_tokens=config.LETTER_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            # Adaptive thinking: choosing WHICH two experiences match a given
            # posting, and being honest about gaps, is a judgment task — worth
            # letting the model reason before it writes.
            thinking={"type": "adaptive"},
            output_config={
                "effort": config.LETTER_EFFORT,
                # A JSON schema guarantees we get back the four fields we
                # need, rather than prose we'd have to parse with regexes.
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            messages=[
                {"role": "user", "content": _build_prompt(posting, profile)}
            ],
        )
    except Exception as exc:
        raise LetterError(_explain_api_error(exc)) from exc

    # A refusal is a real outcome, not an exception — check before reading.
    if response.stop_reason == "refusal":
        raise LetterError(
            "Claude declined to draft this one. That's unusual for a job "
            "application — check the posting for anything odd."
        )

    text = next(
        (b.text for b in response.content if b.type == "text"), None
    )
    if not text:
        raise LetterError("Claude returned no text. Try again.")

    try:
        packet = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LetterError(
            f"Couldn't parse the response as JSON: {exc}"
        ) from exc

    packet["model"] = config.LETTER_MODEL
    return packet


MISSING_KEY_HELP = (
    "No Anthropic API key found.\n\n"
    "1. Get a key at https://console.anthropic.com/settings/keys\n"
    "2. Add this line to ~/.zshrc:\n"
    "       export ANTHROPIC_API_KEY='sk-ant-...'\n"
    "3. Restart the dashboard so it picks up the new value:\n"
    "       ./scripts/schedule.sh restart\n\n"
    "The key is read from the environment and never stored in this repo."
)


def _explain_api_error(exc) -> str:
    """Turn an SDK exception into something worth reading."""
    import anthropic

    # Missing credentials surface as a plain TypeError from the SDK's auth
    # resolution — NOT as anthropic.AuthenticationError, which only fires
    # when a key exists and the server rejects it. Checked first, because
    # this is the error a new setup actually hits.
    if isinstance(exc, TypeError) and "authentication" in str(exc).lower():
        return MISSING_KEY_HELP

    if isinstance(exc, anthropic.AuthenticationError):
        return (
            "Your API key was rejected.\n\n"
            "Check it at https://console.anthropic.com/settings/keys, then:\n"
            "    export ANTHROPIC_API_KEY='sk-ant-...'"
        )
    if isinstance(exc, anthropic.RateLimitError):
        return "Rate limited by the API. Wait a moment and try again."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Couldn't reach the API. Check your internet connection."
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return f"The API had a server error ({exc.status_code}). Retry."
        if exc.status_code == 400 and "credit" in str(exc).lower():
            return (
                "Your Anthropic account is out of credit. Add some at "
                "https://console.anthropic.com/settings/billing"
            )
        return f"API error {exc.status_code}: {exc}"
    return f"Unexpected error: {exc}"


# =============================================================================
# Saving drafts to disk
# =============================================================================

def _safe_filename(text: str) -> str:
    """Turn a company/role into something safe to use as a filename."""
    slug = re.sub(r"[^\w\s-]", "", text).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug[:60] or "untitled"


def save_markdown(posting: dict, packet: dict) -> str:
    """
    Write the packet to letters/ as readable markdown, and return the path.

    The database is the source of truth; this is for reading, editing, and
    copy-pasting. letters/ is gitignored — these are written in your name.
    """
    os.makedirs(config.LETTERS_DIR, exist_ok=True)

    name = f"{_safe_filename(posting['company'])}-" \
           f"{_safe_filename(posting['role'])}.md"
    path = os.path.join(config.LETTERS_DIR, name)

    gaps = "\n".join(
        f"- **{g['requirement']}** — {g['how_to_address']}"
        for g in packet.get("gaps", [])
    ) or "- None identified."

    points = "\n".join(
        f"- {p}" for p in packet.get("talking_points", [])
    ) or "- None."

    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"""# {posting['company']} — {posting['role']}

**Fit score:** {posting['fit_score']}
**Location:** {posting['location']}
**Apply:** {posting.get('apply_url', '')}

> {packet.get('fit_summary', '')}

---

## Cover letter

{packet.get('cover_letter', '')}

---

## Talking points

{points}

---

## Gaps to be ready for

{gaps}

---

*Draft written by {packet.get('model', 'Claude')} from your profile.md.
Read it before you send it — it's going out in your name.*
""")

    return path
