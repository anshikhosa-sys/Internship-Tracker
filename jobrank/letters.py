"""
letters.py — builds copy-paste prompts for job applications.

Two kinds, both free:

    cover_letter    a tailored letter for one posting
    work_experience bullets rewritten for that role's audience, for the
                    "describe your relevant experience" boxes

NOTHING HERE CALLS AN API OR COSTS MONEY. It assembles text. You copy it into
claude.ai, ChatGPT, or whatever you already use, and paste the result back.

THE IDEA THAT MAKES THESE GOOD
------------------------------
A cover letter for a forward-deployed role and one for a backend SWE role are
not the same document, even from the same person with the same résumé. An FDE
reviewer is scanning for evidence you can sit with a customer and handle
ambiguity. A SWE reviewer wants depth on the hardest thing you've shipped. The
same internal-tools project is the lead story for one and a footnote for the other.

So the prompt adapts. Every posting's title classifies to a taxonomy role
family, and that family selects a block of guidance from
config.ROLE_FAMILY_GUIDANCE about what this audience is actually reading for.

THE RULE THAT MATTERS MOST
--------------------------
The prompt tells the model to use ONLY what's in profile.md, and to report
gaps rather than paper over them. A letter that invents a credential is far
worse than a plain one: it goes out under your real name, and if it surfaces
in an interview you can't walk it back.
"""


from jobrank import config


class LetterError(Exception):
    """Something the user needs to read and fix."""


# =============================================================================
# Loading your profile
# =============================================================================

def load_profile(user_id: str | None = None) -> str:
    """
    The candidate's parsed résumé, formatted for a prompt.

    Built from the stored profile rather than a raw file, so the prompt
    contains exactly what extraction found — the same corpus tailoring is
    validated against — and never contact details.
    """
    from jobrank.profile import active, store

    user_id = active.resolve(user_id)
    if not user_id:
        raise LetterError("No profile exists yet. Create one at /profile or with:\n"
                          "    python3 run.py profile create --id <id> --resume <file>")
    try:
        resume = store.load_resume(user_id)
    except store.ProfileNotFound as exc:
        raise LetterError(str(exc)) from exc
    text = format_resume(resume)
    if len(text) < 200:
        raise LetterError(f"Profile '{user_id}' has almost no experience in it. These prompts are only "
                          "as specific as the résumé — upload a fuller one first.")
    return text


def format_resume(resume) -> str:
    lines = []
    if resume.education:
        lines.append("EDUCATION")
        for e in resume.education:
            when = f", graduating {e.graduation}" if e.graduation else ""
            lines.append(f"- {e.degree or e.level} — {e.institution}{when}")
    if resume.experience:
        lines.append("\nEXPERIENCE")
        for x in resume.experience:
            span = f"{x.start or '?'} to {'present' if x.current else (x.end or '?')}"
            lines.append(f"{x.title} — {x.organization} ({span})")
            lines.extend(f"- {b}" for b in x.bullets)
    if resume.projects:
        lines.append("\nPROJECTS")
        for p in resume.projects:
            tech = f" [{', '.join(p.technologies)}]" if p.technologies else ""
            lines.append(f"{p.name}{tech}")
            if p.description:
                lines.append(f"- {p.description}")
    if resume.skills or resume.other_skills:
        lines.append("\nSKILLS")
        lines.append(", ".join(resume.skills + resume.other_skills))
    return "\n".join(lines)


def _guidance(role_family: str) -> dict:
    """What this kind of role's reviewer is actually reading for."""
    key = config.GUIDANCE_FOR_FAMILY.get(role_family or "", role_family or "")
    return config.ROLE_FAMILY_GUIDANCE.get(key, config.DEFAULT_FAMILY_GUIDANCE)


def _posting_block(posting: dict) -> str:
    """The facts about the role, formatted for the prompt."""
    family = posting.get("role_family") or "not classified"
    return (
        f"  Company:    {posting['company']}\n"
        f"  Role:       {posting['role']}\n"
        f"  Category:   {posting['category']}\n"
        f"  Location:   {posting['location']}\n"
        f"  Role type:  {family}\n"
        f"  Posted:     {posting.get('age_text', 'unknown')} ago"
    )


# =============================================================================
# The shared rules
# =============================================================================

HONESTY_RULES = """\
NON-NEGOTIABLE RULES

1. Use only what the candidate profile below states. Never invent, embellish,
   or imply experience, coursework, employers, metrics, technologies, or
   skills that are not in it. If you catch yourself writing something you
   can't point to a line in the profile for, delete it.

2. Do not inflate. "Built data pipelines processing 100+ hours of video" is
   in the profile. "Architected large-scale distributed video infrastructure"
   is not — it's the same work dressed up, and it reads as dressed up.

3. Where the role clearly wants something the profile doesn't show, say so in
   the GAPS section. Never paper over it in the prose. A candidate who names
   fewer matches honestly beats one who overstates and gets caught.

4. Write like a person. Banned outright: "I am writing to express",
   "passionate about", "leverage", "synergy", "thrilled at the opportunity",
   "I believe I would be a great fit", "delve", "tapestry", "testament to".
   Contractions are fine. Short sentences are better than long ones.

5. No invented specifics about the company or the team — no hiring manager's
   name, no guesses about their tech stack or current projects. If you don't
   know it, write about the role instead."""


WHAT_WE_KNOW = """\
WHAT YOU HAVE AND DON'T HAVE

You have the job TITLE and category, not the full job description. Write from
what the title genuinely implies, and do not invent requirements that were
never stated. If the candidate pastes the real description below, use it and
prefer it over inference — mirror its actual language where it honestly
matches their experience, since a human and an applicant-tracking system will
both be scanning for those words."""


def _job_description_block(job_description: str) -> str:
    """The optional pasted job description."""
    if job_description and job_description.strip():
        return (
            "--- THE ACTUAL JOB DESCRIPTION (use this over inference) ---\n\n"
            f"{job_description.strip()}\n\n"
            "--- END JOB DESCRIPTION ---\n\n"
        )
    return (
        "(No job description was pasted. Work from the title, and stay "
        "general rather than inventing requirements.)\n\n"
    )


# =============================================================================
# Cover letter prompt
# =============================================================================

def cover_letter_prompt(posting: dict, profile: str = None,
                        job_description: str = "") -> str:
    """Build the copy-paste prompt for a cover letter."""
    profile = profile or load_profile()
    guidance = _guidance(posting.get("role_family"))

    return f"""\
You are helping a candidate write a cover letter they will review, edit, and
send themselves. Write in their voice — first person, plain, specific.

{HONESTY_RULES}

WHAT THIS PARTICULAR AUDIENCE CARES ABOUT

{guidance['emphasis']}

{WHAT_WE_KNOW}

HOW TO STRUCTURE IT

- 200-300 words. Three or four short paragraphs. No address header, no
  "To Whom It May Concern", no signature block.
- Open with something specific about this role — a reason this particular
  job is interesting. Not a summary of the resume, and not flattery.
- The middle carries the weight: pick the ONE or TWO experiences from the
  profile that genuinely match this posting and go concrete on them. Name the
  system, the problem, what changed. Resist listing everything they've done —
  a letter that covers three projects shallowly is worse than one that covers
  one properly.
- Close briefly. One or two sentences. No restating the whole letter.
- Then read it back and delete every sentence that would not be missed.

ALSO PRODUCE

TALKING POINTS: 3-5 short bullets reusable in application form fields like
"why this company" or "relevant experience". Each self-contained, each
specific enough that it couldn't be pasted into a different application
unchanged.

GAPS: what this role likely wants that the profile doesn't demonstrate. For
each, either the closest honest thing the candidate can point to, or a plain
statement that it's a real gap. This is for their own preparation — being
blindsided in an interview is worse than knowing in advance.

FIT CHECK: one honest sentence on whether this is worth applying to. If it's
a weak match, say so — their time is finite and a frank answer is more useful
than encouragement.

===============================================================================
THE POSTING
===============================================================================

{_posting_block(posting)}

{_job_description_block(job_description)}\
===============================================================================
THE CANDIDATE PROFILE — the only source of facts about them
===============================================================================

{profile}

===============================================================================

Write the cover letter, then the talking points, then the gaps, then the fit
check."""


# =============================================================================
# Work experience prompt
# =============================================================================

def work_experience_prompt(posting: dict, profile: str = None,
                           job_description: str = "") -> str:
    """
    Build the copy-paste prompt for rewriting work experience for one role.

    Many applications ask you to describe relevant experience in a text box,
    separately from any resume upload. Pasting the same résumé bullets every
    time wastes the one chance to speak directly to what this reviewer wants.
    """
    profile = profile or load_profile()
    guidance = _guidance(posting.get("role_family"))

    return f"""\
You are helping a candidate fill in the "relevant work experience" section of
a job application — the free-text box, not a resume upload. They will review
and edit whatever you produce.

{HONESTY_RULES}

WHAT THIS PARTICULAR AUDIENCE CARES ABOUT

{guidance['emphasis']}

HOW TO FRAME THE EXPERIENCE FOR THIS ROLE

{guidance['experience_framing']}

{WHAT_WE_KNOW}

WHAT TO PRODUCE

1. A SHORT VERSION (about 80 words, one paragraph). Many forms have a tight
   character limit. This is the version that has to survive one.

2. A FULL VERSION: the candidate's roles and projects, rewritten as bullets
   aimed at THIS posting. Rules:
   - Keep every real number and system name from the profile. The specifics
     are the evidence; generalizing them destroys the value.
   - Reorder so the most relevant experience is first. Relevance to this
     posting decides the order, not chronology.
   - Cut what doesn't serve this application. A shorter, sharper set beats a
     complete one.
   - Rewrite the emphasis, never the facts. Same work, aimed differently.

3. WHAT I CHANGED AND WHY: two or three lines telling the candidate what you
   reordered or reframed for this role. They should understand the pitch
   they're making, not just paste it.

===============================================================================
THE POSTING
===============================================================================

{_posting_block(posting)}

{_job_description_block(job_description)}\
===============================================================================
THE CANDIDATE PROFILE — the only source of facts about them
===============================================================================

{profile}

===============================================================================

Produce the short version, then the full version, then what you changed."""
