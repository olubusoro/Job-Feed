"""
Workable ATS adapter.
Public widget/jobs API endpoint: https://apply.workable.com/api/v3/accounts/{token}/jobs

Response shape (relevant fields, POST body {"query":"","location":"","department":"","worktype":"","remote":false}):
  {
    "results": [
      {
        "id": "abc-123",
        "title": "Software Engineer",
        "location": {"city": "Minneapolis", "country": "US", "region": "Minnesota"},
        "remote": true,
        "url": "https://apply.workable.com/company/j/...",
        "created_at": "2024-01-15T10:00:00Z",
        "description": "..."
      }
    ]
  }

Note: Workable's public API uses a POST endpoint with a JSON body.
"""

from __future__ import annotations

import logging

import httpx

from app.adapters.base import BaseAdapter, NormalizedJob
from app.config import settings

logger = logging.getLogger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}


class WorkableAdapter(BaseAdapter):
    """Adapter for Workable job boards."""

    ats_name = "workable"
    JOBS_URL = "https://apply.workable.com/api/v3/accounts/{token}/jobs"

    def fetch(self) -> list[NormalizedJob]:  # noqa: D102
        url = self.JOBS_URL.format(token=self.token)
        payload = {
            "query": "",
            "location": [],
            "department": [],
            "worktype": [],
            "remote": False,
        }
        headers = {
            "User-Agent": settings.HTTP_USER_AGENT,
            "Content-Type": "application/json",
        }
        import time
        backoff = 1.0
        last_exc: Exception = RuntimeError("No attempts")

        for attempt in range(settings.HTTP_MAX_RETRIES):
            try:
                with httpx.Client(
                    timeout=settings.HTTP_TIMEOUT,
                    headers=headers,
                    follow_redirects=True,
                ) as client:
                    resp = client.post(url, json=payload)
                    if resp.status_code in _RETRY_STATUS:
                        raise httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}",
                            request=resp.request,
                            response=resp,
                        )
                    if resp.status_code == 404:
                        logger.warning("Workable: 404 for token %s", self.token)
                        return []
                    resp.raise_for_status()
                    data = resp.json()
                    break
            except Exception as exc:
                last_exc = exc
                if attempt < settings.HTTP_MAX_RETRIES - 1:
                    time.sleep(backoff * (2**attempt))
        else:
            logger.error("Workable fetch failed for %s: %s", self.token, last_exc)
            return []

        jobs_raw = data.get("results", [])
        if not isinstance(jobs_raw, list):
            logger.warning("Workable: unexpected response for token %s", self.token)
            return []

        results: list[NormalizedJob] = []
        for raw in jobs_raw:
            try:
                loc_obj = raw.get("location") or {}
                if isinstance(loc_obj, dict):
                    parts = filter(None, [
                        loc_obj.get("city"),
                        loc_obj.get("region"),
                        loc_obj.get("country"),
                    ])
                    location = ", ".join(parts)
                else:
                    location = str(loc_obj)
                is_remote = bool(raw.get("remote", False)) or "remote" in location.lower()
                posted_at = self._parse_dt(raw.get("created_at") or raw.get("published_on"))
                description = raw.get("description", "")

                results.append(
                    NormalizedJob(
                        company=self.company_name,
                        title=raw.get("title", ""),
                        location=location,
                        is_remote=is_remote,
                        url=raw.get("url", ""),
                        posted_at=posted_at,
                        description=description,
                        ats=self.ats_name,
                        external_id=str(raw.get("id", raw.get("shortcode", ""))),
                        token=self.token,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Workable: skipping malformed job entry: %s", exc)

        return results
