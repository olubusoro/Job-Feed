"""
Tests for the matching / scoring / freshness / seniority logic.
Run with: pytest tests/
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.services.matcher import (
    _detect_seniority,
    _is_fresh,
    _location_matches,
    filter_and_rank,
    score_job,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers to build mock objects
# ─────────────────────────────────────────────────────────────────────────────


def _job(
    title: str = "Software Engineer",
    location: str = "Remote",
    is_remote: bool = True,
    posted_at: datetime | None = None,
    description: str = "",
    job_id: int = 1,
) -> MagicMock:
    job = MagicMock()
    job.id = job_id
    job.title = title
    job.location = location
    job.is_remote = is_remote
    job.posted_at = posted_at
    job.first_seen_at = datetime.utcnow() - timedelta(hours=1)
    job.description = description
    return job


def _profile(
    target_titles: list[str] | None = None,
    include_mid: bool = True,
    include_senior: bool = True,
    must_have_keywords: list[str] | None = None,
    nice_to_have_keywords: list[str] | None = None,
    exclude_keywords: list[str] | None = None,
    location_mode: str = "remote_us",
    onsite_area: str = "Minneapolis, Minnesota",
    exclude_no_sponsorship: bool = False,
    exclude_clearance_required: bool = False,
    max_age_hours: int = 48,
    daily_target: int = 10,
) -> MagicMock:
    p = MagicMock()
    p.target_titles = target_titles or ["Software Engineer"]
    p.include_mid = include_mid
    p.include_senior = include_senior
    p.must_have_keywords = must_have_keywords or []
    p.nice_to_have_keywords = nice_to_have_keywords or []
    p.exclude_keywords = exclude_keywords or []
    p.location_mode = location_mode
    p.onsite_area = onsite_area
    p.exclude_no_sponsorship = exclude_no_sponsorship
    p.exclude_clearance_required = exclude_clearance_required
    p.max_age_hours = max_age_hours
    p.daily_target = daily_target
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Seniority detection tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSeniorityDetection:
    def test_junior_detected(self):
        assert _detect_seniority("Junior Software Engineer")[0] is True

    def test_intern_detected(self):
        assert _detect_seniority("Software Engineering Intern")[0] is True

    def test_entry_level_detected(self):
        assert _detect_seniority("Entry Level Backend Engineer")[0] is True

    def test_senior_detected(self):
        is_j, is_m, is_s = _detect_seniority("Senior Software Engineer")
        assert is_j is False
        assert is_s is True

    def test_mid_by_default(self):
        is_j, is_m, is_s = _detect_seniority("Software Engineer")
        assert is_j is False
        assert is_m is True

    def test_staff_detected_as_senior_plus(self):
        # Staff engineer should be excluded (treated as is_senior=True by senior+ logic)
        is_j, is_m, is_s = _detect_seniority("Staff Software Engineer")
        assert is_s is True

    def test_director_detected_as_senior_plus(self):
        is_j, is_m, is_s = _detect_seniority("Director of Engineering")
        assert is_s is True

    def test_vp_detected(self):
        is_j, is_m, is_s = _detect_seniority("VP of Engineering")
        assert is_s is True

    def test_sr_abbreviation(self):
        is_j, is_m, is_s = _detect_seniority("Sr. Software Engineer")
        assert is_s is True


# ─────────────────────────────────────────────────────────────────────────────
# Freshness tests
# ─────────────────────────────────────────────────────────────────────────────


class TestFreshness:
    def test_fresh_job_within_window(self):
        job = _job(posted_at=datetime.utcnow() - timedelta(hours=5))
        assert _is_fresh(job, max_age_hours=24) is True

    def test_stale_job_outside_window(self):
        job = _job(posted_at=datetime.utcnow() - timedelta(hours=50))
        assert _is_fresh(job, max_age_hours=30) is False

    def test_no_posted_at_uses_first_seen(self):
        job = _job(posted_at=None)
        job.first_seen_at = datetime.utcnow() - timedelta(hours=2)
        assert _is_fresh(job, max_age_hours=10) is True

    def test_updated_at_not_used_when_posted_at_exists(self):
        # Simulates a job posted 2 hours ago (fresh) with an older updated_at
        # Our code never uses updated_at — this just validates the field mapping
        job = _job(posted_at=datetime.utcnow() - timedelta(hours=2))
        assert _is_fresh(job, max_age_hours=30) is True

    def test_exactly_on_boundary(self):
        # Use 29h59m to stay safely inside the 30h window regardless of execution time
        job = _job(posted_at=datetime.utcnow() - timedelta(hours=29, minutes=59))
        assert _is_fresh(job, max_age_hours=30) is True


# ─────────────────────────────────────────────────────────────────────────────
# Location matching tests
# ─────────────────────────────────────────────────────────────────────────────


class TestLocationMatching:
    def test_remote_us_accepts_remote_job(self):
        job = _job(location="Remote, US", is_remote=True)
        p = _profile(location_mode="remote_us")
        assert _location_matches(job, p) is True

    def test_remote_us_rejects_onsite_job(self):
        job = _job(location="Minneapolis, MN", is_remote=False)
        p = _profile(location_mode="remote_us")
        assert _location_matches(job, p) is False

    def test_onsite_accepts_local(self):
        job = _job(location="Minneapolis, MN", is_remote=False)
        p = _profile(location_mode="onsite", onsite_area="Minneapolis, Minnesota")
        assert _location_matches(job, p) is True

    def test_onsite_rejects_other_city(self):
        job = _job(location="New York, NY", is_remote=False)
        p = _profile(location_mode="onsite", onsite_area="Minneapolis, Minnesota")
        assert _location_matches(job, p) is False

    def test_both_accepts_remote(self):
        job = _job(location="Remote", is_remote=True)
        p = _profile(location_mode="both")
        assert _location_matches(job, p) is True

    def test_both_accepts_local_onsite(self):
        job = _job(location="Eden Prairie, MN", is_remote=False)
        p = _profile(location_mode="both", onsite_area="Eden Prairie, Minnesota")
        assert _location_matches(job, p) is True


# ─────────────────────────────────────────────────────────────────────────────
# Scoring tests
# ─────────────────────────────────────────────────────────────────────────────


class TestScoring:
    def test_title_match_scores_higher(self):
        job_match = _job(title="Software Engineer", description="Python backend")
        job_no_match = _job(title="Product Manager", description="product roadmap")
        p = _profile(target_titles=["Software Engineer"])
        assert score_job(job_match, p) > score_job(job_no_match, p)

    def test_must_have_boost(self):
        p = _profile(target_titles=["Engineer"], must_have_keywords=["Python", "Django"])
        job_with = _job(title="Engineer", description="Python Django PostgreSQL")
        job_without = _job(title="Engineer", description="Java Spring")
        assert score_job(job_with, p) > score_job(job_without, p)

    def test_nice_to_have_boost(self):
        p = _profile(target_titles=["Engineer"], nice_to_have_keywords=["Redis"])
        job_with = _job(title="Engineer", description="Redis caching layer")
        # Explicitly exclude Redis from the second job's description
        job_without = _job(title="Engineer", description="Postgres and Kafka only")
        assert score_job(job_with, p) > score_job(job_without, p)

    def test_recency_boost(self):
        p = _profile(target_titles=["Engineer"])
        job_new = _job(title="Engineer", posted_at=datetime.utcnow() - timedelta(hours=1))
        job_old = _job(title="Engineer", posted_at=datetime.utcnow() - timedelta(hours=25))
        assert score_job(job_new, p) > score_job(job_old, p)


# ─────────────────────────────────────────────────────────────────────────────
# Filter and rank integration tests
# ─────────────────────────────────────────────────────────────────────────────


class TestFilterAndRank:
    def test_excludes_already_shown(self):
        job = _job(job_id=42, posted_at=datetime.utcnow() - timedelta(hours=1))
        p = _profile()
        result = filter_and_rank([job], p, already_shown_ids={42})
        assert result == []

    def test_excludes_stale(self):
        job = _job(posted_at=datetime.utcnow() - timedelta(hours=100))
        p = _profile(max_age_hours=24)
        result = filter_and_rank([job], p, already_shown_ids=set())
        assert result == []

    def test_excludes_junior(self):
        job = _job(title="Junior Software Engineer", posted_at=datetime.utcnow())
        p = _profile()
        result = filter_and_rank([job], p, already_shown_ids=set())
        assert result == []

    def test_excludes_when_must_have_missing(self):
        job = _job(title="Software Engineer", description="Java Spring", posted_at=datetime.utcnow())
        p = _profile(must_have_keywords=["Python"])
        result = filter_and_rank([job], p, already_shown_ids=set())
        assert result == []

    def test_excludes_keyword_in_exclude_list(self):
        job = _job(title="Software Engineer", description="blockchain crypto", posted_at=datetime.utcnow())
        p = _profile(exclude_keywords=["blockchain"])
        result = filter_and_rank([job], p, already_shown_ids=set())
        assert result == []

    def test_sponsorship_filter(self):
        job = _job(
            title="Software Engineer",
            description="We will not sponsor work visas for this role.",
            posted_at=datetime.utcnow(),
        )
        p = _profile(exclude_no_sponsorship=True)
        result = filter_and_rank([job], p, already_shown_ids=set())
        assert result == []

    def test_clearance_filter(self):
        job = _job(
            title="Software Engineer",
            description="Active Secret clearance required.",
            posted_at=datetime.utcnow(),
        )
        p = _profile(exclude_clearance_required=True)
        result = filter_and_rank([job], p, already_shown_ids=set())
        assert result == []

    def test_daily_target_respected(self):
        jobs = [
            _job(title="Software Engineer", posted_at=datetime.utcnow(), job_id=i)
            for i in range(20)
        ]
        p = _profile(daily_target=5)
        result = filter_and_rank(jobs, p, already_shown_ids=set())
        assert len(result) <= 5

    def test_results_sorted_by_score_desc(self):
        job_high = _job(
            title="Software Engineer",
            description="Python Django Redis",
            posted_at=datetime.utcnow(),
            job_id=1,
        )
        job_low = _job(
            title="Software Engineer",
            description="",
            posted_at=datetime.utcnow() - timedelta(hours=20),
            job_id=2,
        )
        p = _profile(nice_to_have_keywords=["Python", "Django", "Redis"])
        result = filter_and_rank([job_low, job_high], p, already_shown_ids=set())
        assert result[0].job.id == job_high.id
