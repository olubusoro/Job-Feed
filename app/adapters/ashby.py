"""
Ashby ATS adapter.
API: https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true

Response shape (relevant fields):
  {
    "jobPostings": [
      {
        "id": "abc-123",
        "title": "Software Engineer",
        "locationName": "Remote - US",
        "isRemote": true,
        "externalLink": "https://jobs.ashbyhq.com/...",
        "publishedAt": "2024-01-15T10:00:00.000Z",
        "descriptionPlain": "..."
      }
    ]
  }
"""

from __future__ import annotations

import logging

from app.adapters.base import BaseAdapter, NormalizedJob, fetch_with_retry

logger = logging.getLogger(__name__)


class AshbyAdapter(BaseAdapter):
    """Adapter for Ashby job boards."""

    ats_name = "ashby"
    BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"

    def fetch(self) -> list[NormalizedJob]:  # noqa: D102
        url = self.BASE_URL.format(token=self.token)
        try:
            resp = fetch_with_retry(url, params={"includeCompensation": "true"})
            resp.raise_for_status()
        except Exception as exc:
            logger.error("Ashby fetch failed for %s: %s", self.token, exc)
            return []

        try:
            data = resp.json()
        except Exception as exc:
            logger.error("Ashby: JSON parse error for %s: %s", self.token, exc)
            return []

        jobs_raw = data.get("jobPostings", [])
        if not isinstance(jobs_raw, list):
            logger.warning("Ashby: unexpected response shape for token %s", self.token)
            return []

        results: list[NormalizedJob] = []
        for raw in jobs_raw:
            try:
                location = raw.get("locationName") or raw.get("location") or ""
                is_remote = bool(raw.get("isRemote", False)) or "remote" in location.lower()
                posted_at = self._parse_dt(raw.get("publishedAt"))
                description = raw.get("descriptionPlain") or raw.get("description", "")

                results.append(
                    NormalizedJob(
                        company=self.company_name,
                        title=raw.get("title", ""),
                        location=location,
                        is_remote=is_remote,
                        url=raw.get("externalLink") or raw.get("jobUrl") or raw.get("applyUrl", ""),
                        posted_at=posted_at,
                        description=description,
                        ats=self.ats_name,
                        external_id=str(raw.get("id", "")),
                        token=self.token,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ashby: skipping malformed job entry: %s", exc)

        return results
