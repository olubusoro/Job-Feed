"""
SQLAlchemy ORM models for JobFeed.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────


class ATSType(str, enum.Enum):
    greenhouse = "greenhouse"
    lever = "lever"
    ashby = "ashby"
    workable = "workable"
    smartrecruiters = "smartrecruiters"
    recruitee = "recruitee"


class JobStatus(str, enum.Enum):
    new = "New"
    applied = "Applied"
    skipped = "Skipped"
    interview = "Interview"


# ─────────────────────────────────────────────────────────────────────────────
# User
# ─────────────────────────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    profiles: Mapped[list["SearchProfile"]] = relationship(
        "SearchProfile", back_populates="user", cascade="all, delete-orphan"
    )
    user_jobs: Mapped[list["UserJob"]] = relationship(
        "UserJob", back_populates="user", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Search Profile
# ─────────────────────────────────────────────────────────────────────────────


class SearchProfile(Base):
    __tablename__ = "search_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="My Profile")

    # Titles
    target_titles: Mapped[list] = mapped_column(JSON, default=list)

    # Seniority
    include_mid: Mapped[bool] = mapped_column(Boolean, default=True)
    include_senior: Mapped[bool] = mapped_column(Boolean, default=True)

    # Keywords
    must_have_keywords: Mapped[list] = mapped_column(JSON, default=list)
    nice_to_have_keywords: Mapped[list] = mapped_column(JSON, default=list)
    exclude_keywords: Mapped[list] = mapped_column(JSON, default=list)

    # Work auth
    exclude_no_sponsorship: Mapped[bool] = mapped_column(Boolean, default=True)
    exclude_clearance_required: Mapped[bool] = mapped_column(Boolean, default=True)

    # Freshness / size
    max_age_hours: Mapped[int] = mapped_column(Integer, default=30)
    daily_target: Mapped[int] = mapped_column(Integer, default=100)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped["User"] = relationship("User", back_populates="profiles")
    user_jobs: Mapped[list["UserJob"]] = relationship(
        "UserJob", back_populates="profile", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Company
# ─────────────────────────────────────────────────────────────────────────────


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    ats: Mapped[str] = mapped_column(String(50), nullable=False)
    token: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("ats", "token", name="uq_company_ats_token"),)

    jobs: Mapped[list["Job"]] = relationship(
        "Job", back_populates="company", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Job
# ─────────────────────────────────────────────────────────────────────────────


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)

    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    ats: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    location: Mapped[str] = mapped_column(String(300), default="")
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("ats", "external_id", name="uq_job_ats_external_id"),
    )

    company: Mapped["Company"] = relationship("Company", back_populates="jobs")
    user_jobs: Mapped[list["UserJob"]] = relationship(
        "UserJob", back_populates="job", cascade="all, delete-orphan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# UserJob — tracks what has been shown/applied per user per profile
# ─────────────────────────────────────────────────────────────────────────────


class UserJob(Base):
    __tablename__ = "user_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("search_profiles.id"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)

    score: Mapped[float] = mapped_column(Float, default=0.0)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.new.value)
    notes: Mapped[str] = mapped_column(Text, default="")

    shown_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("user_id", "profile_id", "job_id", name="uq_user_profile_job"),
    )

    user: Mapped["User"] = relationship("User", back_populates="user_jobs")
    profile: Mapped["SearchProfile"] = relationship(
        "SearchProfile", back_populates="user_jobs"
    )
    job: Mapped["Job"] = relationship("Job", back_populates="user_jobs")


# ─────────────────────────────────────────────────────────────────────────────
# FetchRun — log of each scheduled / manual run
# ─────────────────────────────────────────────────────────────────────────────


class FetchRun(Base):
    __tablename__ = "fetch_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    companies_ok: Mapped[int] = mapped_column(Integer, default=0)
    companies_failed: Mapped[int] = mapped_column(Integer, default=0)
    jobs_fetched: Mapped[int] = mapped_column(Integer, default=0)
    jobs_new: Mapped[int] = mapped_column(Integer, default=0)
    triggered_by: Mapped[str] = mapped_column(String(50), default="scheduler")
    error_summary: Mapped[str] = mapped_column(Text, default="")
