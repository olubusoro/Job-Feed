# JobFeed 🚀

A self-hosted job aggregator that pulls fresh postings from company ATS
public APIs, matches them against your custom search profiles, and delivers a
ranked daily list — viewable in the browser and downloadable as Excel.

---

## Features

| Feature | Detail |
|---|---|
| **ATS Adapters** | Greenhouse, Lever (+ EU fallback), Ashby, Workable, SmartRecruiters, Recruitee |
| **Smart Matching** | Seniority rules, keyword filters, location modes, sponsorship/clearance detection |
| **Scoring** | Title match, must-have hits, nice-to-have hits, recency |
| **Multi-user** | Invite-code signup; each user has independent profiles |
| **Excel export** | .xlsx with clickable Apply links, frozen header, auto-filter |
| **Scheduler** | APScheduler fetches every 3 h; manual "Refresh Now" button |
| **Admin panel** | Import companies via JSON/CSV, toggle active/inactive, fetch run log |
| **Deploy-ready** | Dockerfile + docker-compose + Render/Railway instructions |

---

## Quick Start (Local)

### Prerequisites

- Python 3.11+
- Docker & Docker Compose (optional but recommended)

### With Docker Compose

```bash
git clone <repo> jobfeed && cd jobfeed

# 1. Create your .env
cp .env.example .env
# Edit .env — set SECRET_KEY, INVITE_CODE, ADMIN_EMAIL at minimum

# 2. Start
docker compose up --build

# 3. Open http://localhost:8000
#    Register with your invite code → dashboard appears
```

### Without Docker

```bash
cd jobfeed

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env

uvicorn app.main:app --reload --port 8000
```

App starts at **http://localhost:8000**.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `change-me-...` | Session signing key — **change this!** |
| `INVITE_CODE` | `friends-only` | Required at signup |
| `ADMIN_EMAIL` | `` | This user gets the Admin panel automatically |
| `DATABASE_URL` | `sqlite:///./jobfeed.db` | Switch to `postgresql+psycopg2://...` for Postgres |
| `FETCH_INTERVAL_HOURS` | `3` | How often APScheduler fetches jobs |
| `HTTP_TIMEOUT` | `10` | Per-request timeout (seconds) |
| `HTTP_MAX_WORKERS` | `15` | ThreadPoolExecutor workers for concurrent fetching |
| `HTTP_MAX_RETRIES` | `3` | Retry attempts with exponential backoff |
| `DEBUG` | `false` | Enables SQLAlchemy echo and verbose logging |

---

## Project Structure

```
jobfeed/
├── app/
│   ├── main.py              # FastAPI app, all routes
│   ├── config.py            # Pydantic settings (env vars)
│   ├── database.py          # SQLAlchemy engine + session
│   ├── models.py            # ORM models
│   ├── auth.py              # bcrypt + session cookies
│   ├── scheduler.py         # APScheduler (3-hour interval)
│   ├── seed.py              # DB init + ~5 companies per ATS
│   ├── adapters/
│   │   ├── base.py          # BaseAdapter + HTTP retry helper
│   │   ├── greenhouse.py
│   │   ├── lever.py         # EU endpoint fallback
│   │   ├── ashby.py
│   │   ├── workable.py      # POST-based API
│   │   ├── smartrecruiters.py  # Paginated
│   │   └── recruitee.py
│   ├── services/
│   │   ├── fetcher.py       # Concurrent orchestrator + upsert
│   │   ├── matcher.py       # Seniority / keywords / location / scoring
│   │   └── excel.py         # openpyxl export
│   └── templates/           # Jinja2 + HTMX
│       ├── base.html
│       ├── login.html
│       ├── register.html
│       ├── dashboard.html
│       ├── profiles.html
│       ├── profile_edit.html
│       └── admin.html
├── tests/
│   ├── test_matcher.py      # 20+ tests: seniority, freshness, location, scoring
│   └── test_adapters.py     # Datetime parsing tests
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## Adding a Company

### Via Admin Panel (browser)

1. Sign in as admin → **Admin** in the nav.
2. Paste JSON or CSV in the import box.

**JSON:**
```json
[
  {"name": "Acme Corp", "ats": "greenhouse", "token": "acme"},
  {"name": "Widget Inc", "ats": "lever", "token": "widgetinc"}
]
```

**CSV:**
```
name,ats,token
Acme Corp,greenhouse,acme
Widget Inc,lever,widgetinc
```

### Via seed file

Edit `app/seed.py` and add entries to `SEED_COMPANIES`. Re-run:

```bash
python -m app.seed
```

---

## Adding a New ATS Adapter

1. Create `app/adapters/your_ats.py` subclassing `BaseAdapter`.
2. Implement `fetch() -> list[NormalizedJob]`.
3. Register it in `app/services/fetcher.py`:

```python
from app.adapters.your_ats import YourATSAdapter

ADAPTER_REGISTRY["your_ats"] = YourATSAdapter
```

4. Add companies with `"ats": "your_ats"`.

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Deploy to Render

1. Push to GitHub.
2. Create a new **Web Service** on [Render](https://render.com).
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Add environment variables in the Render dashboard (copy from `.env.example`).
4. For persistence, add a **Render Disk** mounted at `/app/data` and set:
   `DATABASE_URL=sqlite:////app/data/jobfeed.db`
5. Or connect a **Render Postgres** database and set the `DATABASE_URL` accordingly.
   > Install `psycopg2-binary` — add to `requirements.txt`.

---

## Deploy to Railway

1. Push to GitHub.
2. Create a new project → **Deploy from GitHub repo**.
3. Railway auto-detects the `Dockerfile`.
4. Add a **PostgreSQL** plugin → Railway sets `DATABASE_URL` automatically.
   > Make sure `psycopg2-binary` is in `requirements.txt`.
5. Set other env vars: `SECRET_KEY`, `INVITE_CODE`, `ADMIN_EMAIL`.
6. Deploy — Railway uses port `$PORT` automatically.

> **Tip:** For Railway, override the Dockerfile `CMD` with:
> `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

---

## Using Postgres Instead of SQLite

```bash
pip install psycopg2-binary
```

Set in `.env`:
```
DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/jobfeed
```

All schema is created automatically on startup via `Base.metadata.create_all()`.

---

## Seniority Rules (Quick Reference)

| Signal | Action |
|---|---|
| intern, junior, entry-level, new grad, apprentice | ❌ Always excluded |
| staff, principal, lead, director, VP, manager, head-of | ❌ Always excluded |
| senior / sr | ✅ Included if "include_senior" checked |
| mid-level / II | ✅ Included if "include_mid" checked |
| No signal | ✅ Treated as mid-level |

---

## License

MIT
