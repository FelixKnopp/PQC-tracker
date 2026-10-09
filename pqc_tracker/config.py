"""Declarative inventory loading and validation (data/specifications.yml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CATEGORIES = {
    "x509-pki": "X.509 PKI",
    "tls": "TLS",
    "signatures": "Digital signatures",
    "key-establishment": "Key establishment",
    "algorithms": "Algorithm specifications",
    "other-protocols": "Other protocol integrations",
}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
DRAFT_RE = re.compile(r"^draft-[a-z0-9-]+$")
NIST_RE = re.compile(r"^(fips|sp|ir)/[a-z0-9-]+/[a-z]+$")
ALLOWED_KEYS = {
    "id", "title", "category", "organization", "summary",
    "draft", "rfc", "nist", "related", "note",
}  # fmt: skip


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Spec:
    id: str
    title: str
    category: str
    organization: str
    summary: str
    draft: str | None = None
    rfc: int | None = None
    nist: str | None = None
    related: tuple[str, ...] = field(default_factory=tuple)
    note: str | None = None


def parse_specs(raw: Any) -> list[Spec]:
    items = raw.get("specifications") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ConfigError("top level must be a mapping with a 'specifications' list")
    specs: list[Spec] = []
    seen: set[str] = set()
    for i, it in enumerate(items):
        where = f"specifications[{i}]"
        if not isinstance(it, dict):
            raise ConfigError(f"{where}: must be a mapping")
        unknown = set(it) - ALLOWED_KEYS
        if unknown:
            raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")
        for key in ("id", "title", "category", "organization", "summary"):
            if not isinstance(it.get(key), str) or not it[key].strip():
                raise ConfigError(f"{where}: '{key}' is required")
        sid = it["id"]
        if not ID_RE.match(sid):
            raise ConfigError(f"{where}: invalid id {sid!r}")
        if sid in seen:
            raise ConfigError(f"duplicate id {sid!r}")
        seen.add(sid)
        if it["category"] not in CATEGORIES:
            raise ConfigError(f"{sid}: unknown category {it['category']!r}")
        draft, rfc, nist = it.get("draft"), it.get("rfc"), it.get("nist")
        if not (draft or rfc or nist):
            raise ConfigError(f"{sid}: needs at least one of draft, rfc, nist")
        if draft is not None and not (isinstance(draft, str) and DRAFT_RE.match(draft)):
            raise ConfigError(f"{sid}: invalid draft name {draft!r} (no revision suffix)")
        if rfc is not None and not (isinstance(rfc, int) and not isinstance(rfc, bool) and rfc > 0):
            raise ConfigError(f"{sid}: rfc must be a positive integer")
        if nist is not None and not (isinstance(nist, str) and NIST_RE.match(nist)):
            raise ConfigError(f"{sid}: nist must look like 'fips/205/final'")
        related = it.get("related") or []
        if not isinstance(related, list) or not all(isinstance(r, str) for r in related):
            raise ConfigError(f"{sid}: related must be a list of ids")
        specs.append(
            Spec(
                id=sid,
                title=it["title"].strip(),
                category=it["category"],
                organization=it["organization"].strip(),
                summary=" ".join(it["summary"].split()),
                draft=draft,
                rfc=rfc,
                nist=nist,
                related=tuple(related),
                note=it.get("note"),
            )
        )
    for s in specs:
        for r in s.related:
            if r not in seen:
                raise ConfigError(f"{s.id}: related id {r!r} is not in the inventory")
    return specs


def load_specs(path: Path) -> list[Spec]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    return parse_specs(raw)
