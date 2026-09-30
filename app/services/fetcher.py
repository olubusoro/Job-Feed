"""
Job fetcher service.

Orchestrates concurrent fetching from all active companies using a
ThreadPoolExecutor (max 15 workers). Each company's adapter is called
independently; one failure never crashes the whole run.

Results are persisted to the DB (upsert semantics).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.base import BaseAdapter, NormalizedJob
from app.adapters.greenhouse import GreenhouseAdapter
from app.adapters.lever import LeverAdapter
from app.adapters.ashby import AshbyAdapter
from app.adapters.workable import WorkableAdapter
from app.adapters.smartrecruiters import SmartRecruitersAdapter
from app.adapters.recruitee import RecruiteeAdapter
from app.config import settings
from app.database import SessionLocal
from app.models import Company, FetchRun, Job

logger = logging.getLogger(__name__)

# Map ATS name → adapter class
ADAPTER_REGISTRY: dict[str, type[BaseAdapter]] = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
    "workable": WorkableAdapter,
    "smartrecruiters": SmartRecruitersAdapter,
    "recruitee": RecruiteeAdapter,
}

MAX_CONSECUTIVE_FAILURES = 5  # Flag company inactive after this many runs with 0 jobs


def _get_adapter(company: Company) -> Optional[BaseAdapter]:
    cls = ADAPTER_REGISTRY.get(company.ats)
    if cls is None:
        logger.warning("Unknown ATS '%s' for company '%s'", company.ats, company.name)
        return None
    return cls(company_name=company.name, token=company.token)


def _fetch_one(company_id: int) -> tuple[int, list[NormalizedJob], Optional[str]]:
    """
    Fetch jobs for a single company. Runs in a thread.
    Returns (company_id, jobs, error_message_or_None).
    """
    db: Session = SessionLocal()
    try:
        company = db.get(Company, company_id)
        if company is None:
            return company_id, [], "Company not found"
        adapter = _get_adapter(company)
        if adapter is None:
            return company_id, [], f"No adapter for ATS '{company.ats}'"
        jobs = adapter.fetch()
        return company_id, jobs, None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error fetching company_id=%d", company_id)
        return company_id, [], str(exc)
    finally:
        db.close()


def _upsert_jobs(db: Session, company: Company, jobs: list[NormalizedJob]) -> int:
    """
    Insert new jobs; skip existing ones (by ats + external_id).
    Returns count of newly inserted jobs.
    """
    new_count = 0
    for job in jobs:
        existing = (
            db.query(Job)
            .filter(Job.ats == job.ats, Job.external_id == job.external_id)
            .first()
        )
        if existing is None:
            db_job = Job(
                company_id=company.id,
                external_id=job.external_id,
                ats=job.ats,
                title=job.title,
                location=job.location,
                is_remote=job.is_remote,
                url=job.url,
                posted_at=job.posted_at,
                description=job.description,
                first_seen_at=datetime.utcnow(),
            )
            db.add(db_job)
            new_count += 1
        else:
            # Update mutable fields but never overwrite first_seen_at
            existing.title = job.title
            existing.location = job.location
            existing.is_remote = job.is_remote
            existing.description = job.description
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.warning("IntegrityError on upsert for company_id=%d, rolling back", company.id)
    return new_count


def run_fetch(triggered_by: str = "scheduler") -> FetchRun:
    """
    Main entry point. Fetches all active companies concurrently and
    records a FetchRun log entry. Returns the completed FetchRun.
    """
    db: Session = SessionLocal()
    try:
        fetch_run = FetchRun(started_at=datetime.utcnow(), triggered_by=triggered_by)
        db.add(fetch_run)
        db.commit()
        db.refresh(fetch_run)

        companies = db.query(Company).filter(Company.active == True).all()  # noqa: E712
        company_ids = [c.id for c in companies]
        logger.info("Starting fetch run #%d for %d companies", fetch_run.id, len(company_ids))

        ok_count = 0
        fail_count = 0
        total_fetched = 0
        total_new = 0
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=settings.HTTP_MAX_WORKERS) as executor:
            future_to_id = {
                executor.submit(_fetch_one, cid): cid for cid in company_ids
            }
            for future in as_completed(future_to_id):
                company_id, jobs, error = future.result()
                company = db.get(Company, company_id)
                if company is None:
                    continue

                if error:
                    fail_count += 1
                    company.consecutive_failures += 1
                    errors.append(f"{company.name}: {error}")
                    logger.warning("Failed: %s — %s", company.name, error)
                    if company.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        company.active = False
                        logger.warning(
                            "Deactivating company %s after %d consecutive failures",
                            company.name,
                            company.consecutive_failures,
                        )
                else:
                    ok_count += 1
                    company.consecutive_failures = 0
                    company.last_fetched_at = datetime.utcnow()
                    total_fetched += len(jobs)
                    new = _upsert_jobs(db, company, jobs)
                    total_new += new
                    logger.info(
                        "OK: %s — %d jobs fetched, %d new",
                        company.name,
                        len(jobs),
                        new,
                    )

        db.commit()

        fetch_run.finished_at = datetime.utcnow()
        fetch_run.companies_ok = ok_count
        fetch_run.companies_failed = fail_count
        fetch_run.jobs_fetched = total_fetched
        fetch_run.jobs_new = total_new
        fetch_run.error_summary = "\n".join(errors[:50])  # Truncate for DB
        db.commit()
        db.refresh(fetch_run)

        logger.info(
            "Fetch run #%d done. OK=%d FAIL=%d FETCHED=%d NEW=%d",
            fetch_run.id,
            ok_count,
            fail_count,
            total_fetched,
            total_new,
        )
        return fetch_run
    finally:
        db.close()
