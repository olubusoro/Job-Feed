"""
JobFeed — FastAPI main application.
Routes for auth, dashboard, profiles, admin, and API endpoints.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.auth import (
    create_session,
    delete_session,
    get_current_user,
    hash_password,
    require_admin,
    require_user,
    verify_password,
)
from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.models import (
    Company,
    FetchRun,
    Job,
    JobStatus,
    SearchProfile,
    User,
    UserJob,
)
from app.scheduler import start_scheduler, stop_scheduler
from app.seed import seed
from app.services.excel import generate_excel
from app.services.fetcher import run_fetch
from app.services.matcher import filter_and_rank

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title=settings.APP_TITLE, docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory="app/templates")


def _timeago(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    delta = datetime.utcnow() - dt
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        mins = int(delta.total_seconds() // 60)
        return f"{mins}m ago" if mins > 0 else "Just now"
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


templates.env.filters["timeago"] = _timeago


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    seed()
    start_scheduler()
    # Auto-promote ADMIN_EMAIL user if configured
    if settings.ADMIN_EMAIL:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == settings.ADMIN_EMAIL).first()
            if user and not user.is_admin:
                user.is_admin = True
                db.commit()
        finally:
            db.close()


@app.on_event("shutdown")
def on_shutdown() -> None:
    stop_scheduler()


# ─────────────────────────────────────────────────────────────────────────────
# Auth routes
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, current_user: Optional[User] = Depends(get_current_user)):
    if current_user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "current_user": None})


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "current_user": None, "error": "Invalid email or password"},
            status_code=401,
        )
    token = create_session(user.id)
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        "session_token",
        token,
        max_age=settings.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return resp


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, current_user: Optional[User] = Depends(get_current_user)):
    if current_user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("register.html", {"request": request, "current_user": None})


@app.post("/register")
def register(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    invite_code: str = Form(...),
    db: Session = Depends(get_db),
):
    def _err(msg: str):
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "current_user": None, "error": msg},
            status_code=400,
        )

    if invite_code.strip() != settings.INVITE_CODE:
        return _err("Invalid invite code.")
    if len(password) < 8:
        return _err("Password must be at least 8 characters.")
    email = email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        return _err("An account with this email already exists.")

    is_admin = email == settings.ADMIN_EMAIL.lower() if settings.ADMIN_EMAIL else False
    user = User(
        email=email,
        hashed_password=hash_password(password),
        display_name=display_name.strip(),
        is_admin=is_admin,
    )
    db.add(user)
    db.flush()

    # Create default search profile
    profile = SearchProfile(
        user_id=user.id,
        name="My Profile",
        target_titles=["Software Engineer", "Backend Engineer", "Full Stack Engineer"],
        include_mid=True,
        include_senior=True,
        must_have_keywords=[],
        nice_to_have_keywords=[],
        exclude_keywords=[],
        exclude_no_sponsorship=True,
        exclude_clearance_required=True,
        max_age_hours=30,
        daily_target=100,
    )
    db.add(profile)
    db.commit()

    token = create_session(user.id)
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie("session_token", token, max_age=settings.SESSION_MAX_AGE, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout(request: Request):
    session_token = request.cookies.get("session_token")
    if session_token:
        delete_session(session_token)
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session_token")
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────


def _get_or_create_user_jobs(
    db: Session,
    user: User,
    profile: SearchProfile,
) -> list[UserJob]:
    """
    Run matching for this profile, add new UserJob rows, and return
    all UserJob rows for today's dashboard (today = last 24 h of shown_at).
    """
    # Gather already-shown job IDs for this profile
    shown_ids: set[int] = {
        uj.job_id
        for uj in db.query(UserJob.job_id)
        .filter(UserJob.user_id == user.id, UserJob.profile_id == profile.id)
        .all()
    }

    # Load all candidate jobs from DB
    cutoff_date = datetime.utcnow() - timedelta(hours=profile.max_age_hours + 48)
    all_jobs = (
        db.query(Job)
        .options(joinedload(Job.company))
        .filter(
            (Job.posted_at >= cutoff_date) |
            ((Job.posted_at.is_(None)) & (Job.first_seen_at >= cutoff_date))
        )
        .all()
    )

    # Filter & rank
    scored = filter_and_rank(all_jobs, profile, shown_ids)

    # Persist new UserJob rows
    for rank, sj in enumerate(scored, start=1):
        uj = UserJob(
            user_id=user.id,
            profile_id=profile.id,
            job_id=sj.job.id,
            score=sj.score,
            rank=rank,
            status=JobStatus.new.value,
        )
        db.add(uj)

    if scored:
        try:
            db.commit()
        except Exception as exc:
            logger.error("Failed to persist UserJob rows: %s", exc)
            db.rollback()
            raise

    # Return today's UserJobs (shown in last 24 h)
    cutoff = datetime.utcnow() - timedelta(hours=24)
    return (
        db.query(UserJob)
        .options(joinedload(UserJob.job).joinedload(Job.company))
        .filter(
            UserJob.user_id == user.id,
            UserJob.profile_id == profile.id,
            UserJob.shown_at >= cutoff,
        )
        .order_by(UserJob.rank)
        .all()
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    profile_id: Optional[int] = None,
    q: Optional[str] = None,
    status: Optional[str] = None,
    ats: Optional[str] = None,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profiles = (
        db.query(SearchProfile)
        .filter(SearchProfile.user_id == current_user.id, SearchProfile.active == True)  # noqa: E712
        .all()
    )

    if not profiles:
        return RedirectResponse("/profiles/new", status_code=302)

    profile = next((p for p in profiles if p.id == profile_id), profiles[0])

    user_jobs = _get_or_create_user_jobs(db, current_user, profile)

    # Apply UI filters
    if q:
        q_lower = q.lower()
        user_jobs = [
            uj
            for uj in user_jobs
            if q_lower in (uj.job.title or "").lower()
            or q_lower in (uj.job.company.name if uj.job.company else "").lower()
        ]
    if status:
        user_jobs = [uj for uj in user_jobs if uj.status == status]
    if ats:
        user_jobs = [uj for uj in user_jobs if uj.job.ats == ats]

    from app.services.matcher import _location_matches
    for uj in user_jobs:
        _, region = _location_matches(uj.job)
        uj.job.region_tag = region

    # Stats
    now = datetime.utcnow()
    today_cutoff = now - timedelta(hours=24)
    week_cutoff = now - timedelta(days=7)

    applied_today = (
        db.query(UserJob)
        .filter(
            UserJob.user_id == current_user.id,
            UserJob.status == JobStatus.applied.value,
            UserJob.applied_at >= today_cutoff,
        )
        .count()
    )
    applied_week = (
        db.query(UserJob)
        .filter(
            UserJob.user_id == current_user.id,
            UserJob.status == JobStatus.applied.value,
            UserJob.applied_at >= week_cutoff,
        )
        .count()
    )
    applied_total = (
        db.query(UserJob)
        .filter(
            UserJob.user_id == current_user.id,
            UserJob.status == JobStatus.applied.value,
        )
        .count()
    )

    ats_list = sorted({uj.job.ats for uj in user_jobs if uj.job.ats})

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "profiles": profiles,
            "profile": profile,
            "user_jobs": user_jobs,
            "stats": {"today": applied_today, "week": applied_week, "total": applied_total},
            "filters": {"q": q, "status": status, "ats": ats},
            "ats_list": ats_list,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Profile routes
# ─────────────────────────────────────────────────────────────────────────────


def _parse_keywords(raw: str) -> list[str]:
    return [kw.strip() for kw in raw.split(",") if kw.strip()]


@app.get("/profiles", response_class=HTMLResponse)
def list_profiles(
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profiles = db.query(SearchProfile).filter(SearchProfile.user_id == current_user.id).all()
    return templates.TemplateResponse(
        "profiles.html",
        {"request": request, "current_user": current_user, "profiles": profiles},
    )


@app.get("/profiles/new", response_class=HTMLResponse)
def new_profile_page(request: Request, current_user: User = Depends(require_user)):
    return templates.TemplateResponse(
        "profile_edit.html",
        {"request": request, "current_user": current_user, "profile": None},
    )


@app.post("/profiles/new")
def create_profile(
    request: Request,
    name: str = Form("My Profile"),
    target_titles: str = Form(""),
    include_mid: Optional[str] = Form(None),
    include_senior: Optional[str] = Form(None),
    must_have_keywords: str = Form(""),
    nice_to_have_keywords: str = Form(""),
    exclude_keywords: str = Form(""),
    location_mode: str = Form("both"),
    onsite_area: str = Form(""),
    exclude_no_sponsorship: Optional[str] = Form(None),
    exclude_clearance_required: Optional[str] = Form(None),
    max_age_hours: int = Form(30),
    daily_target: int = Form(100),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profile = SearchProfile(
        user_id=current_user.id,
        name=name,
        target_titles=_parse_keywords(target_titles),
        include_mid=include_mid == "1",
        include_senior=include_senior == "1",
        must_have_keywords=_parse_keywords(must_have_keywords),
        nice_to_have_keywords=_parse_keywords(nice_to_have_keywords),
        exclude_keywords=_parse_keywords(exclude_keywords),
        exclude_no_sponsorship=exclude_no_sponsorship == "1",
        exclude_clearance_required=exclude_clearance_required == "1",
        max_age_hours=max(1, min(720, max_age_hours)),
        daily_target=max(1, min(500, daily_target)),
    )
    db.add(profile)
    db.commit()
    return RedirectResponse("/profiles", status_code=302)


@app.get("/profiles/{profile_id}/edit", response_class=HTMLResponse)
def edit_profile_page(
    profile_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profile = db.get(SearchProfile, profile_id)
    if not profile or profile.user_id != current_user.id:
        raise HTTPException(404)
    return templates.TemplateResponse(
        "profile_edit.html",
        {"request": request, "current_user": current_user, "profile": profile},
    )


@app.post("/profiles/{profile_id}/edit")
def update_profile(
    profile_id: int,
    request: Request,
    name: str = Form("My Profile"),
    target_titles: str = Form(""),
    include_mid: Optional[str] = Form(None),
    include_senior: Optional[str] = Form(None),
    must_have_keywords: str = Form(""),
    nice_to_have_keywords: str = Form(""),
    exclude_keywords: str = Form(""),
    location_mode: str = Form("both"),
    onsite_area: str = Form(""),
    exclude_no_sponsorship: Optional[str] = Form(None),
    exclude_clearance_required: Optional[str] = Form(None),
    max_age_hours: int = Form(30),
    daily_target: int = Form(100),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profile = db.get(SearchProfile, profile_id)
    if not profile or profile.user_id != current_user.id:
        raise HTTPException(404)

    profile.name = name
    profile.target_titles = _parse_keywords(target_titles)
    profile.include_mid = include_mid == "1"
    profile.include_senior = include_senior == "1"
    profile.must_have_keywords = _parse_keywords(must_have_keywords)
    profile.nice_to_have_keywords = _parse_keywords(nice_to_have_keywords)
    profile.exclude_keywords = _parse_keywords(exclude_keywords)
    profile.exclude_no_sponsorship = exclude_no_sponsorship == "1"
    profile.exclude_clearance_required = exclude_clearance_required == "1"
    profile.max_age_hours = max(1, min(720, max_age_hours))
    profile.daily_target = max(1, min(500, daily_target))
    db.commit()
    return RedirectResponse("/profiles", status_code=302)


@app.post("/profiles/{profile_id}/delete")
def delete_profile(
    profile_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profile = db.get(SearchProfile, profile_id)
    if not profile or profile.user_id != current_user.id:
        raise HTTPException(404)
    db.delete(profile)
    db.commit()
    return RedirectResponse("/profiles", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# HTMX API endpoints
# ─────────────────────────────────────────────────────────────────────────────


@app.post("/api/refresh", response_class=HTMLResponse)
def api_refresh(current_user: User = Depends(require_user)):
    """Trigger a manual fetch run in a background thread."""
    import threading

    def _bg():
        run_fetch(triggered_by=f"manual:{current_user.email}")

    threading.Thread(target=_bg, daemon=True).start()
    return HTMLResponse(
        '<div class="alert alert-info" style="margin:0;">🔄 Fetch started in background. Refresh the page in a minute.</div>'
    )


@app.post("/api/user-job/{uj_id}/status", response_class=HTMLResponse)
def update_status(
    uj_id: int,
    request: Request,
    status: str = Form(...),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    uj = (
        db.query(UserJob)
        .options(joinedload(UserJob.job).joinedload(Job.company))
        .filter(UserJob.id == uj_id)
        .first()
    )
    if not uj or uj.user_id != current_user.id:
        raise HTTPException(404)
    uj.status = status
    if status == JobStatus.applied.value:
        uj.applied_at = datetime.utcnow()
    db.commit()
    db.refresh(uj)

    # Re-render just the row via the same template fragment
    job = uj.job
    posted_str = _timeago(job.posted_at or job.first_seen_at)
    return HTMLResponse(f"""
    <tr id="row-{uj.id}">
      <td class="text-muted">{uj.rank}</td>
      <td><strong>{job.company.name if job.company else '—'}</strong></td>
      <td>{job.title}</td>
      <td>{job.location or '—'} {'<span class="badge badge-remote">Remote</span>' if job.is_remote else ''}</td>
      <td class="text-muted" style="white-space:nowrap;">{posted_str}</td>
      <td><span class="score-pill">{int(uj.score)}</span></td>
      <td><span class="badge badge-ats">{job.ats}</span></td>
      <td>
        <select class="status-select"
                hx-post="/api/user-job/{uj.id}/status"
                hx-target="#row-{uj.id}"
                hx-swap="outerHTML"
                name="status">
          {''.join(f'<option value="{s}" {"selected" if uj.status == s else ""}>{s}</option>' for s in ['New','Applied','Skipped','Interview'])}
        </select>
      </td>
      <td>
        <input type="text" class="notes-input"
               placeholder="Add note…"
               value="{uj.notes or ''}"
               hx-post="/api/user-job/{uj.id}/notes"
               hx-trigger="change"
               hx-target="#note-saved-{uj.id}"
               hx-swap="innerHTML"
               name="notes" />
        <span id="note-saved-{uj.id}" class="saved-flash"></span>
      </td>
      <td>
        <a href="{job.url}" target="_blank" rel="noopener" class="btn btn-primary btn-sm">Apply</a>
      </td>
    </tr>
    """)


@app.post("/api/user-job/{uj_id}/notes", response_class=HTMLResponse)
def update_notes(
    uj_id: int,
    notes: str = Form(""),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    uj = db.get(UserJob, uj_id)
    if not uj or uj.user_id != current_user.id:
        raise HTTPException(404)
    uj.notes = notes
    db.commit()
    return HTMLResponse('<span class="saved-flash">✓ Saved</span>')


# ─────────────────────────────────────────────────────────────────────────────
# Excel export
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/export/excel")
def export_excel(
    profile_id: int,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    cutoff = datetime.utcnow() - timedelta(hours=24)
    user_jobs = (
        db.query(UserJob)
        .options(joinedload(UserJob.job).joinedload(Job.company))
        .filter(
            UserJob.user_id == current_user.id,
            UserJob.profile_id == profile_id,
            UserJob.shown_at >= cutoff,
        )
        .order_by(UserJob.rank)
        .all()
    )
    xlsx_bytes = generate_excel(user_jobs)
    filename = f"jobfeed_{datetime.utcnow().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Admin routes
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    msg: Optional[str] = None,
    error: Optional[str] = None,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    companies = db.query(Company).order_by(Company.ats, Company.name).all()
    fetch_runs = db.query(FetchRun).order_by(FetchRun.id.desc()).limit(50).all()
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "current_user": current_user,
            "companies": companies,
            "fetch_runs": fetch_runs,
            "msg": msg,
            "error": error,
        },
    )


@app.post("/admin/companies/import")
def import_companies(
    request: Request,
    import_data: str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Parse JSON or CSV and bulk-import companies."""
    added = 0
    errors: list[str] = []
    raw = import_data.strip()

    try:
        if raw.startswith("[") or raw.startswith("{"):
            items = json.loads(raw)
            if isinstance(items, dict):
                items = [items]
        else:
            reader = csv.DictReader(io.StringIO(raw))
            items = list(reader)
    except Exception as exc:
        return RedirectResponse(f"/admin?error=Parse+error:+{exc}", status_code=302)

    for item in items:
        name = str(item.get("name", "")).strip()
        ats = str(item.get("ats", "")).strip().lower()
        token = str(item.get("token", "")).strip()
        if not name or not ats or not token:
            errors.append(f"Skipped incomplete row: {item}")
            continue
        existing = (
            db.query(Company).filter(Company.ats == ats, Company.token == token).first()
        )
        if not existing:
            db.add(Company(name=name, ats=ats, token=token))
            added += 1

    db.commit()
    msg = f"Imported {added} companies."
    if errors:
        msg += f" Skipped {len(errors)} rows."
    return RedirectResponse(f"/admin?msg={msg}", status_code=302)


@app.post("/admin/companies/{company_id}/toggle")
def toggle_company(
    company_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(404)
    company.active = not company.active
    company.consecutive_failures = 0
    db.commit()
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/companies/{company_id}/delete")
def delete_company(
    company_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(404)
    db.delete(company)
    db.commit()
    return RedirectResponse("/admin", status_code=302)
