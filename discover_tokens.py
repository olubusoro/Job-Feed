#!/usr/bin/env python3
"""
discover_tokens.py

Finds new Greenhouse, Lever, and Ashby job board tokens by searching GitHub,
validates them against the actual ATS APIs, and inserts them into the JobFeed DB.
"""

import argparse
import base64
import csv
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

# Import from our existing JobFeed app
from app.database import SessionLocal
from app.models import ATSType, Company

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("discover")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
MAX_WORKERS = 10
REQUEST_TIMEOUT = 10.0
MAX_RETRIES = 2

# ─────────────────────────────────────────────────────────────────────────────
# Regex Patterns for Extraction
# ─────────────────────────────────────────────────────────────────────────────

# Matches both boards.greenhouse.io/token and boards-api.greenhouse.io/v1/boards/token
GREENHOUSE_RE = re.compile(r"boards(?:-api)?\.greenhouse\.io(?:/v1/boards)?/([a-zA-Z0-9_-]+)")
LEVER_RE = re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)")
ASHBY_RE = re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)")


@dataclass
class CandidateToken:
    ats: str
    token: str
    source_url: str


@dataclass
class ValidatedToken:
    ats: str
    token: str
    name: str
    source_url: str
    needs_review: bool


# ─────────────────────────────────────────────────────────────────────────────
# GitHub Search Logic
# ─────────────────────────────────────────────────────────────────────────────


def search_github() -> List[CandidateToken]:
    """Search GitHub for files that contain ATS URLs, then extract tokens."""
    if not GITHUB_TOKEN:
        logger.warning("No GITHUB_TOKEN set. Searching GitHub will be heavily rate-limited.")
    
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    queries = [
        '"boards.greenhouse.io"',
        '"jobs.lever.co"',
        '"jobs.ashbyhq.com"',
        'filename:greenhouse token',
        'filename:lever companies'
    ]

    candidates: List[CandidateToken] = []
    seen_tokens: Set[Tuple[str, str]] = set()

    with httpx.Client(headers=headers, timeout=15.0) as client:
        for query in queries:
            logger.info(f"Searching GitHub for: {query}")
            page = 1
            while page <= 3:  # Limit to 3 pages per query to avoid burning rate limits instantly
                url = f"https://api.github.com/search/code?q={query}&per_page=30&page={page}"
                resp = client.get(url)

                if resp.status_code in (403, 429):
                    reset_time = int(resp.headers.get("x-ratelimit-reset", time.time() + 60))
                    sleep_duration = max(5, reset_time - int(time.time()) + 1)
                    logger.warning(f"Rate limited by GitHub. Sleeping for {sleep_duration}s...")
                    time.sleep(sleep_duration)
                    continue

                if resp.status_code != 200:
                    logger.error(f"GitHub search failed: {resp.status_code} - {resp.text}")
                    break

                data = resp.json()
                items = data.get("items", [])
                if not items:
                    break

                for item in items:
                    # e.g. https://github.com/user/repo/blob/abcdef/file.txt
                    html_url = item.get("html_url", "")
                    
                    # Convert to raw URL to download content
                    # Format: https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{path}
                    repo_full_name = item["repository"]["full_name"]
                    path = item["path"]
                    
                    # We can fetch via the API to get the content directly for small files
                    content_url = item.get("url")
                    if content_url:
                        try:
                            c_resp = client.get(content_url)
                            if c_resp.status_code == 200:
                                c_data = c_resp.json()
                                if c_data.get("encoding") == "base64":
                                    file_content = base64.b64decode(c_data["content"]).decode("utf-8", errors="ignore")
                                    _extract_from_text(file_content, html_url, candidates, seen_tokens)
                        except Exception as e:
                            logger.debug(f"Failed to fetch content from {content_url}: {e}")

                page += 1
                time.sleep(2)  # Polite delay between search pages

    return candidates


def _extract_from_text(text: str, source_url: str, candidates: List[CandidateToken], seen: Set[Tuple[str, str]]):
    """Run regexes over text and add to candidates if not seen."""
    for match in GREENHOUSE_RE.findall(text):
        token = match.lower().strip()
        if ("greenhouse", token) not in seen:
            seen.add(("greenhouse", token))
            candidates.append(CandidateToken("greenhouse", token, source_url))
            
    for match in LEVER_RE.findall(text):
        token = match.lower().strip()
        if ("lever", token) not in seen:
            seen.add(("lever", token))
            candidates.append(CandidateToken("lever", token, source_url))
            
    for match in ASHBY_RE.findall(text):
        token = match.lower().strip()
        if ("ashby", token) not in seen:
            seen.add(("ashby", token))
            candidates.append(CandidateToken("ashby", token, source_url))


# ─────────────────────────────────────────────────────────────────────────────
# Validation Logic
# ─────────────────────────────────────────────────────────────────────────────


def _fetch_with_retry(client: httpx.Client, url: str) -> Optional[httpx.Response]:
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code < 500:
                return resp
            logger.debug(f"Server error {resp.status_code} for {url} (attempt {attempt+1})")
        except httpx.RequestError as e:
            logger.debug(f"Request failed for {url} (attempt {attempt+1}): {e}")
            
        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)
    return None


def validate_token(candidate: CandidateToken) -> Optional[ValidatedToken]:
    """Hits the ATS API to see if the token is real and has jobs."""
    with httpx.Client(follow_redirects=True) as client:
        if candidate.ats == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{candidate.token}/jobs"
            resp = _fetch_with_retry(client, url)
            if resp and resp.status_code == 200:
                data = resp.json()
                jobs = data.get("jobs", [])
                if jobs:
                    # Greenhouse often includes the company name in the board endpoint
                    board_resp = _fetch_with_retry(client, f"https://boards-api.greenhouse.io/v1/boards/{candidate.token}")
                    name = candidate.token.title()
                    needs_review = True
                    if board_resp and board_resp.status_code == 200:
                        b_data = board_resp.json()
                        if b_data.get("name"):
                            name = b_data["name"]
                            needs_review = False
                    return ValidatedToken(candidate.ats, candidate.token, name, candidate.source_url, needs_review)
                    
        elif candidate.ats == "lever":
            # Lever returns a list of postings directly
            url = f"https://api.lever.co/v0/postings/{candidate.token}?mode=json"
            resp = _fetch_with_retry(client, url)
            if resp and resp.status_code == 200:
                jobs = resp.json()
                if isinstance(jobs, list) and len(jobs) > 0:
                    return ValidatedToken(candidate.ats, candidate.token, candidate.token.title(), candidate.source_url, True)
            else:
                # Try EU endpoint
                eu_url = f"https://api.eu.lever.co/v0/postings/{candidate.token}?mode=json"
                eu_resp = _fetch_with_retry(client, eu_url)
                if eu_resp and eu_resp.status_code == 200:
                    jobs = eu_resp.json()
                    if isinstance(jobs, list) and len(jobs) > 0:
                        return ValidatedToken(candidate.ats, candidate.token, candidate.token.title(), candidate.source_url, True)
                        
        elif candidate.ats == "ashby":
            url = f"https://api.ashbyhq.com/posting-api/job-board/{candidate.token}"
            resp = _fetch_with_retry(client, url)
            if resp and resp.status_code == 200:
                data = resp.json()
                jobs = data.get("jobs", [])
                if jobs:
                    return ValidatedToken(candidate.ats, candidate.token, candidate.token.title(), candidate.source_url, True)

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main Execution
# ─────────────────────────────────────────────────────────────────────────────

def run_discovery(dry_run: bool):
    logger.info("Starting ATS token discovery run...")
    
    # 1. Search GitHub
    candidates = search_github()
    logger.info(f"Found {len(candidates)} raw candidate tokens from GitHub.")
    
    db = SessionLocal()
    
    # 2. Deduplicate against known-active tokens in DB
    existing_companies = db.query(Company).all()
    active_tokens = {(c.ats.lower(), c.token.lower()) for c in existing_companies if c.active}
    all_known_tokens = {(c.ats.lower(), c.token.lower()) for c in existing_companies}
    
    to_validate: List[CandidateToken] = []
    skipped_count = 0
    
    for c in candidates:
        if (c.ats, c.token) in active_tokens:
            skipped_count += 1
        else:
            to_validate.append(c)
            
    logger.info(f"Skipped {skipped_count} currently active tokens. Validating {len(to_validate)} candidates...")
    
    # 3. Validate concurrently
    valid_tokens: List[ValidatedToken] = []
    invalid_count = 0
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_cand = {executor.submit(validate_token, c): c for c in to_validate}
        for i, future in enumerate(as_completed(future_to_cand), 1):
            cand = future_to_cand[future]
            try:
                result = future.result()
                if result:
                    valid_tokens.append(result)
                    logger.debug(f"[VALID] {result.ats} / {result.token}")
                else:
                    invalid_count += 1
            except Exception as exc:
                logger.error(f"Error validating {cand.ats}/{cand.token}: {exc}")
                invalid_count += 1
                
            if i % 50 == 0:
                logger.info(f"Validated {i}/{len(to_validate)}...")
                
    logger.info(f"Validation complete. Found {len(valid_tokens)} valid tokens, {invalid_count} invalid/empty.")
    
    # 4. Save to DB and CSVs
    added_count = 0
    reactivated_count = 0
    
    log_rows = []
    review_rows = []
    
    for vt in valid_tokens:
        is_reactivation = False
        if not dry_run:
            existing = db.query(Company).filter(
                Company.ats == vt.ats, 
                Company.token == vt.token
            ).first()
            
            if existing:
                # Was inactive, but now it's valid again
                existing.active = True
                existing.consecutive_failures = 0
                is_reactivation = True
                reactivated_count += 1
            else:
                db.add(Company(
                    name=vt.name,
                    ats=vt.ats,
                    token=vt.token,
                    active=True
                ))
                added_count += 1
                
        status = "reactivated" if is_reactivation else "added"
        log_rows.append((vt.token, vt.ats, status, vt.source_url))
        
        if vt.needs_review:
            review_rows.append((vt.name, vt.ats, vt.token))
            
    # Add skipped to log rows too
    for c in candidates:
        if (c.ats, c.token) in active_tokens:
            log_rows.append((c.token, c.ats, "duplicate_active", c.source_url))

    if not dry_run:
        try:
            db.commit()
            logger.info("Successfully committed new companies to the database.")
        except Exception as e:
            logger.error(f"Database commit failed: {e}")
            db.rollback()
    else:
        logger.info("[DRY RUN] Would have committed DB changes.")
        
    db.close()
    
    # Write Logs
    with open("discovery_run_log.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Token", "ATS", "Status", "Source"])
        writer.writerows(log_rows)
        
    if review_rows:
        with open("review_queue.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Assumed Name", "ATS", "Token"])
            writer.writerows(review_rows)
            
    logger.info("--- Discovery Run Summary ---")
    logger.info(f"Candidates Found: {len(candidates)}")
    logger.info(f"Duplicates Skipped: {skipped_count}")
    logger.info(f"Invalid/Empty Discarded: {invalid_count}")
    logger.info(f"Valid Tokens Reactivated: {reactivated_count}")
    logger.info(f"New Valid Tokens Added: {added_count}")
    logger.info("Output written to discovery_run_log.csv")
    if review_rows:
        logger.info(f"{len(review_rows)} tokens require name review in review_queue.csv")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover new ATS tokens via GitHub search.")
    parser.add_argument("--dry-run", action="store_true", help="Find and validate but do not save to DB.")
    args = parser.parse_args()
    
    run_discovery(args.dry_run)
