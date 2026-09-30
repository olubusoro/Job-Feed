"""
Recruitee ATS adapter.
API: https://{token}.recruitee.com/api/offers/

Response shape (relevant fields):
  {
    "offers": [
      {
        "id": 12345,
        "title": "Software Engineer",
        "location": "Remote",
        "remote": true,
        "careers_url": "https://company.recruitee.com/o/software-engineer",
        "published_at": "2024-01-15T10:00:00.000Z",
        "description": "..."
      }
    ]
  }
"""

from __future__ import annotations

import logging

from app.adapters.base import BaseAdapter, NormalizedJob, fetch_with_retry

logger = logging.getLogger(__name__)


class RecruiteeAdapter(BaseAdapter):
    """Adapter for Recruitee job boards."""

    ats_name = "recruitee"
    BASE_URL = "https://{token}.recruitee.com/api/offers/"

    def fetch(self) -> list[NormalizedJob]:  # noqa: D102
        url = self.BASE_URL.format(token=self.token)
        try:
            resp = fetch_with_retry(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("Recruitee fetch failed for %s: %s", self.token, exc)
            return []

        try:
            data = resp.json()
        except Exception as exc:
            logger.error("Recruitee: JSON parse error for %s: %s", self.token, exc)
            return []

        jobs_raw = data.get("offers", [])
        if not isinstance(jobs_raw, list):
            logger.warning("Recruitee: unexpected response for token %s", self.token)
            return []

        results: list[NormalizedJob] = []
        for raw in jobs_raw:
            try:
                location = raw.get("location") or raw.get("city") or ""
                is_remote = bool(raw.get("remote", False)) or "remote" in location.lower()
                posted_at = self._parse_dt(
                    raw.get("published_at") or raw.get("created_at")
                )
                description = raw.get("description") or raw.get("description_plain", "")

                results.append(
                    NormalizedJob(
                        company=self.company_name,
                        title=raw.get("title", ""),
                        location=location,
                        is_remote=is_remote,
                        url=raw.get("careers_url") or raw.get("url", ""),
                        posted_at=posted_at,
                        description=description,
                        ats=self.ats_name,
                        external_id=str(raw.get("id", raw.get("slug", ""))),
                        token=self.token,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Recruitee: skipping malformed job entry: %s", exc)

        return results
