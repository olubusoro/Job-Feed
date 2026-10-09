"""
Jobicy adapter — global remote job board.
API: https://jobicy.com/api/v2/remote-jobs?count=50&tag={query}

Response shape:
  {
    "success": true,
    "jobs": [
      {
        "id": 12345,
        "jobTitle": "Software Engineer",
        "companyName": "Acme Corp",
        "jobGeo": "Europe",               ← key field for matcher
        "url": "https://jobicy.com/jobs/...",
        "pubDate": "2024-01-15 10:00:00",
        "jobDescription": "<p>Job description HTML</p>"
      }
    ]
  }

- is_remote is ALWAYS True (Jobicy is a remote-only board).
- jobGeo maps directly to location — the shared _location_matches() filter in
  matcher.py will accept/reject based on it. Examples: "Europe", "UK",
  "Worldwide", "USA Only".
- No per-company token: this adapter is called once per search query (target title).
- HTML in jobDescription is stripped to plain text.
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser

from app.adapters.base import BaseAdapter, NormalizedJob, fetch_with_retry

logger = logging.getLogger(__name__)

ATS_NAME = "jobicy"
BASE_URL = "https://jobicy.com/api/v2/remote-jobs"

# Jobicy's max result count per request
_MAX_COUNT = 50


# ─────────────────────────────────────────────────────────────────────────────
# HTML stripper (same pattern as greenhouse.py / remotive.py)
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


def fetch_jobicy(search_query: str) -> list[NormalizedJob]:
    """
    Fetch remote jobs from Jobicy matching *search_query*.

    Called once per target title from the fetch orchestrator. Uses the shared
    fetch_with_retry helper (exponential backoff + configurable timeout) from
    adapters/base.py — no duplicated retry logic here.

    Returns an empty list on any unrecoverable error — never raises.
    """
    try:
        resp = fetch_with_retry(
            BASE_URL,
            params={"count": _MAX_COUNT, "tag": search_query},
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Jobicy fetch failed for query=%r: %s", search_query, exc)
        return []

    try:
        data = resp.json()
    except Exception as exc:
        logger.error("Jobicy: JSON parse error for query=%r: %s", search_query, exc)
        return []

    # Jobicy wraps results in {"success": true/false, "jobs": [...]}
    if not data.get("success", True):
        logger.warning("Jobicy: API returned success=false for query=%r", search_query)
        return []

    jobs_raw = data.get("jobs", [])
    if not isinstance(jobs_raw, list):
        logger.warning("Jobicy: unexpected response shape (jobs not a list)")
        return []

    results: list[NormalizedJob] = []
    for raw in jobs_raw:
        try:
            # jobGeo is the field matcher.py's _location_matches() reads.
            # Examples from Jobicy: "Europe", "UK", "Worldwide", "USA", "Anywhere".
            location: str = str(raw.get("jobGeo") or "").strip()

            # Reuse BaseAdapter._parse_dt for consistent datetime parsing
            # Jobicy pubDate format: "2024-01-15 10:00:00"
            posted_at = BaseAdapter._parse_dt(raw.get("pubDate"))

            description = _strip_html(raw.get("jobDescription", ""))

            company_name = str(raw.get("companyName") or "").strip() or "Unknown"

            results.append(
                NormalizedJob(
                    company=company_name,
                    title=str(raw.get("jobTitle") or "").strip(),
                    location=location,
                    is_remote=True,  # Jobicy is remote-only — always True
                    url=str(raw.get("url") or "").strip(),
                    posted_at=posted_at,
                    description=description,
                    ats=ATS_NAME,
                    external_id=str(raw.get("id", "")),
                    token="__global__",
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Jobicy: skipping malformed job entry: %s", exc)

    logger.info("Jobicy: fetched %d jobs for query=%r", len(results), search_query)
    return results
