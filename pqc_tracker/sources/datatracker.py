"""IETF Datatracker adapter (https://datatracker.ietf.org/api/v1/).

Verified against live responses: a draft's ``rfc`` field stays null after
publication; the link to the RFC is a ``became_rfc`` relateddocument and the
draft receives the draft-state ``RFC``. RFC documents are separate objects
(type ``rfc``) carrying ``std_level``.
"""

from __future__ import annotations

import re
from typing import Any

from ..http import Http, SourceError, get_json

SOURCE = "datatracker"
BASE = "https://datatracker.ietf.org"
DRAFT_RE = re.compile(r"^draft-[a-z0-9-]+$")
SLUG_RE = re.compile(r"^[a-z0-9_-]+$")

# Datatracker std level slugs, aligned to RFC Editor wording.
STD_LEVELS = {
    "ps": "Proposed Standard",
    "std": "Internet Standard",
    "ds": "Draft Standard",
    "bcp": "Best Current Practice",
    "inf": "Informational",
    "exp": "Experimental",
    "hist": "Historic",
    "unkn": "Unknown",
}


def draft_url(name: str) -> str:
    return f"{BASE}/doc/{name}/"


def history_url(name: str) -> str:
    return f"{BASE}/doc/{name}/history/"


class Datatracker:
    """Cached, read-only client for the handful of endpoints we need."""

    def __init__(self, http: Http) -> None:
        self.http = http
        self._cache: dict[str, Any] = {}

    def _get(self, path: str) -> Any:
        if not path.startswith("/api/v1/"):
            raise SourceError(SOURCE, f"unexpected API path: {path!r}")
        if path not in self._cache:
            sep = "&" if "?" in path else "?"
            self._cache[path] = get_json(self.http, f"{BASE}{path}{sep}format=json", SOURCE)
        return self._cache[path]

    def _resolve(self, uri: str) -> dict[str, Any]:
        obj = self._get(uri)
        if not isinstance(obj, dict):
            raise SourceError(SOURCE, f"unexpected payload for {uri}")
        return obj

    def document(self, name: str) -> dict[str, Any]:
        return self._resolve(f"/api/v1/doc/document/{name}/")

    def state(self, uri: str) -> dict[str, str]:
        obj = self._resolve(uri)
        stype = str(obj.get("type", "")).rstrip("/").rsplit("/", 1)[-1]
        slug, name = obj.get("slug"), obj.get("name")
        if not (isinstance(slug, str) and isinstance(name, str) and SLUG_RE.match(stype)):
            raise SourceError(SOURCE, f"incomplete state object {uri}")
        return {"type": stype, "slug": slug, "name": name}

    def name_of(self, uri: str) -> str:
        name = self._resolve(uri).get("name")
        if not isinstance(name, str):
            raise SourceError(SOURCE, f"incomplete name object {uri}")
        return name

    def related(self, query: str) -> list[dict[str, Any]]:
        data = self._get(f"/api/v1/doc/relateddocument/?{query}&limit=100")
        objs = data.get("objects") if isinstance(data, dict) else None
        if not isinstance(objs, list):
            raise SourceError(SOURCE, "relateddocument payload has no objects list")
        return objs


def _doc_name(uri: Any) -> str | None:
    if isinstance(uri, str) and uri.startswith("/api/v1/doc/document/"):
        return uri.rstrip("/").rsplit("/", 1)[-1]
    return None


def fetch_draft(dt: Datatracker, name: str) -> dict[str, Any]:
    """Return normalized facts for an Internet-Draft (verified fields only)."""
    if not DRAFT_RE.match(name):
        raise SourceError(SOURCE, f"invalid draft name {name!r}")
    doc = dt.document(name)
    if doc.get("name") != name:
        raise SourceError(SOURCE, f"Datatracker returned {doc.get('name')!r} for {name!r}")
    title, rev = doc.get("title"), doc.get("rev")
    if not isinstance(title, str) or not isinstance(rev, str) or not rev:
        raise SourceError(SOURCE, f"incomplete metadata for {name} (title/rev missing)")
    state_uris = doc.get("states")
    if not isinstance(state_uris, list) or not state_uris:
        raise SourceError(SOURCE, f"no states returned for {name}")
    states = sorted((dt.state(u) for u in state_uris), key=lambda s: (s["type"], s["slug"]))
    if not any(s["type"] == "draft" for s in states):
        raise SourceError(SOURCE, f"no draft-state returned for {name}")
    tags = sorted(dt.name_of(u) for u in doc.get("tags") or [])
    group = dt._resolve(doc["group"]) if isinstance(doc.get("group"), str) else {}
    intended = doc.get("intended_std_level")
    became = [
        _doc_name(o.get("target")) for o in dt.related(f"source__name={name}&relationship__slug=became_rfc")
    ]
    replaces = [
        _doc_name(o.get("target")) for o in dt.related(f"source__name={name}&relationship__slug=replaces")
    ]
    replaced_by = [
        _doc_name(o.get("source")) for o in dt.related(f"target__name={name}&relationship__slug=replaces")
    ]
    return {
        "name": name,
        "title": title,
        "rev": rev,
        "stream": dt.name_of(doc["stream"]) if isinstance(doc.get("stream"), str) else None,
        "group": group.get("acronym"),
        "group_name": group.get("name"),
        "intended_level": STD_LEVELS.get(intended.rstrip("/").rsplit("/", 1)[-1])
        if isinstance(intended, str)
        else None,
        "states": states,
        "tags": tags,
        "became_rfc": sorted(n.removeprefix("rfc") for n in became if n and re.fullmatch(r"rfc\d+", n)),
        "replaces": sorted(n for n in replaces if n),
        "replaced_by": sorted(n for n in replaced_by if n),
        "url": draft_url(name),
        "volatile": {"expires": doc.get("expires"), "doc_time": doc.get("time")},
    }


def fetch_rfc_levels(dt: Datatracker, number: int) -> dict[str, Any]:
    """Datatracker view of a published RFC (used to cross-check the RFC Editor)."""
    doc = dt.document(f"rfc{number}")
    if doc.get("rfc_number") != number:
        raise SourceError(SOURCE, f"Datatracker rfc{number}: rfc_number mismatch")
    level = doc.get("std_level")
    slug = level.rstrip("/").rsplit("/", 1)[-1] if isinstance(level, str) else None
    return {
        "std_level": STD_LEVELS.get(slug) if slug else None,
        "std_level_slug": slug,
        "group": dt._resolve(doc["group"]).get("acronym") if isinstance(doc.get("group"), str) else None,
    }
