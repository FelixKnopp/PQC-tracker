"""Status model: derives classifications from verified facts.

Three independent dimensions are kept separate and never merged:

* publication  - what kind of document it is (Internet-Draft, RFC, NIST pub)
* lifecycle    - the source's own process state (Datatracker states / NIST status)
* maturity     - the RFC Editor standards designation (RFCs only)
"""

from __future__ import annotations

from typing import Any

UNVERIFIED = "Unknown or unverified"
NOT_APPLICABLE = "Not applicable"

# indicator key -> (emoji, text label). The label must always accompany the emoji.
INDICATORS = {
    "internet-standard": ("🟢", "Internet Standard"),
    "proposed-standard": ("🔵", "Proposed Standard"),
    "other-rfc": ("🟣", "Other published RFC"),
    "active-draft": ("🟡", "Active Internet-Draft"),
    "in-review": ("🟠", "In review / awaiting publication"),
    "non-standard": ("⚪", "Non-standard-track or inactive"),
    "nist-final": ("🏛️", "NIST final publication"),
    "unverified": ("❔", "Unverified"),
    "error": ("🔴", "Source error / attention needed"),
}

# Datatracker draft-iesg slugs that are not "under review".
IESG_QUIET = {"idexists", "watching"}
IESG_INACTIVE = {"dead"}
DRAFT_INACTIVE = {"expired", "repl", "auth-rm", "ietf-rm", "withdrawn", "dead"}


def _state(states: list[dict[str, str]], stype: str) -> dict[str, str] | None:
    return next((s for s in states if s["type"] == stype), None)


def draft_lifecycle(d: dict[str, Any]) -> tuple[str, str, str]:
    """Return (label, detail, indicator) from actual Datatracker states."""
    states, tags = d["states"], d.get("tags") or []
    base = _state(states, "draft")
    iesg = _state(states, "draft-iesg")
    rfced = _state(states, "draft-rfceditor")
    stream = _state(states, "draft-stream-ietf")
    assert base is not None
    detail: list[str] = []
    if stream:
        detail.append(f"Stream state: {stream['name']}")
    if rfced:
        detail.append(f"RFC Editor: {rfced['name']}")
    if tags:
        detail.append("Tags: " + ", ".join(tags))
    d_text = "; ".join(detail)
    if base["slug"] == "rfc":
        return "Published as RFC", d_text, "other-rfc"
    if base["slug"] in DRAFT_INACTIVE:
        return base["name"], d_text, "non-standard"
    if iesg and iesg["slug"] in IESG_INACTIVE:
        return f"IESG: {iesg['name']}", d_text, "non-standard"
    if iesg and iesg["slug"] not in IESG_QUIET:
        return iesg["name"], d_text, "in-review"
    if stream:
        return f"{base['name']} ({stream['name']})", "", "active-draft"
    return base["name"], d_text, "active-draft"


def rfc_indicator(maturity: str | None) -> str:
    return {
        "Internet Standard": "internet-standard",
        "Proposed Standard": "proposed-standard",
        "Informational": "non-standard",
        "Experimental": "non-standard",
        "Historic": "non-standard",
    }.get(maturity or "", "other-rfc")


def nist_indicator(status: str) -> str:
    s = status.lower()
    if s == "final":
        return "nist-final"
    if s in {"draft", "initial public draft", "ipd"}:
        return "active-draft"
    return "non-standard"


def derive(facts: dict[str, Any]) -> dict[str, Any]:
    """Derive display classifications. Pure function of stored facts."""
    draft, rfc, nist = facts.get("draft"), facts.get("rfc"), facts.get("nist")
    warnings: list[str] = []
    out: dict[str, Any] = {
        "publication": UNVERIFIED,
        "lifecycle": UNVERIFIED,
        "lifecycle_detail": "",
        "maturity": UNVERIFIED,
        "intended_level": None,
        "document": None,
        "document_url": None,
        "version": None,
        "indicator": "unverified",
        "warnings": warnings,
    }
    if rfc:
        mat = rfc["status"] or f"Other ({rfc['status_raw']})"
        if rfc["status"] is None:
            warnings.append(f"Unrecognized RFC Editor status {rfc['status_raw']!r}")
        dt_level = rfc.get("datatracker_std_level")
        if dt_level and rfc["status"] and dt_level != rfc["status"]:
            warnings.append(
                f"Maturity mismatch: RFC Editor says {rfc['status']}, Datatracker says {dt_level}"
            )
        out.update(
            publication="Published RFC",
            lifecycle="Published",
            maturity=mat,
            document=f"RFC {rfc['number']}",
            document_url=rfc["url"],
            indicator=rfc_indicator(rfc["status"]),
            version=str(rfc["number"]),
        )
        rel = []
        if rfc["obsoleted_by"]:
            rel.append("Obsoleted by " + ", ".join(rfc["obsoleted_by"]))
        if rfc["updated_by"]:
            rel.append("Updated by " + ", ".join(rfc["updated_by"]))
        out["lifecycle_detail"] = "; ".join(rel)
        if draft:
            out["intended_level"] = draft.get("intended_level")
        return out
    if draft:
        label, detail, ind = draft_lifecycle(draft)
        base = _state(draft["states"], "draft")
        if base and base["slug"] == "rfc":
            warnings.append("Draft is marked as published RFC but the RFC number is unresolved")
        out.update(
            publication="Internet-Draft",
            lifecycle=label,
            lifecycle_detail=detail,
            maturity=NOT_APPLICABLE,
            intended_level=draft.get("intended_level"),
            document=f"{draft['name']}-{draft['rev']}",
            document_url=draft["url"],
            version=draft["rev"],
            indicator=ind,
        )
        if draft["replaced_by"]:
            out["lifecycle_detail"] = (
                (out["lifecycle_detail"] + "; " if out["lifecycle_detail"] else "")
                + "Replaced by "
                + ", ".join(draft["replaced_by"])
            )
        return out
    if nist:
        out.update(
            publication="NIST publication (not an IETF RFC)",
            lifecycle=nist["status"],
            maturity=NOT_APPLICABLE,
            document=nist["edition"],
            document_url=nist["url"],
            version=nist["edition"],
            indicator=nist_indicator(nist["status"]),
        )
    return out
