"""Deterministic README generation from stored data (no network access)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from .config import CATEGORIES, Spec
from .http import ALLOWED_HOSTS
from .status import INDICATORS, UNVERIFIED
from .update import STALE_DAYS, parse_iso

RECENT_DAYS = 30
RECENT_MAX = 25
_ESC = re.compile(r"([\\`*_{}\[\]<>|#~])")


def esc(value: Any) -> str:
    """Escape untrusted text for safe inline use in Markdown (incl. tables)."""
    text = " ".join(str(value).split())
    return _ESC.sub(r"\\\1", text).replace("&", "&amp;")


def safe_url(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    p = urlparse(url)
    if p.scheme != "https" or p.hostname not in ALLOWED_HOSTS or any(c in url for c in " ()<>\"'"):
        return None
    return url


def link(label: str, url: Any) -> str:
    u = safe_url(url)
    return f"[{esc(label)}]({u})" if u else esc(label)


def badge(item: dict[str, Any] | None) -> str:
    key = (item or {}).get("derived", {}).get("indicator", "unverified")
    emoji, label = INDICATORS.get(key, INDICATORS["unverified"])
    return f"{emoji} {label}"


def freshness(item: dict[str, Any] | None, ref: datetime | None) -> tuple[str, bool]:
    """(text, needs_attention). Never presents stale data as freshly verified."""
    if not item or not item.get("last_verified"):
        return f"{INDICATORS['error'][0]} Never verified", True
    verified = item["last_verified"]
    day = verified[:10]
    problems = []
    if item.get("check_status") != "ok":
        srcs = ", ".join(sorted({e["source"] for e in item.get("errors", [])})) or "source"
        problems.append(f"latest check failed ({esc(srcs)})")
    if ref and ref - parse_iso(verified) > timedelta(days=STALE_DAYS):
        problems.append("stale")
    if item.get("derived", {}).get("warnings"):
        problems.append("data inconsistency")
    if problems:
        return f"{INDICATORS['error'][0]} Attention: {', '.join(problems)}; last verified {day}", True
    return f"✅ Verified {day}", False


def _doc_links(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    f = item.get("facts", {})
    out = []
    if f.get("rfc"):
        out.append(link("RFC Editor info", f["rfc"]["url"]))
        out.append(
            link(
                f"Datatracker (RFC {f['rfc']['number']})",
                f"https://datatracker.ietf.org/doc/rfc{f['rfc']['number']}/",
            )
        )
    if f.get("draft"):
        out.append(link("Internet-Draft", f["draft"]["url"]))
        out.append(link("History", f"https://datatracker.ietf.org/doc/{f['draft']['name']}/history/"))
    if f.get("nist"):
        out.append(link("NIST page", f["nist"]["url"]))
    return " · ".join(out)


def _maturity_cell(d: dict[str, Any]) -> str:
    m = esc(d["maturity"])
    if d.get("intended_level") and d["publication"] == "Internet-Draft":
        return f"{m} (intended: {esc(d['intended_level'])})"
    return m


def _lifecycle_cell(d: dict[str, Any]) -> str:
    t = esc(d["lifecycle"])
    return f"{t}<br><sub>{esc(d['lifecycle_detail'])}</sub>" if d["lifecycle_detail"] else t


def _row(spec: Spec, item: dict[str, Any] | None, ref: datetime | None, full: bool) -> str:
    d = (item or {}).get("derived") or {}
    fresh, _ = freshness(item, ref)
    doc = link(d["document"], d.get("document_url")) if d.get("document") else esc(UNVERIFIED)
    head = f"**{esc(spec.title)}**<br>{badge(item)}"
    if full:
        cols = [
            head,
            esc(CATEGORIES[spec.category]),
            f"{doc}<br><sub>{_doc_links(item)}</sub>",
            esc(d.get("publication", UNVERIFIED)),
            _maturity_cell(d) if d else esc(UNVERIFIED),
            _lifecycle_cell(d) if d else esc(UNVERIFIED),
            fresh,
        ]
    else:
        cols = [
            head,
            doc,
            esc(d.get("publication", UNVERIFIED)),
            _maturity_cell(d) if d else esc(UNVERIFIED),
            _lifecycle_cell(d) if d else esc(UNVERIFIED),
        ]
    return "| " + " | ".join(cols) + " |"


FULL_HEAD = "| Specification | Area | Document | Publication | Standards maturity | Lifecycle state | Last checked |\n|---|---|---|---|---|---|---|"
TOPIC_HEAD = (
    "| Specification | Document | Publication | Standards maturity | Lifecycle state |\n|---|---|---|---|---|"
)


def counts(specs: list[Spec], items: dict[str, Any], ref: datetime | None) -> dict[str, int]:
    c = dict(total=len(specs), rfc=0, ps=0, std=0, active=0, review=0, nist=0, attention=0, recent=0)
    for s in specs:
        it = items.get(s.id)
        d = (it or {}).get("derived") or {}
        pub = d.get("publication")
        if pub == "Published RFC":
            c["rfc"] += 1
            c["ps"] += d["maturity"] == "Proposed Standard"
            c["std"] += d["maturity"] == "Internet Standard"
        elif pub == "Internet-Draft":
            c["active"] += d["indicator"] == "active-draft"
            c["review"] += d["indicator"] == "in-review"
        elif pub and pub.startswith("NIST"):
            c["nist"] += 1
        c["attention"] += freshness(it, ref)[1]
    return c


def recent_events(history: list[dict[str, Any]], ref: datetime | None) -> list[dict[str, Any]]:
    if ref is None:
        return []
    cutoff = ref - timedelta(days=RECENT_DAYS)
    ev = [
        e for e in history
        if e["type"] != "tracking_started" and parse_iso(e["detected_at"]) >= cutoff
    ]  # fmt: skip
    return sorted(ev, key=lambda e: (e["detected_at"], e["id"]), reverse=True)[:RECENT_MAX]


def _val(v: Any) -> str:
    if isinstance(v, list):
        return esc(", ".join(v) if v else "none")
    return esc("—" if v is None else v)


def render(
    specs: list[Spec],
    state: dict[str, Any],
    history: list[dict[str, Any]],
    repo: str = "felixknopp/pqc-tracker",
) -> str:
    items = state.get("items", {})
    last_ok = state.get("last_successful_refresh")
    last_run = state.get("last_run")
    ref = parse_iso(last_run["at"]) if last_run else None
    c = counts(specs, items, ref)
    titles = {s.id: s.title for s in specs}
    L: list[str] = []
    L += [
        "<!-- GENERATED FILE: edit data/specifications.yml and run scripts/update.py; do not edit by hand. -->",
        "# Post-Quantum Cryptography Standards Tracker",
        "",
        f"[![Update workflow](https://github.com/{repo}/actions/workflows/update.yml/badge.svg)](https://github.com/{repo}/actions/workflows/update.yml)"
        f" [![Tests](https://github.com/{repo}/actions/workflows/tests.yml/badge.svg)](https://github.com/{repo}/actions/workflows/tests.yml)",
        "",
        "A live dashboard of the standardization status of post-quantum cryptography (PQC) specifications "
        "relevant to Internet protocols and PKI. Data is pulled automatically from the "
        "[IETF Datatracker](https://datatracker.ietf.org/), the [RFC Editor](https://www.rfc-editor.org/) "
        "and [NIST CSRC](https://csrc.nist.gov/), and this page is regenerated from it.",
        "",
        f"**Last successful data refresh:** {last_ok or 'never'} (UTC)"
        + (
            f" · **Last run:** {last_run['at']} ({'no errors' if last_run['outcome'] == 'ok' else str(last_run['errors']) + ' source error(s)'})"
            if last_run
            else ""
        ),
        "",
        "## Standards overview",
        "",
        "Counts are **unique tracked initiatives** (one row per tracked specification; a draft and the RFC it became are one initiative, counted once as an RFC).",
        "",
        "| Measure | Count |",
        "|---|---:|",
        f"| Tracked initiatives (total) | {c['total']} |",
        f"| 📄 Published RFCs | {c['rfc']} |",
        f"| 🟢 Internet Standards | {c['std']} |",
        f"| 🔵 Proposed Standards | {c['ps']} |",
        f"| 🟡 Active Internet-Drafts | {c['active']} |",
        f"| 🟠 Drafts in review / awaiting publication | {c['review']} |",
        f"| 🏛️ NIST publications (not RFCs) | {c['nist']} |",
        f"| 🔁 Items with status changes in the last {RECENT_DAYS} days | {len({e['item'] for e in recent_events(history, ref)})} |",
        f"| 🔴 Items needing attention (error / stale / inconsistent) | {c['attention']} |",
        "",
        "## All tracked specifications",
        "",
        FULL_HEAD,
    ]
    for s in specs:
        L.append(_row(s, items.get(s.id), ref, True))
    L += ["", f"## Recently changed (last {RECENT_DAYS} days)", ""]
    rec = recent_events(history, ref)
    if rec:
        L += [
            "| Detected (UTC) | Specification | Field | Previous | New | Source |",
            "|---|---|---|---|---|---|",
        ]
        for e in rec:
            L.append(
                f"| {esc(e['detected_at'])} | {esc(titles.get(e['item'], e['item']))} | {esc(e['field'])} "
                f"| {_val(e['previous'])} | {_val(e['new'])} | {link('source', e.get('source_url'))} |"
            )
    else:
        L.append("_No meaningful status changes detected in this period._")
    L += ["", "## By topic", ""]
    for key, name in CATEGORIES.items():
        group = [s for s in specs if s.category == key]
        L += [f"### {name}", ""]
        if not group:
            L += ["_No specifications tracked in this area yet._", ""]
            continue
        L.append(TOPIC_HEAD)
        L += [_row(s, items.get(s.id), ref, False) for s in group]
        L.append("")
    issues = [(s, w) for s in specs for w in (items.get(s.id, {}).get("derived") or {}).get("warnings", [])]
    errs = [(s, e) for s in specs for e in items.get(s.id, {}).get("errors", [])]
    if issues or errs:
        L += ["## Data-quality notices", ""]
        for s, e in errs:
            L.append(
                f"- 🔴 **{esc(s.title)}** — {esc(e['source'])} check failed; showing last known data. ({esc(e['message'])})"
            )
        for s, w in issues:
            L.append(f"- 🔴 **{esc(s.title)}** — {esc(w)}")
        L.append("")
    L += LEGEND.format(stale=STALE_DAYS, recent=RECENT_DAYS, repo=repo).splitlines()
    return "\n".join(L).rstrip() + "\n"


LEGEND = """
## Legend and methodology

| Indicator | Meaning |
|---|---|
| 🟢 Internet Standard | RFC with RFC Editor status *Internet Standard* |
| 🔵 Proposed Standard | RFC with RFC Editor status *Proposed Standard* |
| 🟣 Other published RFC | RFC with another designation (e.g. Draft Standard, Best Current Practice, unrecognized) |
| 🟡 Active Internet-Draft | Draft not yet in IESG/RFC Editor processing |
| 🟠 In review / awaiting publication | Draft in IESG evaluation, approved, or in the RFC Editor queue; the exact state is printed in the table |
| ⚪ Non-standard-track or inactive | Informational, Experimental or Historic RFCs, and expired/replaced/withdrawn drafts; the precise status is printed in text |
| 🏛️ NIST final publication | A final NIST publication such as a FIPS. Not an IETF RFC |
| ❔ Unverified | No successful check yet |
| 🔴 Source error / attention needed | Monitoring or data-quality problem. It never means "ordinary unpublished draft" |

Every indicator is accompanied by a text label, and the exact states are repeated in text columns.

**Three separate concepts.** *Publication* (Internet-Draft, RFC, NIST publication), *lifecycle state* (the source's own process state, e.g. "IESG Evaluation", "RFC Ed Queue", "Final") and *standards maturity* (the RFC Editor designation, e.g. Proposed Standard, Informational) are different things and are never merged. Being published as an RFC does not imply Proposed or Internet Standard status; the designation shown is the actual one reported by the RFC Editor. Maturity is "Not applicable" for drafts and NIST documents.

**Sources per field.**

| Field | Authoritative source |
|---|---|
| Draft version, lifecycle states, WG, intended level, draft→RFC link, replacements | IETF Datatracker API (`/api/v1/doc/...`) |
| RFC status (maturity), publication date, obsoletes/updates | RFC Editor per-RFC JSON (`/rfc/rfcN.json`), cross-checked with Datatracker `std_level` |
| NIST publication status and date | NIST CSRC publication page (HTML meta tags; NIST offers no JSON API for this) |
| Titles, areas, summaries, related links | Curated in `data/specifications.yml` (explanatory, not verified facts) |

**Refresh and errors.** A GitHub Actions workflow runs daily (06:17 UTC) and can be started manually. A failing source never changes a status: the last known data is kept, the failure is recorded, and the row is flagged 🔴 with its last successful verification date. Data not verified for more than {stale} days is flagged as stale. When nothing meaningful changed, no commit is made (a weekly heartbeat re-stamps verification times). History lives in `data/history.jsonl`; detection times are when this tracker noticed a change, not when the source changed it.

**Suggest a specification or report wrong metadata.** Open an issue or pull request; see [CONTRIBUTING.md](CONTRIBUTING.md). Adding a specification only needs an entry in `data/specifications.yml`.

## Disclaimer

This repository is a monitoring aid. It is not an authoritative standards publication and no substitute for the linked IETF, RFC Editor or NIST sources. Always consult those sources before relying on a status.
"""
