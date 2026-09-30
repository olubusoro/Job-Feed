"""
Matching and scoring service.

Given a SearchProfile and a list of Job rows, returns the top-N jobs
that pass all hard filters, scored and ranked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from app.models import Job, SearchProfile

# ─────────────────────────────────────────────────────────────────────────────
# Seniority signal words
# ─────────────────────────────────────────────────────────────────────────────

_JUNIOR_SIGNALS = [
    r"\bjunior\b",
    r"\bjr\b",
    r"\bentry.?level\b",
    r"\bintern\b",
    r"\binternship\b",
    r"\bapprentice\b",
    r"\bgraduate\b",
    r"\bnew.?grad\b",
]

_SENIOR_SIGNALS = [
    r"\bsenior\b",
    r"\bsr\b",
    r"\bstaff\b",
    r"\bprincipal\b",
    r"\blead\b",
    r"\bdirector\b",
    r"\bvp\b",
    r"\bvice.?president\b",
    r"\bmanager\b",
    r"\bhead.?of\b",
]

_MID_SIGNALS = [
    r"\bmid.?level\b",
    r"\bii\b",
    r"\b2\b",  # e.g. "Engineer II"
]

# Compiled pattern lists
_RE_JUNIOR = [re.compile(p, re.IGNORECASE) for p in _JUNIOR_SIGNALS]
_RE_SENIOR = [re.compile(p, re.IGNORECASE) for p in _SENIOR_SIGNALS]
_RE_MID = [re.compile(p, re.IGNORECASE) for p in _MID_SIGNALS]

# ─────────────────────────────────────────────────────────────────────────────
# Sponsorship / clearance signal phrases
# ─────────────────────────────────────────────────────────────────────────────

_NO_SPONSORSHIP_PHRASES = [
    r"will not sponsor",
    r"cannot sponsor",
    r"no visa sponsor",
    r"does not sponsor",
    r"sponsorship not available",
    r"must be authorized to work",
    r"must be (a )?us citizen",
    r"must have (a )?work authorization",
    r"authorized to work in the (us|united states) without",
]

_CLEARANCE_PHRASES = [
    r"security clearance required",
    r"clearance required",
    r"must hold.*clearance",
    r"active (secret|top secret|ts/sci|ts-sci)",
    r"us citizenship required",
    r"must be a us citizen",
    r"citizen.*required",
    r"usc (only|required)",
]

_RE_NO_SPONSOR = [re.compile(p, re.IGNORECASE) for p in _NO_SPONSORSHIP_PHRASES]
_RE_CLEARANCE = [re.compile(p, re.IGNORECASE) for p in _CLEARANCE_PHRASES]

# ─────────────────────────────────────────────────────────────────────────────
# Twin Cities / Minnesota location keywords
# ─────────────────────────────────────────────────────────────────────────────

_TWIN_CITIES_KEYWORDS = [
    "minneapolis",
    "st. paul",
    "saint paul",
    "lakeville",
    "bloomington",
    "eden prairie",
    "minnesota",
    " mn",
    ",mn",
]


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────


def _word_match(text: str, keyword: str) -> bool:
    """True if *keyword* appears as a whole word in *text* (case-insensitive)."""
    pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
    return bool(pattern.search(text))


def _any_match(text: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


def _detect_seniority(title: str) -> tuple[bool, bool, bool]:
    """
    Returns (is_junior, is_mid, is_senior) based on title signals.
    A title with no seniority signals is treated as mid-level.
    """
    is_junior = _any_match(title, _RE_JUNIOR)
    # Staff/principal/lead/director/VP etc. are "senior-plus" (excluded by default)
    is_senior_plus = any(
        p.search(title)
        for p in _RE_SENIOR
        if p.pattern not in (r"\bsenior\b", r"\bsr\b")
    )
    is_senior = bool(re.search(r"\b(senior|sr)\b", title, re.IGNORECASE))
    is_mid = _any_match(title, _RE_MID) or (
        not is_junior and not is_senior and not is_senior_plus
    )
    return is_junior, is_mid, is_senior or is_senior_plus


def _location_matches(job: Job, profile: SearchProfile) -> bool:
    """True if job location passes the profile's location rules."""
    from app.models import LocationMode

    mode = profile.location_mode
    loc = (job.location or "").lower()

    remote_ok = job.is_remote or "remote" in loc
    onsite_area_raw = (profile.onsite_area or "").lower()
    area_terms = [t.strip() for t in onsite_area_raw.split(",") if t.strip()]
    onsite_ok = any(term in loc for term in area_terms)

    if mode == LocationMode.remote_us.value:
        return remote_ok
    elif mode == LocationMode.onsite.value:
        return onsite_ok
    else:  # both
        return remote_ok or onsite_ok


def _is_fresh(job: Job, max_age_hours: int) -> bool:
    """True if the job's effective date is within max_age_hours of now."""
    now = datetime.utcnow()
    # Use posted_at if present, else fall back to first_seen_at
    effective_date = job.posted_at or job.first_seen_at
    if effective_date is None:
        return False
    age = now - effective_date
    return age <= timedelta(hours=max_age_hours)


# ─────────────────────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ScoredJob:
    job: Job
    score: float
    breakdown: dict = field(default_factory=dict)


def score_job(job: Job, profile: SearchProfile) -> float:
    """
    Return a numeric score for *job* against *profile*.
    Higher = better match.
    """
    score = 0.0
    title = job.title or ""
    combined = f"{title} {job.description or ''}"

    # Title match (40 pts base, +10 per additional title hit)
    title_hits = sum(
        1 for t in profile.target_titles if _word_match(title, t)
    )
    score += title_hits * 40

    # Must-have keywords (10 pts each, up to 30 bonus)
    must_hits = sum(
        1 for kw in (profile.must_have_keywords or []) if _word_match(combined, kw)
    )
    score += min(must_hits * 10, 30)

    # Nice-to-have keywords (5 pts each, up to 25 bonus)
    nice_hits = sum(
        1 for kw in (profile.nice_to_have_keywords or []) if _word_match(combined, kw)
    )
    score += min(nice_hits * 5, 25)

    # Recency bonus (up to 10 pts: newer = more)
    now = datetime.utcnow()
    effective_date = job.posted_at or job.first_seen_at
    if effective_date:
        age_hours = max(0.0, (now - effective_date).total_seconds() / 3600)
        score += max(0.0, 10.0 - age_hours / 3.0)

    return round(score, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Main filter + rank function
# ─────────────────────────────────────────────────────────────────────────────


def filter_and_rank(
    jobs: list[Job],
    profile: SearchProfile,
    already_shown_ids: set[int],
) -> list[ScoredJob]:
    """
    Apply all hard filters then rank by score.
    Returns at most profile.daily_target ScoredJob objects (new to this user).
    """
    candidates: list[ScoredJob] = []

    for job in jobs:
        # Skip already shown
        if job.id in already_shown_ids:
            continue

        title = job.title or ""
        combined = f"{title} {job.description or ''}"

        # ── Freshness ──────────────────────────────────────────────────────
        if not _is_fresh(job, profile.max_age_hours):
            continue

        # ── Seniority ──────────────────────────────────────────────────────
        is_junior, is_mid, is_senior = _detect_seniority(title)

        if is_junior:
            continue  # Always exclude junior/intern/entry-level

        if is_senior and not profile.include_senior:
            continue

        # Detect staff/principal/director/manager etc. (senior-plus roles)
        is_senior_plus = any(
            p.search(title)
            for p in _RE_SENIOR
            if p.pattern not in (r"\bsenior\b", r"\bsr\b")
        )
        if is_senior_plus:
            continue  # Always exclude these "above senior" roles

        if is_mid and not profile.include_mid:
            continue

        # ── Target title match ─────────────────────────────────────────────
        if profile.target_titles:
            if not any(_word_match(title, t) for t in profile.target_titles):
                continue

        # ── Must-have keywords ─────────────────────────────────────────────
        for kw in profile.must_have_keywords or []:
            if not _word_match(combined, kw):
                break
        else:
            pass  # All must-have keywords present
        # Redo with a flag (the for-else above has a bug; use this pattern):
        if profile.must_have_keywords:
            missing = any(
                not _word_match(combined, kw) for kw in profile.must_have_keywords
            )
            if missing:
                continue

        # ── Exclude keywords ───────────────────────────────────────────────
        if any(_word_match(combined, kw) for kw in (profile.exclude_keywords or [])):
            continue

        # ── Location ───────────────────────────────────────────────────────
        if not _location_matches(job, profile):
            continue

        # ── Work auth: sponsorship ─────────────────────────────────────────
        if profile.exclude_no_sponsorship and _any_match(combined, _RE_NO_SPONSOR):
            continue

        # ── Work auth: clearance ───────────────────────────────────────────
        if profile.exclude_clearance_required and _any_match(combined, _RE_CLEARANCE):
            continue

        candidates.append(ScoredJob(job=job, score=score_job(job, profile)))

    # Sort by score descending, then by posted_at descending (newer first)
    candidates.sort(
        key=lambda s: (s.score, (s.job.posted_at or s.job.first_seen_at or datetime.min)),
        reverse=True,
    )

    return candidates[: profile.daily_target]
