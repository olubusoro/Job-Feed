"""
SmartRecruiters ATS adapter.
API: https://api.smartrecruiters.com/v1/companies/{token}/postings

Response shape (relevant fields):
  {
    "content": [
      {
        "id": "abc-123",
        "name": "Software Engineer",
        "location": {
          "city": "Minneapolis",
          "region": "MN",
          "country": "us",
          "remote": true
        },
        "ref": "https://jobs.smartrecruiters.com/...",
        "releasedDate": "2024-01-15T10:00:00.000+0000",
        "jobAd": {"sections": {"jobDescription": {"text": "..."}}}
      }
    ]
  }
"""

from __future__ import annotations

import logging

from app.adapters.base import BaseAdapter, NormalizedJob, fetch_with_retry

logger = logging.getLogger(__name__)


class SmartRecruitersAdapter(BaseAdapter):
    """Adapter for SmartRecruiters job boards."""

    ats_name = "smartrecruiters"
    BASE_URL = "https://api.smartrecruiters.com/v1/companies/{token}/postings"

    def fetch(self) -> list[NormalizedJob]:  # noqa: D102
        url = self.BASE_URL.format(token=self.token)
        all_jobs: list[NormalizedJob] = []
        offset = 0
        limit = 100

        while True:
            try:
                resp = fetch_with_retry(
                    url,
                    params={"limit": limit, "offset": offset, "status": "ACTIVE"},
                )
                resp.raise_for_status()
            except Exception as exc:
                logger.error(
                    "SmartRecruiters fetch failed for %s (offset=%d): %s",
                    self.token,
                    offset,
                    exc,
                )
                break

            try:
                data = resp.json()
            except Exception as exc:
                logger.error("SmartRecruiters: JSON parse error for %s: %s", self.token, exc)
                break

            jobs_raw = data.get("content", [])
            if not isinstance(jobs_raw, list) or not jobs_raw:
                break

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
                        is_remote = bool(loc_obj.get("remote", False))
                    else:
                        location = str(loc_obj)
                        is_remote = "remote" in location.lower()

                    posted_at = self._parse_dt(raw.get("releasedDate"))
                    job_ad = raw.get("jobAd") or {}
                    sections = job_ad.get("sections") or {}
                    desc = ""
                    for section_key in ("jobDescription", "qualifications", "additionalInformation"):
                        section = sections.get(section_key) or {}
                        desc += section.get("text", "") + "\n"

                    all_jobs.append(
                        NormalizedJob(
                            company=self.company_name,
                            title=raw.get("name", ""),
                            location=location,
                            is_remote=is_remote,
                            url=raw.get("ref", ""),
                            posted_at=posted_at,
                            description=desc.strip(),
                            ats=self.ats_name,
                            external_id=str(raw.get("id", "")),
                            token=self.token,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("SmartRecruiters: skipping malformed job entry: %s", exc)

            # Pagination
            total = data.get("totalFound", 0)
            offset += limit
            if offset >= total:
                break

        return all_jobs
