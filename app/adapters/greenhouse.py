"""
Greenhouse ATS adapter.
API: https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Response shape (relevant fields):
  {
    "jobs": [
      {
        "id": 12345,
        "title": "Software Engineer",
        "location": {"name": "Remote"},
        "absolute_url": "https://boards.greenhouse.io/...",
        "first_published": "2024-01-15T10:00:00-05:00",   # preferred
        "updated_at": "2024-01-16T08:00:00-05:00",         # fallback
        "content": "<p>Job description HTML</p>"
      }
    ]
  }
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser

from app.adapters.base import BaseAdapter, NormalizedJob, fetch_with_retry

logger = logging.getLogger(__name__)


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


class GreenhouseAdapter(BaseAdapter):
    """Adapter for Greenhouse job boards."""

    ats_name = "greenhouse"
    BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

    def fetch(self) -> list[NormalizedJob]:  # noqa: D102
        url = self.BASE_URL.format(token=self.token)
        try:
            resp = fetch_with_retry(url, params={"content": "true"})
            resp.raise_for_status()
        except Exception as exc:
            logger.error("Greenhouse fetch failed for %s: %s", self.token, exc)
            return []

        data = resp.json()
        jobs_raw = data.get("jobs", [])
        if not isinstance(jobs_raw, list):
            logger.warning("Greenhouse: unexpected response shape for token %s", self.token)
            return []

        results: list[NormalizedJob] = []
        for raw in jobs_raw:
            try:
                location_obj = raw.get("location") or {}
                location = location_obj.get("name", "") if isinstance(location_obj, dict) else str(location_obj)
                is_remote = "remote" in location.lower()

                # Prefer first_published over updated_at
                posted_at = self._parse_dt(
                    raw.get("first_published") or raw.get("updated_at")
                )

                description = _strip_html(raw.get("content", ""))

                results.append(
                    NormalizedJob(
                        company=self.company_name,
                        title=raw.get("title", ""),
                        location=location,
                        is_remote=is_remote,
                        url=raw.get("absolute_url", ""),
                        posted_at=posted_at,
                        description=description,
                        ats=self.ats_name,
                        external_id=str(raw.get("id", "")),
                        token=self.token,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Greenhouse: skipping malformed job entry: %s", exc)

        return results
