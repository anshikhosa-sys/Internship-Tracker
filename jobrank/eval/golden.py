"""
Golden relevance labels: `eval/golden/<profile_id>.jsonl`, one label per line.

    {"posting_id": "job:…", "relevance": 2, "company": "Acme", "role": "SWE Intern",
     "source": "application:applied", "labeled_at": "2026-09-15T…", "note": ""}

Company and role are stored with each label so a label is still readable (and
re-attachable) after its posting drops off every source.

Seeding from applications: a user's own applications are revealed preference —
they read the posting and chose to spend an application on it. Interview or
offer stages are stronger evidence than a plain application. Manual labels
always win over seeded ones, so a user can correct a regretted application to
relevance 0.

KNOWN BIAS: applications were chosen while browsing an earlier ranking, so the
seeded set over-represents what that ranking surfaced. Manual labels from the
`label queue` command (top unlabeled postings of the CURRENT ranking) are the
counterweight, and they are what make precision@k meaningful.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

GOLDEN_DIR = os.path.join("eval", "golden")

# Application stage -> relevance.
STAGE_RELEVANCE = {
    "offer": 3,
    "interview": 3,
    "oa": 3,
    "applied": 2,
    "rejected": 2,
    "ghosted": 2,
}


@dataclass
class Label:
    posting_id: str
    relevance: int
    company: str = ""
    role: str = ""
    source: str = "manual"
    labeled_at: str = ""
    note: str = ""


def path_for(profile_id: str, directory: str | None = None) -> str:
    from jobrank.profile.store import validate_user_id

    return os.path.join(directory or GOLDEN_DIR, f"{validate_user_id(profile_id)}.jsonl")


def load(profile_id: str, directory: str | None = None) -> dict[str, Label]:
    path = path_for(profile_id, directory)
    labels: dict[str, Label] = {}
    if not os.path.exists(path):
        return labels
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                label = Label(**{k: v for k, v in data.items() if k in Label.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"{path}:{line_number}: malformed label ({exc})") from exc
            if not isinstance(label.relevance, int) or not 0 <= label.relevance <= 3:
                raise ValueError(f"{path}:{line_number}: relevance must be 0-3")
            labels[label.posting_id] = label
    return labels


def save(profile_id: str, labels: dict[str, Label], directory: str | None = None) -> str:
    path = path_for(profile_id, directory)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for label in sorted(labels.values(), key=lambda l: (-l.relevance, l.company.lower(), l.role.lower())):
            handle.write(json.dumps(asdict(label), ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert(labels: dict[str, Label], posting: dict, relevance: int, source: str = "manual", note: str = "") -> Label:
    if not 0 <= relevance <= 3:
        raise ValueError("relevance must be 0-3")
    label = Label(posting_id=posting["id"], relevance=relevance, company=posting.get("company", ""),
                  role=posting.get("role", ""), source=source, labeled_at=_now(), note=note)
    labels[label.posting_id] = label
    return label


def seed_from_applications(labels: dict[str, Label], applications: list[dict]) -> int:
    """Add a label for each application not already labeled by hand. Returns how many changed."""
    changed = 0
    for app in applications:
        status = app.get("status") or ("applied" if app.get("applied") else "")
        relevance = STAGE_RELEVANCE.get(status)
        if relevance is None:
            continue
        existing = labels.get(app["id"])
        if existing and existing.source == "manual":
            continue
        if existing and existing.relevance == relevance:
            continue
        upsert(labels, app, relevance, source=f"application:{status}")
        changed += 1
    return changed
