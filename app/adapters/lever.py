"""
Lever ATS adapter.
Primary:  https://api.lever.co/v0/postings/{token}?mode=json
Fallback: https://api.eu.lever.co/v0/postings/{token}?mode=json

Response shape (relevant fields):
  [
    {
      "id": "abc-123",
      "text": "Software Engineer",
      "categories": {"location": "Remote"},
      "hostedUrl": "https://jobs.lever.co/...",
      "createdAt": 1705312000000,         # epoch ms
      "descriptionPlain": "Job description..."
    }
  ]
"""

from __future__ import annotations

import logging

import httpx

from app.adapters.base import BaseAdapter, NormalizedJob, fetch_with_retry

logger = logging.getLogger(__name__)


class LeverAdapter(BaseAdapter):
    """Adapter for Lever job boards."""

    ats_name = "lever"
    PRIMARY_URL = "https://api.lever.co/v0/postings/{token}"
    EU_URL = "https://api.eu.lever.co/v0/postings/{token}"

    def fetch(self) -> list[NormalizedJob]:  # noqa: D102
        url = self.PRIMARY_URL.format(token=self.token)
        try:
            resp = fetch_with_retry(url, params={"mode": "json"})
            if resp.status_code == 404:
                logger.info("Lever: 404 on primary, trying EU endpoint for %s", self.token)
                url = self.EU_URL.format(token=self.token)
                resp = fetch_with_retry(url, params={"mode": "json"})
            resp.raise_for_status()
        except Exception as exc:
            logger.error("Lever fetch failed for %s: %s", self.token, exc)
            return []

        try:
            jobs_raw = resp.json()
        except Exception as exc:
            logger.error("Lever: JSON parse error for %s: %s", self.token, exc)
            return []

        if not isinstance(jobs_raw, list):
            # Some Lever boards wrap in {"data": [...]}
            if isinstance(jobs_raw, dict):
                jobs_raw = jobs_raw.get("data", [])
            else:
                logger.warning("Lever: unexpected response shape for token %s", self.token)
                return []

        results: list[NormalizedJob] = []
        for raw in jobs_raw:
            try:
                categories = raw.get("categories") or {}
                location = categories.get("location", "") if isinstance(categories, dict) else ""
                is_remote = "remote" in location.lower()

                posted_at = self._parse_dt(raw.get("createdAt"))
                description = raw.get("descriptionPlain") or raw.get("description", "")

                results.append(
                    NormalizedJob(
                        company=self.company_name,
                        title=raw.get("text", ""),
                        location=location,
                        is_remote=is_remote,
                        url=raw.get("hostedUrl", ""),
                        posted_at=posted_at,
                        description=description,
                        ats=self.ats_name,
                        external_id=str(raw.get("id", "")),
                        token=self.token,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Lever: skipping malformed job entry: %s", exc)

        return results
