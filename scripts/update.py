#!/usr/bin/env python3
"""Fetch current metadata, update data/current.json + history, regenerate README.

Exit codes: 0 clean, 1 fatal/config error, 2 one or more sources failed
(last-known-good data was preserved and the failure recorded).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pqc_tracker.config import ConfigError  # noqa: E402
from pqc_tracker.http import HttpClient  # noqa: E402
from pqc_tracker.update import update_files  # noqa: E402

import generate_readme  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--specs", type=Path, default=ROOT / "data" / "specifications.yml")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--readme", type=Path, default=ROOT / "README.md")
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        errors = update_files(args.specs, args.data_dir, HttpClient(timeout=args.timeout), datetime.now(UTC))
        generate_readme.generate(args.specs, args.data_dir, args.readme)
    except (ConfigError, ValueError, OSError) as exc:
        logging.error("fatal: %s", exc)
        return 1
    if errors:
        logging.error("%d source error(s); last-known-good data preserved.", errors)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
