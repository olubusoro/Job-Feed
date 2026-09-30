"""
Database seed script.
Creates tables and populates companies with real ATS tokens (~5 per ATS).
Run once: python -m app.seed
"""

from __future__ import annotations

import logging

from app.database import Base, SessionLocal, engine
from app.models import Company

logger = logging.getLogger(__name__)

# ── Real companies per ATS ────────────────────────────────────────────────────
SEED_COMPANIES = [
    # Greenhouse
    {"name": "Stripe", "ats": "greenhouse", "token": "stripe"},
    {"name": "Figma", "ats": "greenhouse", "token": "figma"},
    {"name": "Notion", "ats": "greenhouse", "token": "notion"},
    {"name": "Airbnb", "ats": "greenhouse", "token": "airbnb"},
    {"name": "Dropbox", "ats": "greenhouse", "token": "dropbox"},
    # Lever
    {"name": "Netflix", "ats": "lever", "token": "netflix"},
    {"name": "Shopify", "ats": "lever", "token": "shopify"},
    {"name": "Airtable", "ats": "lever", "token": "airtable"},
    {"name": "Reddit", "ats": "lever", "token": "reddit"},
    {"name": "Twitch", "ats": "lever", "token": "twitch"},
    # Ashby
    {"name": "Linear", "ats": "ashby", "token": "linear"},
    {"name": "Vercel", "ats": "ashby", "token": "vercel"},
    {"name": "Retool", "ats": "ashby", "token": "retool"},
    {"name": "Clerk", "ats": "ashby", "token": "clerk"},
    {"name": "Supabase", "ats": "ashby", "token": "supabase"},
    # Workable
    {"name": "Vimeo", "ats": "workable", "token": "vimeo"},
    {"name": "Typeform", "ats": "workable", "token": "typeform"},
    {"name": "Personio", "ats": "workable", "token": "personio"},
    {"name": "Whereby", "ats": "workable", "token": "whereby"},
    {"name": "Loom", "ats": "workable", "token": "loom"},
    # SmartRecruiters
    {"name": "Bosch", "ats": "smartrecruiters", "token": "Bosch"},
    {"name": "Ikea", "ats": "smartrecruiters", "token": "Ingka"},
    {"name": "Sephora", "ats": "smartrecruiters", "token": "Sephora"},
    {"name": "McDonald's", "ats": "smartrecruiters", "token": "McDonalds"},
    {"name": "Aldi", "ats": "smartrecruiters", "token": "AldiUSA"},
    # Recruitee
    {"name": "Brex", "ats": "recruitee", "token": "brex"},
    {"name": "Invoice Ninja", "ats": "recruitee", "token": "invoiceninja"},
    {"name": "PostHog", "ats": "recruitee", "token": "posthog"},
    {"name": "Spendesk", "ats": "recruitee", "token": "spendesk"},
    {"name": "Factorial HR", "ats": "recruitee", "token": "factorial"},
]


def seed() -> None:
    """Create tables and insert seed companies (skip if already exists)."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        added = 0
        for entry in SEED_COMPANIES:
            exists = (
                db.query(Company)
                .filter(Company.ats == entry["ats"], Company.token == entry["token"])
                .first()
            )
            if not exists:
                db.add(Company(**entry))
                added += 1
        db.commit()
        logger.info("Seed complete. Added %d companies.", added)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed()
