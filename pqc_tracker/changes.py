"""Meaningful-change detection and append-only history events."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

FIELD_TYPES = {
    "publication": "publication_changed",
    "lifecycle": "lifecycle_changed",
    "maturity": "maturity_changed",
    "version": "version_changed",
    "intended_level": "intended_level_changed",
    "rfc_number": "rfc_published",
    "obsoletes": "relationship_changed",
    "obsoleted_by": "relationship_changed",
    "updates": "relationship_changed",
    "updated_by": "relationship_changed",
    "replaced_by": "relationship_changed",
}


def tracked_view(item: dict[str, Any]) -> dict[str, Any]:
    """Flat dict of only the fields whose change is considered meaningful.

    Timestamps, expiry dates, reordered lists and other metadata churn are
    deliberately excluded.
    """
    d = item.get("derived") or {}
    facts = item.get("facts") or {}
    rfc, draft = facts.get("rfc") or {}, facts.get("draft") or {}
    return {
        "publication": d.get("publication"),
        "lifecycle": d.get("lifecycle"),
        "maturity": d.get("maturity"),
        "version": d.get("version"),
        "intended_level": d.get("intended_level"),
        "rfc_number": rfc.get("number"),
        "obsoletes": rfc.get("obsoletes", []),
        "obsoleted_by": rfc.get("obsoleted_by", []),
        "updates": rfc.get("updates", []),
        "updated_by": rfc.get("updated_by", []),
        "replaced_by": draft.get("replaced_by", []),
    }


def _event_id(item: str, field: str, prev: Any, new: Any, at: str) -> str:
    blob = json.dumps([item, field, prev, new, at], sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def diff_events(
    item_id: str, old: dict[str, Any] | None, new: dict[str, Any], at: str, url: str
) -> list[dict[str, Any]]:
    """Events for one item. `old` None/unverified means first observation."""
    new_view = tracked_view(new)
    if old is None or not old.get("facts"):
        return [
            {
                "id": _event_id(item_id, "added", None, new_view["lifecycle"], at),
                "detected_at": at,
                "item": item_id,
                "type": "tracking_started",
                "field": "added",
                "previous": None,
                "new": f"{new_view['publication']} / {new_view['lifecycle']} / {new_view['maturity']}",
                "source_url": url,
            }
        ]
    old_view = tracked_view(old)
    events = []
    for field, ftype in FIELD_TYPES.items():
        if old_view[field] != new_view[field]:
            events.append(
                {
                    "id": _event_id(item_id, field, old_view[field], new_view[field], at),
                    "detected_at": at,
                    "item": item_id,
                    "type": ftype,
                    "field": field,
                    "previous": old_view[field],
                    "new": new_view[field],
                    "source_url": url,
                }
            )
    return events


def read_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError as exc:
            raise ValueError(f"{path}:{n}: invalid JSON line: {exc}") from exc
    return out


def append_history(path: Path, events: list[dict[str, Any]]) -> int:
    """Append events not already present (by id). Returns number written."""
    existing = {e.get("id") for e in read_history(path)}
    fresh = [e for e in events if e["id"] not in existing]
    if fresh:
        with path.open("a", encoding="utf-8") as fh:
            for e in fresh:
                fh.write(json.dumps(e, sort_keys=True, ensure_ascii=True) + "\n")
    return len(fresh)
