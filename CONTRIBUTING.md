# Contributing

## Add a tracked specification

Edit `data/specifications.yml` only; no code changes are needed.

```yaml
- id: my-new-spec               # unique, lowercase, [a-z0-9-]
  title: Human readable title
  category: tls                 # x509-pki | tls | signatures | key-establishment | algorithms | other-protocols
  organization: IETF TLS WG
  summary: One factual sentence (curated text, not a verified fact).
  draft: draft-ietf-example-name   # Datatracker name, no -NN revision suffix
  rfc: 1234                        # optional; auto-resolved after the draft becomes an RFC
  nist: fips/205/final             # optional; NIST CSRC path
  related: [other-id]              # optional
```

At least one of `draft`, `rfc`, `nist` is required. Do not write statuses by hand: they are fetched.
Then run `python scripts/update.py` (needs network) and commit `data/` and `README.md`,
or just open a pull request and let the scheduled workflow fill in the data.

## Report incorrect metadata

Open an issue with the specification id, what the README shows, and the authoritative link showing otherwise.
Fixes normally belong in the source adapters (`pqc_tracker/sources/`) or the status model (`pqc_tracker/status.py`).
**Never edit `README.md` by hand**; it is regenerated.

## Correcting history

`data/history.jsonl` is append-only and immutable. If an event was recorded wrongly (e.g. due to a bug), do not
rewrite it: append a new line with `"type": "correction"`, a `"corrects"` field holding the event id, and a
`"note"`, in a pull request that explains the fix.

## Local development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
make check            # ruff, ruff format --check, pytest, README-up-to-date check
python scripts/update.py           # live refresh (exit 2 = a source failed, data preserved)
python scripts/generate_readme.py  # offline: regenerate README from stored data
```

Tests use fixtures and mocked HTTP; they never touch the network.
