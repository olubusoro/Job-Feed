"""
Base adapter interface and shared HTTP client utilities.
All ATS adapters must subclass BaseAdapter and implement `fetch()`.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Normalised job record
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class NormalizedJob:
    """Common representation of a job posting across all ATS adapters."""

    company: str
    title: str
    location: str
    is_remote: bool
    url: str
    posted_at: Optional[datetime]  # None means "unknown"
    description: str
    ats: str
    external_id: str

    # Convenience: source company token (set by orchestrator)
    token: str = field(default="")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helper with retry + backoff
# ─────────────────────────────────────────────────────────────────────────────

RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


def _build_client() -> httpx.Client:
    return httpx.Client(
        timeout=settings.HTTP_TIMEOUT,
        headers={"User-Agent": settings.HTTP_USER_AGENT},
        follow_redirects=True,
    )


def fetch_with_retry(url: str, params: Optional[dict] = None) -> httpx.Response:
    """
    GET *url* with exponential backoff on timeouts / 429 / 5xx.
    Raises httpx.HTTPError on final failure.
    """
    backoff = 1.0
    last_exc: Exception = RuntimeError("No attempts made")

    with _build_client() as client:
        for attempt in range(settings.HTTP_MAX_RETRIES):
            try:
                resp = client.get(url, params=params)
                if resp.status_code in RETRY_STATUS_CODES:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                return resp
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < settings.HTTP_MAX_RETRIES - 1:
                    sleep = backoff * (2**attempt)
                    logger.warning(
                        "Retry %d/%d for %s after %.1fs: %s",
                        attempt + 1,
                        settings.HTTP_MAX_RETRIES,
                        url,
                        sleep,
                        exc,
                    )
                    time.sleep(sleep)

    raise last_exc


# ─────────────────────────────────────────────────────────────────────────────
# Base adapter
# ─────────────────────────────────────────────────────────────────────────────


class BaseAdapter(ABC):
    """Abstract base class for ATS adapters."""

    ats_name: str = ""

    def __init__(self, company_name: str, token: str) -> None:
        self.company_name = company_name
        self.token = token

    @abstractmethod
    def fetch(self) -> list[NormalizedJob]:
        """
        Fetch and return normalised job postings for this company.
        Must never raise — return [] on unrecoverable error.
        """

    @staticmethod
    def _parse_dt(value: str | int | None) -> Optional[datetime]:
        """
        Parse a datetime from common ATS formats:
        - ISO 8601 string (with or without Z/offset)
        - Unix epoch milliseconds (int > 1_000_000_000_000)
        - Unix epoch seconds (int)
        """
        if value is None:
            return None
        if isinstance(value, (int, float)):
            # Epoch ms vs epoch s
            ts = value / 1000 if value > 1_000_000_000_000 else value
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
        if isinstance(value, str):
            value = value.rstrip("Z").replace("T", " ")
            # Try common formats
            for fmt in (
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
            ):
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
        return None
