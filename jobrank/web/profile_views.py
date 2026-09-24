"""
Profile intake in the browser: upload a résumé. That is the whole form.

The platform deliberately does not ask what kind of job you want. Scoring is
built entirely from what the résumé proves and what the market currently
demands, so a stated target would be a number with no evidence behind it —
and, measured on a real golden set, it reordered results against the résumé.

The uploaded file is read in memory and never written to disk; only the
parsed, contact-free profile JSON is saved.
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from jobrank.config import profile as profile_cfg
from jobrank.profile import diff as profile_diff
from jobrank.profile import store
from jobrank.resume import parser as resume_parser

bp = Blueprint("profiles", __name__)


def _form_context(user_id: str = "", errors=None, profile=None,
                  resume=None, changes=None, parse_warning=None):
    return dict(
        changes=changes,
        parse_warning=parse_warning,
        user_id=user_id,
        errors=errors or [],
        profile=profile,
        resume=resume,
        profiles=store.list_ids(),
        max_resume_bytes=profile_cfg.MAX_RESUME_BYTES,
    )


@bp.route("/profile", methods=["GET"])
def profile_form():
    user_id = request.args.get("id", "")
    if user_id:
        try:
            return render_template("profile.html", **_form_context(
                user_id, profile=store.load(user_id), resume=store.load_resume(user_id)))
        except (store.ProfileNotFound, ValueError) as exc:
            return render_template("profile.html", **_form_context(errors=[str(exc)])), 404
    return render_template("profile.html", **_form_context())


@bp.route("/profile/<user_id>/resume", methods=["POST"])
def resume_upload(user_id: str):
    """
    Replace a profile's résumé and report what changed.

    The résumé is the only input, so replacing it rebuilds the profile whole.
    Nothing is saved when the parse fails: a bad upload must not damage the
    profile already on file.
    """
    try:
        store.validate_user_id(user_id)
        previous_resume = store.load_resume(user_id)
        before = store.load(user_id)
    except (store.ProfileNotFound, ValueError) as exc:
        return render_template("profile.html", **_form_context(errors=[str(exc)])), 404

    upload = request.files.get("resume")
    if not upload or not upload.filename:
        return render_template("profile.html", **_form_context(
            user_id, ["Choose a résumé file to upload."], before, previous_resume)), 400
    try:
        resume = resume_parser.parse_bytes(upload.read(profile_cfg.MAX_RESUME_BYTES + 1), upload.filename)
    except (resume_parser.ResumeReadError, resume_parser.ResumeParseError) as exc:
        return render_template("profile.html", **_form_context(
            user_id, [f"Résumé: {exc}"], before, previous_resume)), 400

    after = store.save(user_id, resume)
    changes = profile_diff.compare(before, after, resume, previous_resume)
    warning = ("This résumé extracted far fewer skills than the previous one, which usually means it "
               "parsed badly. Check the list below — a text or Markdown export usually parses best.")
    return render_template("profile.html", **_form_context(
        user_id, profile=after, resume=resume, changes=changes,
        parse_warning=warning if changes.looks_like_a_worse_parse() else None))


@bp.route("/profile", methods=["POST"])
def profile_submit():
    user_id = (request.form.get("user_id") or "").strip()
    errors: list[str] = []
    try:
        store.validate_user_id(user_id)
    except ValueError as exc:
        errors.append(str(exc))

    upload = request.files.get("resume")
    resume = None
    if upload and upload.filename:
        try:
            resume = resume_parser.parse_bytes(upload.read(profile_cfg.MAX_RESUME_BYTES + 1), upload.filename)
        except (resume_parser.ResumeReadError, resume_parser.ResumeParseError) as exc:
            errors.append(f"Résumé: {exc}")
    elif not errors:
        try:
            resume = store.load_resume(user_id)
        except store.ProfileNotFound:
            errors.append("Upload a résumé to create a new profile.")

    if errors:
        return render_template("profile.html", **_form_context(user_id, errors)), 400

    store.save(user_id, resume)
    return redirect(url_for("profiles.profile_form", id=user_id, saved=1))
