#!/usr/bin/env python3
"""Regenerate README.md from stored data only (no network access)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pqc_tracker.changes import read_history  # noqa: E402
from pqc_tracker.config import ConfigError, load_specs  # noqa: E402
from pqc_tracker.render import render  # noqa: E402
from pqc_tracker.update import load_state  # noqa: E402


def generate(specs_path: Path, data_dir: Path, readme: Path) -> bool:
    """Write README if content differs. Returns True when it changed."""
    text = render(
        load_specs(specs_path),
        load_state(data_dir / "current.json"),
        read_history(data_dir / "history.jsonl"),
    )
    if readme.exists() and readme.read_text(encoding="utf-8") == text:
        return False
    readme.write_text(text, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--specs", type=Path, default=ROOT / "data" / "specifications.yml")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--readme", type=Path, default=ROOT / "README.md")
    ap.add_argument("--check", action="store_true", help="fail if README is out of date")
    a = ap.parse_args(argv)
    if a.check:
        before = a.readme.read_text(encoding="utf-8") if a.readme.exists() else ""
        text = render(
            load_specs(a.specs),
            load_state(a.data_dir / "current.json"),
            read_history(a.data_dir / "history.jsonl"),
        )
        return 0 if text == before else 1
    try:
        print("README updated" if generate(a.specs, a.data_dir, a.readme) else "README unchanged")
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
