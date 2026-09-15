"""
Profile intake in the browser: upload a résumé, state preferences.

The uploaded file is read in memory and never written to disk; only the
parsed, contact-free profile JSON is saved. Validation is the same function the
CLI uses, and every problem is shown at once.
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from jobrank.config import profile as profile_cfg
from jobrank.config import taxonomy
from jobrank.models import Preferences
from jobrank.profile import preferences as prefs_mod
from jobrank.profile import store
from jobrank.resume import parser as resume_parser

bp = Blueprint("profiles", __name__)


def _form_context(user_id: str = "", preferences: Preferences | None = None, errors=None, profile=None,
                  resume=None):
    return dict(
        user_id=user_id,
        prefs=(preferences or Preferences()),
        errors=errors or [],
        profile=profile,
        resume=resume,
        profiles=store.list_ids(),
        seniority_levels=taxonomy.SENIORITY_LEVELS,
        remote_choices=profile_cfg.REMOTE_CHOICES,
        size_choices=profile_cfg.COMPANY_SIZE_CHOICES,
        industries=taxonomy.INDUSTRIES,
        factors=profile_cfg.PRIORITY_FACTORS,
        default_priority=profile_cfg.DEFAULT_PRIORITY,
    )


@bp.route("/profile", methods=["GET"])
def profile_form():
    user_id = request.args.get("id", "")
    if user_id:
        try:
            resume, preferences = store.load_inputs(user_id)
            return render_template("profile.html", **_form_context(
                user_id, preferences, profile=store.load(user_id), resume=resume))
        except (store.ProfileNotFound, ValueError) as exc:
            return render_template("profile.html", **_form_context(errors=[str(exc)])), 404
    return render_template("profile.html", **_form_context())


@bp.route("/profile", methods=["POST"])
def profile_submit():
    user_id = (request.form.get("user_id") or "").strip()
    errors: list[str] = []
    try:
        store.validate_user_id(user_id)
    except ValueError as exc:
        errors.append(str(exc))

    raw = {
        "target_roles": request.form.get("target_roles", ""),
        "seniority": request.form.getlist("seniority"),
        "locations": request.form.get("locations", ""),
        "remote": request.form.get("remote", "any"),
        "company_sizes": request.form.getlist("company_sizes"),
        "exclude_industries": request.form.getlist("exclude_industries"),
        "earliest_start": request.form.get("earliest_start", ""),
        "priorities": {f: request.form.get(f"priority_{f}", "") for f in profile_cfg.PRIORITY_FACTORS},
    }
    preferences = None
    try:
        preferences = prefs_mod.validate(raw)
    except prefs_mod.PreferenceError as exc:
        errors.extend(exc.problems)

    upload = request.files.get("resume")
    resume = None
    if upload and upload.filename:
        try:
            resume = resume_parser.parse_bytes(upload.read(profile_cfg.MAX_RESUME_BYTES + 1), upload.filename)
        except (resume_parser.ResumeReadError, resume_parser.ResumeParseError) as exc:
            errors.append(f"Résumé: {exc}")
    elif not errors:
        try:
            resume, _ = store.load_inputs(user_id)
        except store.ProfileNotFound:
            errors.append("Upload a résumé to create a new profile.")

    if errors:
        shown = preferences or Preferences.from_dict({**raw, "priorities": {}})
        return render_template("profile.html", **_form_context(user_id, shown, errors)), 400

    store.save(user_id, resume, preferences)
    return redirect(url_for("profiles.profile_form", id=user_id, saved=1))
