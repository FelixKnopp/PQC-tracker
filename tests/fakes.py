"""Fake HTTP layer and builders for Datatracker/RFC Editor payloads."""

from __future__ import annotations

import json
from typing import Any

from pqc_tracker.http import SourceError

DT = "https://datatracker.ietf.org"
STATE_DEFS = {
    # id: (type, slug, name)
    1: ("draft", "active", "Active"),
    2: ("draft", "expired", "Expired"),
    3: ("draft", "rfc", "RFC"),
    4: ("draft", "repl", "Replaced"),
    10: ("draft-iesg", "idexists", "I-D Exists"),
    11: ("draft-iesg", "iesg-eva", "IESG Evaluation"),
    12: ("draft-iesg", "approved", "Approved-announcement to be sent"),
    13: ("draft-iesg", "rfcqueue", "RFC Ed Queue"),
    14: ("draft-iesg", "pub", "RFC Published"),
    20: ("draft-stream-ietf", "wg-doc", "WG Document"),
    30: ("draft-rfceditor", "in_progress", "In Progress"),
}


class FakeHttp:
    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes: dict[str, Any] = routes or {}
        self.calls: list[str] = []

    def get(self, url: str, source: str) -> bytes:
        self.calls.append(url)
        if url not in self.routes:
            raise SourceError(source, f"HTTP 404 for {url}")
        val = self.routes[url]
        if isinstance(val, Exception):
            raise val
        return val if isinstance(val, bytes) else json.dumps(val).encode()

    def fail_all(self, host: str, message: str = "timeout") -> None:
        for k in list(self.routes):
            if host in k:
                self.routes[k] = SourceError("x", message)


def _j(path: str) -> str:
    return f"{DT}{path}{'&' if '?' in path else '?'}format=json"


def add_draft(
    http: FakeHttp,
    name: str,
    states: list[int],
    rev: str = "01",
    tags: tuple[str, ...] = (),
    became: str | None = None,
    replaced_by: str | None = None,
    intended: str = "ps",
) -> None:
    r = http.routes
    r[_j(f"/api/v1/doc/document/{name}/")] = {
        "name": name,
        "title": f"Title of {name}",
        "rev": rev,
        "states": [f"/api/v1/doc/state/{s}/" for s in states],
        "tags": [f"/api/v1/name/doctagname/{t}/" for t in tags],
        "group": "/api/v1/group/group/1/",
        "stream": "/api/v1/name/streamname/ietf/",
        "intended_std_level": f"/api/v1/name/intendedstdlevelname/{intended}/",
        "expires": "2027-01-01T00:00:00Z",
        "time": "2026-01-01T00:00:00Z",
        "rfc": None,
    }
    for sid in states:
        t, slug, nm = STATE_DEFS[sid]
        r[_j(f"/api/v1/doc/state/{sid}/")] = {
            "type": f"/api/v1/doc/statetype/{t}/", "slug": slug, "name": nm,
        }  # fmt: skip
    for t in tags:
        r[_j(f"/api/v1/name/doctagname/{t}/")] = {"name": f"Tag {t}", "slug": t}
    r[_j("/api/v1/group/group/1/")] = {"acronym": "wg", "name": "Some WG"}
    r[_j("/api/v1/name/streamname/ietf/")] = {"name": "IETF"}
    q = "/api/v1/doc/relateddocument/?"
    empty = {"objects": [], "meta": {}}
    r[_j(f"{q}source__name={name}&relationship__slug=became_rfc&limit=100")] = (
        {"objects": [{"target": f"/api/v1/doc/document/{became}/"}]} if became else empty
    )
    r[_j(f"{q}source__name={name}&relationship__slug=replaces&limit=100")] = empty
    r[_j(f"{q}target__name={name}&relationship__slug=replaces&limit=100")] = (
        {"objects": [{"source": f"/api/v1/doc/document/{replaced_by}/"}]} if replaced_by else empty
    )


def add_rfc(
    http: FakeHttp,
    number: int,
    status: str = "PROPOSED STANDARD",
    dt_level: str = "ps",
    draft: str | None = None,
    **rel: list[str],
) -> None:
    body = {
        "doc_id": f"RFC{number}",
        "title": f"RFC {number} title",
        "status": status,
        "pub_status": status,
        "pub_date": "August 2026",
        "source": "Some WG",
        "draft": f"{draft}-03" if draft else None,
        "obsoletes": [], "obsoleted_by": [], "updates": [], "updated_by": [],
        "errata_url": None,
    }  # fmt: skip
    body.update(rel)
    http.routes[f"https://www.rfc-editor.org/rfc/rfc{number}.json"] = body
    http.routes[_j(f"/api/v1/doc/document/rfc{number}/")] = {
        "name": f"rfc{number}",
        "rfc_number": number,
        "std_level": f"/api/v1/name/stdlevelname/{dt_level}/",
        "group": "/api/v1/group/group/1/",
    }
    http.routes[_j("/api/v1/group/group/1/")] = {"acronym": "wg", "name": "Some WG"}
