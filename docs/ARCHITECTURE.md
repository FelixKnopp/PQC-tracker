# Architecture

```
data/specifications.yml  --(config.py)-->  Spec list
        |                                    |
        v                                    v
 sources/datatracker.py  \                update.py: fetch -> facts (per source, last-known-good kept on failure)
 sources/rfceditor.py     >--facts-->  status.py: derive publication / lifecycle / maturity / indicator
 sources/nist.py         /                   |
                                             v
                          changes.py: diff meaningful fields -> data/history.jsonl (append-only)
                                             |
                          data/current.json  v          render.py (pure) -> README.md
```

## Verified source behaviour (inspected 2026-10-09)

* **Datatracker** `/api/v1/doc/document/<draft>/?format=json`: `states` are URIs to state objects (`type`, `slug`, `name`);
  `tags` (e.g. *AD Followup*), `group`, `intended_std_level`, `rev`. A draft's `rfc`/`rfc_number` stay `null` after publication:
  the link is a `relateddocument` with relationship `became_rfc`, and the draft gets the state `RFC`. RFCs are separate
  documents (`rfcNNNN`) with `std_level`.
* **RFC Editor** `/rfc/rfcN.json`: `status` is the maturity designation, plus `obsoletes`, `obsoleted_by`, `updates`,
  `updated_by`, `draft` (with revision suffix). `rfc-index.json` does not exist (404); the per-RFC JSON is used.
* **NIST CSRC**: no JSON API found. The publication page is parsed (`citation_*` meta tags, and the document-history
  entry flagged `data-current-document='true'`, e.g. "FIPS 205 (Final)"). This is the most fragile adapter; a layout
  change yields a source error (never a status change).

## Three independent dimensions

`publication` (Internet-Draft / Published RFC / NIST publication / unverified), `lifecycle` (the source's own state text),
`maturity` (RFC Editor designation; "Not applicable" for drafts and NIST). Maturity from the RFC Editor is cross-checked
against Datatracker `std_level`; a mismatch or unrecognized value is surfaced as a data-quality notice.

## Failure and staleness

A failed source keeps its previous facts, records the error, does not update `last_verified`, and never emits history events.
The README flags the row. Verification older than 10 days is flagged stale, and a never-verified item shows "Unverified".

## Idempotence and commits

`update_files` compares state without timestamps. If nothing meaningful changed, nothing is written (a weekly heartbeat
re-stamps verification times). History event ids are content hashes and duplicates are skipped.

## Assumptions and limits

* Detection time is when the tracker saw the change, not when the source changed.
* README staleness is evaluated relative to the last run recorded in the data (deterministic, offline regeneration).
  If the workflow itself stops running, the Actions badge is the signal.
* Datatracker/RFC Editor impose no documented hard rate limits for this volume (~15 requests per item, cached per run).
* Licence: MIT was chosen as a common default (assumption); change `LICENSE` if you prefer another.
