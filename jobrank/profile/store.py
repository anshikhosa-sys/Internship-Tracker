"""
Profiles on disk: `profiles/<user_id>.json`.

One document per user holding the three layers separately —

    resume       what extraction produced (re-derivable from the résumé file)
    derived      what the scorer reads (re-derivable from the two above)

Keeping the inputs means a change to derivation rules re-derives every profile
without asking anyone to re-upload. User ids are validated against a strict
pattern before touching the filesystem, so an id can never escape the folder.
"""

from __future__ import annotations

import json
import os
import re
import tempfile

from jobrank.config import profile as cfg
from jobrank.models import ParsedResume, Profile
from jobrank.profile.derive import derive


class ProfileNotFound(LookupError):
    pass


def validate_user_id(user_id: str) -> str:
    if not isinstance(user_id, str) or not re.fullmatch(cfg.USER_ID_PATTERN, user_id):
        raise ValueError("Profile id must be 1-40 lowercase letters, digits, '-' or '_', starting with a letter or digit.")
    return user_id


def path_for(user_id: str, directory: str | None = None) -> str:
    return os.path.join(directory or cfg.PROFILES_DIR, f"{validate_user_id(user_id)}.json")


def save(user_id: str, resume: ParsedResume, directory: str | None = None) -> Profile:
    profile = derive(user_id, resume)
    document = {
        "schema_version": cfg.PROFILE_SCHEMA_VERSION,
        "user_id": user_id,
        "resume": resume.to_dict(),
        "derived": profile.to_dict(),
    }
    path = path_for(user_id, directory)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".profile-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)
    return profile


def load_document(user_id: str, directory: str | None = None) -> dict:
    path = path_for(user_id, directory)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise ProfileNotFound(f"No profile '{user_id}'. Create one with: python3 run.py profile create "
                              f"--id {user_id} --resume <file>") from exc


def load(user_id: str, directory: str | None = None, rederive: bool = True) -> Profile:
    """
    The derived profile. Re-derived from stored inputs by default, so changes to
    config/profile.py or the taxonomy apply without re-saving anything.
    """
    document = load_document(user_id, directory)
    if rederive and document.get("resume"):
        return derive(user_id, ParsedResume.from_dict(document["resume"]))
    return Profile.from_dict(document["derived"])


def load_resume(user_id: str, directory: str | None = None) -> ParsedResume:
    """The résumé a profile was built from — the only input there is."""
    return ParsedResume.from_dict(load_document(user_id, directory)["resume"])


def list_ids(directory: str | None = None) -> list[str]:
    folder = directory or cfg.PROFILES_DIR
    if not os.path.isdir(folder):
        return []
    return sorted(name[:-5] for name in os.listdir(folder)
                  if name.endswith(".json") and re.fullmatch(cfg.USER_ID_PATTERN, name[:-5]))
