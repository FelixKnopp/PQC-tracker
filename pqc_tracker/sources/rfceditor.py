"""RFC Editor adapter using the per-RFC JSON (https://www.rfc-editor.org/rfc/rfcN.json).

``rfc-index.json`` does not exist (HTTP 404 when checked); the per-RFC JSON is
the structured interface. Its ``status`` field is the maturity designation.
"""

from __future__ import annotations

import re
from typing import Any

from ..http import Http, SourceError, get_json

SOURCE = "rfc-editor"
REF_RE = re.compile(r"^(RFC|BCP|STD|FYI)\s?0*(\d+)$", re.I)

KNOWN_STATUS = {
    "INTERNET STANDARD": "Internet Standard",
    "DRAFT STANDARD": "Draft Standard",
    "PROPOSED STANDARD": "Proposed Standard",
    "BEST CURRENT PRACTICE": "Best Current Practice",
    "INFORMATIONAL": "Informational",
    "EXPERIMENTAL": "Experimental",
    "HISTORIC": "Historic",
    "UNKNOWN": "Unknown",
}


def json_url(number: int) -> str:
    return f"https://www.rfc-editor.org/rfc/rfc{number}.json"


def info_url(number: int) -> str:
    return f"https://www.rfc-editor.org/info/rfc{number}"


def normalize_refs(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise SourceError(SOURCE, f"field {field!r} is not a list")
    out = []
    for item in value:
        m = REF_RE.match(item.strip()) if isinstance(item, str) else None
        if not m:
            raise SourceError(SOURCE, f"unparseable reference in {field!r}: {item!r}")
        out.append(f"{m.group(1).upper()}{int(m.group(2))}")
    return sorted(set(out), key=lambda r: (r[:3], int(r[3:] or 0)))


def parse_rfc(data: Any, number: int) -> dict[str, Any]:
    """Validate and normalize one RFC Editor JSON document."""
    if not isinstance(data, dict):
        raise SourceError(SOURCE, "payload is not an object")
    if str(data.get("doc_id", "")).upper() != f"RFC{number}":
        raise SourceError(SOURCE, f"doc_id {data.get('doc_id')!r} does not match RFC{number}")
    title = data.get("title")
    raw_status = data.get("status")
    if not isinstance(title, str) or not title:
        raise SourceError(SOURCE, f"RFC{number}: title missing")
    if not isinstance(raw_status, str) or not raw_status:
        raise SourceError(SOURCE, f"RFC{number}: status missing")
    status = KNOWN_STATUS.get(raw_status.strip().upper())
    draft = data.get("draft")
    draft_base = re.sub(r"-\d{2}$", "", draft) if isinstance(draft, str) else None
    return {
        "number": number,
        "title": title,
        "status": status,
        "status_raw": raw_status,  # kept so unrecognized values stay visible
        "pub_status": data.get("pub_status"),
        "pub_date": data.get("pub_date") if isinstance(data.get("pub_date"), str) else None,
        "source_group": data.get("source") if isinstance(data.get("source"), str) else None,
        "draft": draft_base if draft_base and draft_base.startswith("draft-") else None,
        "obsoletes": normalize_refs(data.get("obsoletes", []), "obsoletes"),
        "obsoleted_by": normalize_refs(data.get("obsoleted_by", []), "obsoleted_by"),
        "updates": normalize_refs(data.get("updates", []), "updates"),
        "updated_by": normalize_refs(data.get("updated_by", []), "updated_by"),
        "has_errata": bool(data.get("errata_url")),
        "url": info_url(number),
        "doi": data.get("doi") if isinstance(data.get("doi"), str) else None,
    }


def fetch_rfc(http: Http, number: int) -> dict[str, Any]:
    return parse_rfc(get_json(http, json_url(number), SOURCE), number)
