"""HTTP access with HTTPS enforcement, timeouts, retries and size limits."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlparse

USER_AGENT = "pqc-tracker/1.0 (+https://github.com/felixknopp/pqc-tracker)"
MAX_BYTES = 20 * 1024 * 1024
ALLOWED_HOSTS = frozenset({"datatracker.ietf.org", "www.rfc-editor.org", "csrc.nist.gov"})


class SourceError(Exception):
    """A source could not be fetched or its payload was unusable.

    A SourceError never implies a status change; callers keep last-known-good data.
    """

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message


class Http(Protocol):
    def get(self, url: str, source: str) -> bytes: ...


def check_url(url: str, source: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SourceError(source, f"refusing non-HTTPS URL: {url}")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise SourceError(source, f"host not allowed: {parsed.hostname}")


class HttpClient:
    """urllib based client. Retries transient failures with exponential backoff."""

    def __init__(
        self,
        timeout: float = 20.0,
        retries: int = 3,
        backoff: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.sleep = sleep

    def get(self, url: str, source: str) -> bytes:
        check_url(url, source)
        last = "unknown error"
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleep(self.backoff**attempt)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - scheme/host validated above
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    final = resp.geturl()
                    check_url(final, source)
                    data = resp.read(MAX_BYTES + 1)
                    if len(data) > MAX_BYTES:
                        raise SourceError(source, "response too large")
                    return data
            except urllib.error.HTTPError as exc:
                last = f"HTTP {exc.code} for {url}"
                if exc.code < 500 and exc.code != 429:
                    break  # permanent client error: do not retry
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = f"{type(exc).__name__}: {exc} for {url}"
        raise SourceError(source, last)


def get_json(http: Http, url: str, source: str) -> Any:
    raw = http.get(url, source)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceError(source, f"invalid JSON from {url}: {exc}") from exc
