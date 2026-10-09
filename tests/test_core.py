"""Tests for sources, status model, change detection, rendering and failures.

All HTTP is mocked; no live Internet access is needed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from pqc_tracker import changes, render, status
from pqc_tracker.config import ConfigError, Spec, parse_specs
from pqc_tracker.http import SourceError, check_url
from pqc_tracker.sources import datatracker, nist, rfceditor
from pqc_tracker.update import (
    empty_state,
    load_state,
    run_update,
    semantic,
    update_files,
)  # fmt: skip

from .fakes import FakeHttp, add_draft, add_rfc

FIX = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)


def spec(**kw) -> Spec:
    base = dict(id="x", title="T", category="tls", organization="O", summary="S")
    base.update(kw)
    return Spec(**base)


def refresh(specs, state, http, now=T0):
    return run_update(specs, state, http, now)


def derived(state, sid="x"):
    return state["items"][sid]["derived"]


# --- 1-2, 7: draft lifecycle states ------------------------------------------------
def test_active_internet_draft():
    h = FakeHttp()
    add_draft(h, "draft-a-b", [1, 10, 20])
    st, _, errs = refresh([spec(draft="draft-a-b")], empty_state(), h)
    d = derived(st)
    assert errs == 0
    assert (d["publication"], d["maturity"], d["indicator"]) == (
        "Internet-Draft",
        "Not applicable",
        "active-draft",
    )
    assert d["lifecycle"] == "Active (WG Document)"
    assert d["intended_level"] == "Proposed Standard"


def test_draft_approved_for_publication_is_not_a_published_rfc():
    h = FakeHttp()
    add_draft(h, "draft-a-b", [1, 12, 20])
    st, _, _ = refresh([spec(draft="draft-a-b")], empty_state(), h)
    d = derived(st)
    assert d["lifecycle"] == "Approved-announcement to be sent"
    assert d["publication"] == "Internet-Draft"
    assert d["indicator"] == "in-review"


def test_rfc_editor_queue_and_iesg_evaluation_are_distinct_states():
    h = FakeHttp()
    add_draft(h, "draft-q", [1, 13, 30])
    add_draft(h, "draft-e", [1, 11], tags=("ad-f-up",))
    st, _, _ = refresh([spec(id="q", draft="draft-q"), spec(id="e", draft="draft-e")], empty_state(), h)
    assert derived(st, "q")["lifecycle"] == "RFC Ed Queue"
    assert "RFC Editor: In Progress" in derived(st, "q")["lifecycle_detail"]
    assert derived(st, "e")["lifecycle"] == "IESG Evaluation"
    assert "Tag ad-f-up" in derived(st, "e")["lifecycle_detail"]


@pytest.mark.parametrize("states,rep", [([2, 10], None), ([4, 10], "draft-new")])
def test_expired_or_replaced_draft(states, rep):
    h = FakeHttp()
    add_draft(h, "draft-old", states, replaced_by=rep)
    st, _, _ = refresh([spec(draft="draft-old")], empty_state(), h)
    d = derived(st)
    assert d["indicator"] == "non-standard"
    assert d["lifecycle"] in ("Expired", "Replaced")
    if rep:
        assert "Replaced by draft-new" in d["lifecycle_detail"]


# --- 3-6: RFC publication and maturity ----------------------------------------------
def test_newly_published_rfc_resolved_from_draft():
    h = FakeHttp()
    add_draft(h, "draft-a-b", [3, 14], became="rfc9999")
    add_rfc(h, 9999, draft="draft-a-b")
    st, events, errs = refresh([spec(draft="draft-a-b")], empty_state(), h)
    d = derived(st)
    assert errs == 0
    assert (d["publication"], d["document"], d["maturity"]) == (
        "Published RFC",
        "RFC 9999",
        "Proposed Standard",
    )


@pytest.mark.parametrize(
    "status_raw,dt,expected,indicator",
    [
        ("PROPOSED STANDARD", "ps", "Proposed Standard", "proposed-standard"),
        ("INTERNET STANDARD", "std", "Internet Standard", "internet-standard"),
        ("INFORMATIONAL", "inf", "Informational", "non-standard"),
        ("EXPERIMENTAL", "exp", "Experimental", "non-standard"),
        ("HISTORIC", "hist", "Historic", "non-standard"),
        ("BEST CURRENT PRACTICE", "bcp", "Best Current Practice", "other-rfc"),
    ],
)
def test_rfc_maturity_is_taken_from_metadata_not_assumed(status_raw, dt, expected, indicator):
    h = FakeHttp()
    add_rfc(h, 1234, status=status_raw, dt_level=dt)
    st, _, _ = refresh([spec(rfc=1234)], empty_state(), h)
    d = derived(st)
    assert d["maturity"] == expected and d["indicator"] == indicator
    assert d["warnings"] == []


def test_real_rfc10024_fixture_parses():
    data = json.loads((FIX / "rfc10024.json").read_text())
    f = rfceditor.parse_rfc(data, 10024)
    assert f["status"] == "Proposed Standard" and f["draft"] == "draft-ietf-tls-ecdhe-mlkem"


def test_unknown_rfc_status_is_not_silently_mapped():
    h = FakeHttp()
    add_rfc(h, 5, status="WEIRD STATUS", dt_level="ps")
    st, _, _ = refresh([spec(rfc=5)], empty_state(), h)
    d = derived(st)
    assert d["maturity"].startswith("Other (WEIRD STATUS")
    assert d["warnings"]


def test_maturity_mismatch_between_sources_is_flagged():
    h = FakeHttp()
    add_rfc(h, 5, status="PROPOSED STANDARD", dt_level="std")
    st, _, _ = refresh([spec(rfc=5)], empty_state(), h)
    assert any("mismatch" in w for w in derived(st)["warnings"])


# --- 8, 12: transitions ------------------------------------------------------------
def test_maturity_transition_keeps_identity_and_records_event():
    h = FakeHttp()
    add_rfc(h, 7, status="PROPOSED STANDARD", dt_level="ps")
    s = spec(rfc=7)
    st1, ev1, _ = refresh([s], empty_state(), h)
    assert [e["type"] for e in ev1] == ["tracking_started"]
    add_rfc(h, 7, status="INTERNET STANDARD", dt_level="std")
    st2, ev2, _ = refresh([s], st1, h, T0 + timedelta(days=1))
    assert list(st2["items"]) == ["x"]  # same identity, not a new item
    assert [(e["type"], e["previous"], e["new"]) for e in ev2] == [
        ("maturity_changed", "Proposed Standard", "Internet Standard")
    ]
    assert st2["items"]["x"]["last_changed"] == "2026-10-02T06:00:00Z"


def test_draft_to_rfc_transition():
    h = FakeHttp()
    add_draft(h, "draft-a-b", [1, 13, 30])
    s = spec(draft="draft-a-b")
    st1, _, _ = refresh([s], empty_state(), h)
    add_draft(h, "draft-a-b", [3, 14], became="rfc42", rev="03")
    add_rfc(h, 42, draft="draft-a-b")
    st2, ev, _ = refresh([s], st1, h, T0 + timedelta(days=2))
    fields = {e["field"] for e in ev}
    assert {"publication", "lifecycle", "maturity", "rfc_number", "version"} <= fields
    assert derived(st2)["document"] == "RFC 42"


# --- 11: unchanged refresh ---------------------------------------------------------
def test_unchanged_refresh_creates_no_events_and_no_semantic_diff():
    h = FakeHttp()
    add_draft(h, "draft-a-b", [1, 10])
    s = spec(draft="draft-a-b")
    st1, _, _ = refresh([s], empty_state(), h)
    st2, ev, _ = refresh([s], st1, h, T0 + timedelta(days=1))
    assert ev == [] and semantic(st1) == semantic(st2)


def test_irrelevant_metadata_churn_is_ignored():
    h = FakeHttp()
    add_draft(h, "draft-a-b", [1, 10])
    s = spec(draft="draft-a-b")
    st1, _, _ = refresh([s], empty_state(), h)
    key = [k for k in h.routes if "/doc/document/draft-a-b/" in k][0]
    h.routes[key] = {**h.routes[key], "time": "2030-01-01T00:00:00Z", "expires": "2031-01-01T00:00:00Z"}
    st2, ev, _ = refresh([s], st1, h, T0 + timedelta(days=1))
    assert ev == [] and semantic(st1) == semantic(st2)


# --- 13: relationships -------------------------------------------------------------
def test_obsoletes_and_updates_relationships_tracked():
    h = FakeHttp()
    add_rfc(h, 100, obsoletes=["RFC0090"], updates=["RFC 80"], updated_by=["RFC200"])
    s = spec(rfc=100)
    st1, _, _ = refresh([s], empty_state(), h)
    f = st1["items"]["x"]["facts"]["rfc"]
    assert f["obsoletes"] == ["RFC90"] and f["updates"] == ["RFC80"]
    add_rfc(h, 100, obsoletes=["RFC0090"], updates=["RFC 80"], updated_by=["RFC200"], obsoleted_by=["RFC300"])
    st2, ev, _ = refresh([s], st1, h, T0 + timedelta(days=1))
    assert [e["field"] for e in ev] == ["obsoleted_by"]
    assert "Obsoleted by RFC300" in derived(st2)["lifecycle_detail"]


# --- 9-10, 15: failures ------------------------------------------------------------
def test_http_error_preserves_last_known_good_and_marks_failure():
    h = FakeHttp()
    add_draft(h, "draft-a-b", [1, 11])
    s = spec(draft="draft-a-b")
    st1, _, _ = refresh([s], empty_state(), h)
    h.fail_all("datatracker", "HTTP 503")
    st2, ev, errs = refresh([s], st1, h, T0 + timedelta(days=1))
    item = st2["items"]["x"]
    assert errs >= 1 and ev == []  # a failure is never a status change
    assert item["facts"] == st1["items"]["x"]["facts"]
    assert item["derived"]["lifecycle"] == "IESG Evaluation"  # not downgraded
    assert item["check_status"] == "partial"
    assert item["last_verified"] == st1["items"]["x"]["last_verified"]  # not re-stamped
    assert st2["last_successful_refresh"] == st1["last_successful_refresh"]
    assert st2["last_run"]["outcome"] == "errors"


def test_first_ever_failure_is_unverified_not_a_valid_looking_status():
    h = FakeHttp()
    st, ev, errs = refresh([spec(draft="draft-a-b")], empty_state(), h)
    d = derived(st)
    assert errs == 1 and ev == []
    assert d["publication"] == status.UNVERIFIED and d["indicator"] == "unverified"
    assert st["items"]["x"]["check_status"] == "failed"


def test_timeout_error_is_a_source_error():
    h = FakeHttp()
    add_draft(h, "draft-a-b", [1, 10])
    h.routes[[k for k in h.routes if "doc/document/draft-a-b" in k][0]] = SourceError(
        "datatracker", "timed out"
    )
    st, _, errs = refresh([spec(draft="draft-a-b")], empty_state(), h)
    assert errs == 1 and "timed out" in st["items"]["x"]["errors"][0]["message"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("status"),
        lambda d: d.update(doc_id="RFC999"),
        lambda d: d.update(obsoletes="RFC1"),
        lambda d: d.update(obsoletes=["not-a-ref"]),
        lambda d: d.update(title=""),
    ],
)
def test_invalid_or_incomplete_rfc_metadata_rejected(mutate):
    data = json.loads((FIX / "rfc10024.json").read_text())
    mutate(data)
    with pytest.raises(SourceError):
        rfceditor.parse_rfc(data, 10024)


def test_incomplete_datatracker_metadata_rejected():
    h = FakeHttp()
    add_draft(h, "draft-a-b", [1, 10])
    key = [k for k in h.routes if "doc/document/draft-a-b" in k][0]
    h.routes[key] = {**h.routes[key], "rev": None}
    with pytest.raises(SourceError):
        datatracker.fetch_draft(datatracker.Datatracker(h), "draft-a-b")


def test_non_json_payload_is_source_error():
    h = FakeHttp({"https://www.rfc-editor.org/rfc/rfc1.json": b"<html>oops</html>"})
    with pytest.raises(SourceError):
        rfceditor.fetch_rfc(h, 1)


def test_nist_parser_real_fixture_and_layout_change():
    html = (FIX / "fips205.html").read_text()
    f = nist.parse_publication(html, "fips/205/final")
    assert f["status"] == "Final" and f["edition"] == "FIPS 205" and f["published"] == "2024-08-13"
    with pytest.raises(SourceError):
        nist.parse_publication("<html></html>", "fips/205/final")


def test_stale_data_reported_after_failed_refresh_and_by_age():
    h = FakeHttp()
    add_draft(h, "draft-a-b", [1, 10])
    s = spec(draft="draft-a-b")
    st1, _, _ = refresh([s], empty_state(), h)
    h.fail_all("datatracker")
    st2, _, _ = refresh([s], st1, h, T0 + timedelta(days=12))
    ref = datetime(2026, 10, 13, 6, 0, tzinfo=UTC)
    text, attention = render.freshness(st2["items"]["x"], ref)
    assert attention and "latest check failed" in text and "last verified 2026-10-01" in text
    assert "✅" not in text
    # age alone also flags staleness
    st1["items"]["x"]["check_status"] = "ok"
    text2, att2 = render.freshness(st1["items"]["x"], T0 + timedelta(days=11))
    assert att2 and "stale" in text2


# --- 16: idempotent history + files -------------------------------------------------
def write_specs(tmp: Path, specs: list[dict]) -> Path:
    p = tmp / "specifications.yml"
    p.write_text(yaml.safe_dump({"specifications": specs}))
    return p


BASE = dict(id="xx", title="T", category="tls", organization="O", summary="S")


def test_history_is_idempotent_and_runs_do_not_rewrite_files(tmp_path):
    h = FakeHttp()
    add_draft(h, "draft-a-b", [1, 10])
    sp = write_specs(tmp_path, [{**BASE, "draft": "draft-a-b"}])
    assert update_files(sp, tmp_path, h, T0) == 0
    hist = (tmp_path / "history.jsonl").read_text()
    cur = (tmp_path / "current.json").read_text()
    assert len(hist.splitlines()) == 1
    update_files(sp, tmp_path, h, T0 + timedelta(days=1))  # unchanged -> no write at all
    assert (tmp_path / "current.json").read_text() == cur
    assert (tmp_path / "history.jsonl").read_text() == hist
    # re-appending identical events is a no-op
    ev = changes.read_history(tmp_path / "history.jsonl")
    assert changes.append_history(tmp_path / "history.jsonl", ev) == 0
    # a heartbeat re-stamp (after 7 days) must not add history either
    update_files(sp, tmp_path, h, T0 + timedelta(days=8))
    assert (tmp_path / "history.jsonl").read_text() == hist
    assert load_state(tmp_path / "current.json")["last_successful_refresh"] == "2026-10-09T06:00:00Z"


def test_failed_run_does_not_overwrite_good_data_on_disk(tmp_path):
    h = FakeHttp()
    add_draft(h, "draft-a-b", [1, 11])
    sp = write_specs(tmp_path, [{**BASE, "draft": "draft-a-b"}])
    update_files(sp, tmp_path, h, T0)
    good = load_state(tmp_path / "current.json")["items"]["xx"]["facts"]
    h.fail_all("datatracker")
    assert update_files(sp, tmp_path, h, T0 + timedelta(days=1)) >= 1
    after = load_state(tmp_path / "current.json")
    assert after["items"]["xx"]["facts"] == good
    assert after["items"]["xx"]["check_status"] == "partial"
    assert len(changes.read_history(tmp_path / "history.jsonl")) == 1


# --- 14: README rendering -----------------------------------------------------------
def test_readme_empty_dataset():
    out = render.render([], empty_state(), [])
    assert "Tracked initiatives (total) | 0" in out and "Last successful data refresh:** never" in out
    assert "Disclaimer" in out


def test_readme_partial_dataset_unverified_row():
    s = spec(draft="draft-a-b")
    st, _, _ = refresh([s], empty_state(), FakeHttp())
    out = render.render([s], st, [])
    assert "❔ Unverified" in out and "Never verified" in out
    assert "🔴 Items needing attention (error / stale / inconsistent) | 1" in out


def test_readme_complete_dataset_counts_and_labels():
    h = FakeHttp()
    add_rfc(h, 1, status="INTERNET STANDARD", dt_level="std")
    add_rfc(h, 2)
    add_rfc(h, 3, status="INFORMATIONAL", dt_level="inf")
    add_draft(h, "draft-act", [1, 10])
    add_draft(h, "draft-rev", [1, 11])
    specs = [
        spec(id="a", rfc=1), spec(id="b", rfc=2), spec(id="c", rfc=3),
        spec(id="d", draft="draft-act"), spec(id="e", draft="draft-rev"),
    ]  # fmt: skip
    st, _, _ = refresh(specs, empty_state(), h)
    out = render.render(specs, st, [])
    for line in (
        "Tracked initiatives (total) | 5", "Published RFCs | 3", "Internet Standards | 1",
        "Proposed Standards | 1", "Active Internet-Drafts | 1", "in review / awaiting publication | 1",
    ):  # fmt: skip
        assert line in out
    assert "🟢 Internet Standard" in out and "⚪ Non-standard-track or inactive" in out
    assert "Informational" in out
    assert render.render(specs, st, []) == out  # deterministic


def test_readme_recently_changed_section():
    h = FakeHttp()
    add_rfc(h, 7)
    s = spec(rfc=7)
    st1, _, _ = refresh([s], empty_state(), h)
    add_rfc(h, 7, status="INTERNET STANDARD", dt_level="std")
    st2, ev, _ = refresh([s], st1, h, T0 + timedelta(days=1))
    out = render.render([s], st2, ev)
    assert "Proposed Standard | Internet Standard" in out
    assert "Items with status changes in the last 30 days | 1" in out


def test_markdown_injection_is_escaped():
    evil = "x | [link](http://evil) <script>alert(1)</script>\n# h"
    out = render.esc(evil)
    import re

    assert not re.search(r"(?<!\\)[|<>\[\]]", out)
    assert render.safe_url("http://datatracker.ietf.org/") is None
    assert render.safe_url("https://evil.example/") is None
    assert render.link("a", "javascript:alert(1)") == "a"


# --- config / security --------------------------------------------------------------
def test_config_validation():
    ok = {"specifications": [dict(BASE, draft="draft-a-b")]}
    assert parse_specs(ok)[0].draft == "draft-a-b"
    bad = [
        {"specifications": [dict(BASE)]},  # no source
        {"specifications": [dict(BASE, draft="Draft_A")]},
        {"specifications": [dict(BASE, draft="draft-a", category="nope")]},
        {"specifications": [dict(BASE, draft="draft-a", extra=1)]},
        {"specifications": [dict(BASE, draft="draft-a", related=["ghost"])]},
        {"specifications": [dict(BASE, draft="draft-a"), dict(BASE, draft="draft-b")]},
        {"specifications": [dict(BASE, rfc=-1)]},
        {"specifications": [dict(BASE, nist="http://x")]},
    ]
    for b in bad:
        with pytest.raises(ConfigError):
            parse_specs(b)


def test_shipped_inventory_is_valid():
    from pqc_tracker.config import load_specs

    specs = load_specs(Path(__file__).parent.parent / "data" / "specifications.yml")
    assert len(specs) >= 5


def test_only_https_allowed_hosts():
    check_url("https://datatracker.ietf.org/x", "s")
    for bad in ("http://datatracker.ietf.org/", "https://evil.example/", "file:///etc/passwd"):
        with pytest.raises(SourceError):
            check_url(bad, "s")


def test_datatracker_rejects_unexpected_api_path():
    with pytest.raises(SourceError):
        datatracker.Datatracker(FakeHttp())._get("/evil")


# --- HTTP client retries/backoff (urlopen mocked) -------------------------------------
def test_http_client_retries_transient_errors_with_backoff(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    from pqc_tracker.http import HttpClient

    calls, sleeps = [], []

    class Resp(io.BytesIO):
        def geturl(self):
            return "https://datatracker.ietf.org/x"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout):
        calls.append(timeout)
        if len(calls) < 3:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
        return Resp(b"ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    c = HttpClient(timeout=5, retries=3, backoff=2, sleep=sleeps.append)
    assert c.get("https://datatracker.ietf.org/x", "s") == b"ok"
    assert len(calls) == 3 and sleeps == [2, 4] and calls[0] == 5


def test_http_client_does_not_retry_404_and_gives_up(monkeypatch):
    import urllib.error
    import urllib.request

    from pqc_tracker.http import HttpClient

    calls = []

    def fake_open(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    with pytest.raises(SourceError, match="404"):
        HttpClient(sleep=lambda s: None).get("https://datatracker.ietf.org/x", "s")
    assert len(calls) == 1
