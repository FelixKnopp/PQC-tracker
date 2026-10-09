"""Orchestrates a refresh: fetch -> normalize -> derive -> diff -> persist."""

from __future__ import annotations

import copy
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import changes, status
from .config import Spec, load_specs
from .http import Http, SourceError
from .sources import datatracker, nist, rfceditor

log = logging.getLogger("pqc_tracker")
SCHEMA_VERSION = 1
HEARTBEAT_DAYS = 7  # unchanged data is re-stamped at most this often
STALE_DAYS = 10  # verification older than this is reported as stale

VOLATILE_ITEM_KEYS = {"last_checked", "source_verified"}


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "last_run": None,
        "last_successful_refresh": None,
        "items": {},
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version in {path}")
    return state


def dump_state(state: dict[str, Any]) -> str:
    return json.dumps(state, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _fetch_rfc(dt: datatracker.Datatracker, http: Http, number: int) -> dict[str, Any]:
    facts = rfceditor.fetch_rfc(http, number)
    try:  # cross-check only; a failure here must not fail the RFC itself
        facts["datatracker_std_level"] = datatracker.fetch_rfc_levels(dt, number)["std_level"]
    except SourceError as exc:
        log.warning("Datatracker cross-check for RFC %s skipped: %s", number, exc.message)
        facts["datatracker_std_level"] = None
    return facts


def refresh_item(spec: Spec, old: dict[str, Any] | None, http: Http, now: str) -> dict[str, Any]:
    """Return the new item record. Failed sources keep last-known-good facts."""
    dt = datatracker.Datatracker(http)
    item = copy.deepcopy(old) if old else {"first_seen": now, "facts": {}, "source_verified": {}}
    item.setdefault("facts", {})
    item.setdefault("source_verified", {})
    item["last_checked"] = now
    errors: list[dict[str, str]] = []

    def attempt(kind: str, fn: Any) -> Any:
        try:
            result = fn()
        except SourceError as exc:
            log.error("%s [%s]: %s", spec.id, kind, exc.message)
            errors.append({"source": exc.source, "target": kind, "message": exc.message})
            return None
        item["facts"][kind] = result
        item["source_verified"][kind] = now
        return result

    draft = attempt("draft", lambda: datatracker.fetch_draft(dt, spec.draft)) if spec.draft else None
    draft = draft or item["facts"].get("draft")
    rfc_no = spec.rfc
    if rfc_no is None and draft and draft.get("became_rfc"):
        rfc_no = int(draft["became_rfc"][0])
    if rfc_no is not None:
        rfc = attempt("rfc", lambda: _fetch_rfc(dt, http, rfc_no))
        if spec.draft is None and rfc and rfc.get("draft"):
            attempt("draft", lambda: datatracker.fetch_draft(dt, rfc["draft"]))
    if spec.nist:
        attempt("nist", lambda: nist.fetch_publication(http, spec.nist))

    item["errors"] = [{**e, "at": now} for e in errors]
    expected = [k for k, v in (("draft", spec.draft), ("rfc", rfc_no), ("nist", spec.nist)) if v]
    item["check_status"] = "ok" if not errors else ("partial" if item["facts"] else "failed")
    item["verified_kinds"] = [k for k in expected if k in item["source_verified"]]
    item["derived"] = status.derive(item["facts"]) if item["facts"] else None
    if item["derived"] is None:
        item["derived"] = status.derive({})
    ok_times = [item["source_verified"][k] for k in expected if k in item["source_verified"]]
    item["last_verified"] = max(ok_times) if ok_times else None
    item.setdefault("last_changed", None)
    return item


def semantic(state: dict[str, Any]) -> str:
    """State minus timestamps/volatile fields, for no-op detection."""
    s = copy.deepcopy(state)
    s.pop("last_run", None)
    s.pop("last_successful_refresh", None)
    for it in s["items"].values():
        for k in VOLATILE_ITEM_KEYS | {"last_verified"}:
            it.pop(k, None)
        for e in it.get("errors", []):
            e.pop("at", None)
        for fact in it.get("facts", {}).values():
            fact.pop("volatile", None)
    return json.dumps(s, sort_keys=True)


def run_update(
    specs: list[Spec],
    state: dict[str, Any],
    http: Http,
    now: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Pure-ish core: returns (new_state, events, error_count). No file IO."""
    at = iso(now)
    new = copy.deepcopy(state)
    new["items"] = {}
    events: list[dict[str, Any]] = []
    error_count = 0
    for spec in specs:
        old = state["items"].get(spec.id)
        item = refresh_item(spec, old, http, at)
        error_count += len(item["errors"])
        url = (item["derived"] or {}).get("document_url") or ""
        new_events = changes.diff_events(spec.id, old, item, at, url) if item["facts"] else []
        if item["facts"] and old and new_events and old.get("facts"):
            item["last_changed"] = at
        elif item["facts"] and not old:
            item["last_changed"] = at
        events.extend(new_events)
        new["items"][spec.id] = item
    # Specs removed from the inventory are dropped from current state; history is kept.
    new["last_run"] = {"at": at, "outcome": "ok" if not error_count else "errors", "errors": error_count}
    if not error_count:
        new["last_successful_refresh"] = at
    return new, events, error_count


def heartbeat_due(state: dict[str, Any], now: datetime) -> bool:
    last = state.get("last_successful_refresh")
    return not last or now - parse_iso(last) >= timedelta(days=HEARTBEAT_DAYS)


def update_files(
    spec_path: Path,
    data_dir: Path,
    http: Http,
    now: datetime,
) -> int:
    """Run an update and persist only when something meaningful changed.

    Returns the number of source errors (0 means a clean refresh).
    """
    specs = load_specs(spec_path)
    cur_path, hist_path = data_dir / "current.json", data_dir / "history.jsonl"
    state = load_state(cur_path)
    new, events, errors = run_update(specs, state, http, now)
    changed = semantic(new) != semantic(state)
    if not changed and not (heartbeat_due(state, now) and not errors):
        log.info("No meaningful changes; nothing written.")
        return errors
    cur_path.write_text(dump_state(new), encoding="utf-8")
    hist_path.touch()
    n = changes.append_history(hist_path, events)
    log.info("State written (%d new history events).", n)
    return errors
