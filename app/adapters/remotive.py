"""
Remotive adapter — global remote job board.
API: https://remotive.com/api/remote-jobs?search={query}

Response shape:
  {
    "job-count": 42,
    "jobs": [
      {
        "id": 12345,
        "title": "Software Engineer",
        "company_name": "Acme Corp",
        "candidate_required_location": "Europe",   ← key field for matcher
        "url": "https://remotive.com/remote-jobs/...",
        "publication_date": "2024-01-15T10:00:00+00:00",
        "description": "<p>Job description HTML</p>"
      }
    ]
  }

- is_remote is ALWAYS True (Remotive is a remote-only board).
- candidate_required_location maps directly to location — the shared
  _location_matches() filter in matcher.py will accept/reject based on it.
- No per-company token: this adapter is called once per search query (target title).
- HTML in description is stripped to plain text.
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser

from app.adapters.base import BaseAdapter, NormalizedJob, fetch_with_retry

logger = logging.getLogger(__name__)

ATS_NAME = "remotive"
BASE_URL = "https://remotive.com/api/remote-jobs"


# ─────────────────────────────────────────────────────────────────────────────
# HTML stripper (same pattern as greenhouse.py)
# ─────────────────────────────────────────────────────────────────────────────


class _HTMLStripper(HTMLParser):
    """Minimal HTML → plain text converter."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def _strip_html(html: str) -> str:
    parser = _HTMLStripper()
    parser.feed(html or "")
    return parser.get_text()


# ─────────────────────────────────────────────────────────────────────────────
# Public fetch function (per-profile, not per-company)
# ─────────────────────────────────────────────────────────────────────────────


def fetch_remotive(search_query: str) -> list[NormalizedJob]:
    """
    Fetch remote jobs from Remotive matching *search_query*.

    Called once per target title from the fetch orchestrator. Uses the shared
    fetch_with_retry helper (exponential backoff + configurable timeout) from
    adapters/base.py — no duplicated retry logic here.

    Returns an empty list on any unrecoverable error — never raises.
    """
    try:
        resp = fetch_with_retry(BASE_URL, params={"search": search_query})
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Remotive fetch failed for query=%r: %s", search_query, exc)
        return []

    try:
        data = resp.json()
    except Exception as exc:
        logger.error("Remotive: JSON parse error for query=%r: %s", search_query, exc)
        return []

    jobs_raw = data.get("jobs", [])
    if not isinstance(jobs_raw, list):
        logger.warning("Remotive: unexpected response shape (jobs not a list)")
        return []

    results: list[NormalizedJob] = []
    for raw in jobs_raw:
        try:
            # candidate_required_location is the field matcher.py's _location_matches()
            # reads. Examples from Remotive: "Europe", "USA Only", "Worldwide", "UK".
            location: str = str(raw.get("candidate_required_location") or "").strip()

            # Reuse BaseAdapter._parse_dt for consistent datetime parsing across all adapters
            posted_at = BaseAdapter._parse_dt(raw.get("publication_date"))

            description = _strip_html(raw.get("description", ""))

            company_name = str(raw.get("company_name") or "").strip() or "Unknown"

            results.append(
                NormalizedJob(
                    company=company_name,
                    title=str(raw.get("title") or "").strip(),
                    location=location,
                    is_remote=True,  # Remotive is remote-only — always True
                    url=str(raw.get("url") or "").strip(),
                    posted_at=posted_at,
                    description=description,
                    ats=ATS_NAME,
                    external_id=str(raw.get("id", "")),
                    token="__global__",
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Remotive: skipping malformed job entry: %s", exc)

    logger.info("Remotive: fetched %d jobs for query=%r", len(results), search_query)
    return results
