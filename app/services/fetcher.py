"""
Job fetcher service.

Orchestrates concurrent fetching from all active companies using a
ThreadPoolExecutor (max 15 workers). Each company's adapter is called
independently; one failure never crashes the whole run.

Also runs per-profile adapters (Remotive, Jobicy) that are not tied to a
specific company token — these are called once per target title across all
active profiles and their results flow through the same upsert + matcher.

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
from app.adapters.remotive import fetch_remotive
from app.adapters.jobicy import fetch_jobicy
from app.config import settings
from app.database import SessionLocal
from app.models import Company, FetchRun, Job, SearchProfile

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

# Sentinel token used for all per-profile (global) adapter jobs
_GLOBAL_TOKEN = "__global__"


def _get_adapter(company: Company) -> Optional[BaseAdapter]:
    cls = ADAPTER_REGISTRY.get(company.ats)
    if cls is None:
        logger.warning("Unknown ATS '%s' for company '%s'", company.ats, company.name)
        return None
    return cls(company_name=company.name, token=company.token)


def _get_or_create_company(db: Session, ats: str, name: str) -> Company:
    """
    Look up a Company row by (ats, name). Create it if it doesn't exist.
    Used by global adapters (Remotive, Jobicy) where each job can come from
    a different company — we create a lightweight Company stub on the fly.
    """
    # Use token = name-slug so the UniqueConstraint (ats, token) stays unique per company.
    token = name.lower().replace(" ", "-")[:200] or _GLOBAL_TOKEN
    company = (
        db.query(Company)
        .filter(Company.ats == ats, Company.token == token)
        .first()
    )
    if company is None:
        company = Company(name=name, ats=ats, token=token, active=True)
        db.add(company)
        try:
            db.commit()
            db.refresh(company)
        except Exception:
            db.rollback()
            # Race condition: another thread created it — fetch again
            company = (
                db.query(Company)
                .filter(Company.ats == ats, Company.token == token)
                .first()
            )
    return company


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


def _upsert_global_jobs(db: Session, jobs: list[NormalizedJob]) -> int:
    """
    Upsert jobs from per-profile (global) adapters (Remotive, Jobicy).

    Unlike _upsert_jobs, each job can come from a different company, so we
    resolve (or create) a Company row per job based on ats + company name.
    Returns count of newly inserted jobs.
    """
    new_count = 0
    for job in jobs:
        company = _get_or_create_company(db, ats=job.ats, name=job.company)
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
            existing.title = job.title
            existing.location = job.location
            existing.is_remote = job.is_remote
            existing.description = job.description
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.warning("IntegrityError on global upsert, rolling back")
    return new_count


def _fetch_profile_sources(db: Session) -> tuple[int, int, list[str]]:
    """
    Run per-profile adapters (Remotive, Jobicy) for all active search profiles.

    Collects the union of target_titles across all active profiles (deduplicated)
    so we don't hammer the APIs with duplicate queries. Each unique title is
    searched once; results are upserted into the shared jobs table.

    Returns (total_fetched, total_new, errors).
    """
    # Collect all unique target titles from all active profiles
    profiles = db.query(SearchProfile).filter(SearchProfile.active == True).all()  # noqa: E712
    all_titles: set[str] = set()
    for p in profiles:
        for title in (p.target_titles or []):
            clean = title.strip()
            if clean:
                all_titles.add(clean)

    if not all_titles:
        logger.info("No target titles found across active profiles — skipping global adapters")
        return 0, 0, []

    logger.info(
        "Fetching global adapters (Remotive, Jobicy) for %d unique titles: %s",
        len(all_titles),
        list(all_titles),
    )

    total_fetched = 0
    total_new = 0
    errors: list[str] = []

    for title in all_titles:
        # ── Remotive ───────────────────────────────────────────────────────────
        try:
            jobs = fetch_remotive(title)
            total_fetched += len(jobs)
            total_new += _upsert_global_jobs(db, jobs)
        except Exception as exc:  # noqa: BLE001
            msg = f"Remotive/{title}: {exc}"
            errors.append(msg)
            logger.error("Global fetch error — %s", msg)

        # ── Jobicy ─────────────────────────────────────────────────────────────
        try:
            jobs = fetch_jobicy(title)
            total_fetched += len(jobs)
            total_new += _upsert_global_jobs(db, jobs)
        except Exception as exc:  # noqa: BLE001
            msg = f"Jobicy/{title}: {exc}"
            errors.append(msg)
            logger.error("Global fetch error — %s", msg)

    logger.info(
        "Global adapters done. FETCHED=%d NEW=%d ERRORS=%d",
        total_fetched,
        total_new,
        len(errors),
    )
    return total_fetched, total_new, errors


def run_fetch(triggered_by: str = "scheduler") -> FetchRun:
    """
    Main entry point. Fetches all active companies concurrently, then runs the
    per-profile global adapters (Remotive, Jobicy). Records a single FetchRun
    log entry for the entire run. Returns the completed FetchRun.
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

        # ── Phase 1: per-company adapters (Greenhouse, Lever, Ashby, etc.) ────
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

        # ── Phase 2: per-profile global adapters (Remotive, Jobicy) ──────────
        # These run synchronously (already fast — 1 HTTP call per title) after
        # the concurrent per-company phase. Their jobs go through the same
        # _upsert_global_jobs path and are included in the FetchRun totals.
        global_fetched, global_new, global_errors = _fetch_profile_sources(db)
        total_fetched += global_fetched
        total_new += global_new
        errors.extend(global_errors)
        # Count global sources as ok/fail at the adapter level
        if global_errors:
            fail_count += len(global_errors)
        else:
            ok_count += 1  # treat all global sources as one logical unit

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
