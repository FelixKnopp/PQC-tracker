"""NIST CSRC adapter. No JSON API was found; the publication page is parsed.

Fields come from the page's ``dcterms``/``citation`` meta tags and the
"Document History" block (``data-current-document='true'`` marks the
current edition, e.g. ``FIPS 205 (Final)``).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from ..http import Http, SourceError

SOURCE = "nist"
PUB_RE = re.compile(r"^(fips|sp)/([a-z0-9-]+)/([a-z]+)$")
CURRENT_RE = re.compile(r"^(.*?)\s*\(([^)]+)\)\s*$")


def page_url(ref: str) -> str:
    return f"https://csrc.nist.gov/pubs/{ref}"


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self._cur: dict[str, str] | None = None
        self._cur_text: list[str] = []
        self.current_doc: str | None = None
        self.history: list[str] = []
        self._hist_depth = 0
        self._hist_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta" and a.get("name") and a.get("content"):
            self.meta.setdefault(a["name"], a["content"])
        if a.get("id", "").startswith("pub-history-link-"):
            self._cur = a
            self._cur_text = []

    def handle_data(self, data: str) -> None:
        if self._cur is not None:
            self._cur_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._cur is not None and tag in ("a", "span"):
            text = " ".join("".join(self._cur_text).split())
            self.history.append(text)
            if self._cur.get("data-current-document") == "true":
                self.current_doc = text
            self._cur = None


def parse_publication(html: str, ref: str) -> dict[str, Any]:
    m = PUB_RE.match(ref)
    if not m:
        raise SourceError(SOURCE, f"invalid NIST reference {ref!r}")
    page = _Page()
    page.feed(html)
    title = page.meta.get("citation_title")
    number = page.meta.get("citation_technical_report_number")
    date = page.meta.get("citation_publication_date")
    if not (title and number and date and page.current_doc):
        raise SourceError(SOURCE, f"{ref}: page layout changed (missing title/number/date/status)")
    cm = CURRENT_RE.match(page.current_doc)
    if not cm:
        raise SourceError(SOURCE, f"{ref}: cannot parse current edition {page.current_doc!r}")
    if not re.fullmatch(r"\d{4}/\d{2}/\d{2}", date):
        raise SourceError(SOURCE, f"{ref}: unexpected date format {date!r}")
    return {
        "ref": ref,
        "title": title,
        "number": number,
        "published": date.replace("/", "-"),
        "status": cm.group(2).strip(),  # e.g. Final, Draft, Withdrawn
        "edition": cm.group(1).strip(),
        "doi": page.meta.get("citation_doi"),
        "url": page_url(ref),
    }


def fetch_publication(http: Http, ref: str) -> dict[str, Any]:
    if not PUB_RE.match(ref):
        raise SourceError(SOURCE, f"invalid NIST reference {ref!r}")
    html = http.get(page_url(ref), SOURCE).decode("utf-8", errors="replace")
    return parse_publication(html, ref)
