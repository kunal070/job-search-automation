from fastapi import FastAPI, Query
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from typing import List, Dict, Any, Tuple, Optional
import re
from collections import deque
import hashlib
import json
import os
import smtplib
import threading
import time

import requests
import schedule
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# Load environment variables from .env file
load_dotenv()

app = FastAPI()

# New imports and shared instances for unified /jobs endpoint
from api.settings import settings
from api.adapters.utils import SimpleCache, RateLimiter
from api.adapters.base import JobItem
from api.adapters.jsearch import JSearchAdapter
from api.adapters.jooble import JoobleAdapter
from api.adapters.adzuna import AdzunaAdapter

# Per-source rate limits (fallbacks); overridable via RATE_LIMITS_JSON
_limits = settings.rate_limits or {}
_jsearch_limits = _limits.get("jsearch", {"per_min": 25, "per_day": 80})
_jooble_limits = _limits.get("jooble", {"per_min": 30, "per_day": 500})
_adzuna_limits = _limits.get("adzuna", {"per_min": 25, "per_day": 250})

_shared_cache = SimpleCache(ttl_seconds=settings.cache_ttl_seconds)
_jsearch = JSearchAdapter(_shared_cache, RateLimiter(_jsearch_limits["per_min"], _jsearch_limits["per_day"]))
_jooble = JoobleAdapter(_shared_cache, RateLimiter(_jooble_limits["per_min"], _jooble_limits["per_day"]))
_adzuna = AdzunaAdapter(_shared_cache, RateLimiter(_adzuna_limits["per_min"], _adzuna_limits["per_day"]))


# ---- Scheduler flag (true by default for local) ----
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "true").lower() == "true"


# ------------------------ Configuration ------------------------

# Adzuna API configuration (replacement for JSearch)
ADZUNA_APP_ID = os.getenv("APP_ID") or os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("APP_KEY") or os.getenv("ADZUNA_APP_KEY", "")
ADZUNA_COUNTRY = os.getenv("ADZUNA_COUNTRY", "ca").lower()
ADZUNA_BASE_URL = f"https://api.adzuna.com/v1/api/jobs/{ADZUNA_COUNTRY}/search/{{page}}"
DEFAULT_RESULTS_PER_PAGE = int(os.getenv("ADZUNA_RESULTS_PER_PAGE", "20"))
DEFAULT_WHERE = os.getenv("ADZUNA_WHERE", "Canada")

# Basic local rate limiting (guard before calling Adzuna)
RATE_MAX_PER_MINUTE = int(os.getenv("ADZUNA_RATE_PER_MIN", "25"))
RATE_MAX_PER_DAY = int(os.getenv("ADZUNA_RATE_PER_DAY", "250"))

# Simple in-memory cache TTL (seconds)
CACHE_TTL_SECONDS = int(os.getenv("ADZUNA_CACHE_TTL", str(60 * 60)))  # 1 hour
ADZUNA_MAX_DAYS_OLD = int(os.getenv("ADZUNA_MAX_DAYS_OLD", "7"))  # API-side freshness filter
JOB_MAX_AGE_DAYS = int(os.getenv("JOB_MAX_AGE_DAYS", "10"))       # Local freshness guard

# Persistence
JOBS_FILE = "jobs_seen.json"

# Email configuration
SMTP_SERVER = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "20"))
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "your-email@gmail.com")
SENDER_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "your-app-password")  # App Password for Gmail

# Multiple Recipients - Read from RECIPIENT_EMAILS and split by comma
RECIPIENT_EMAILS_STRING = os.getenv("RECIPIENT_EMAILS", "recipient@gmail.com")
RECIPIENT_EMAILS = [email.strip() for email in RECIPIENT_EMAILS_STRING.split(",") if email.strip()]

print(f"Email will be sent to: {RECIPIENT_EMAILS}")  # Debug line to verify emails


# ------------------------ Keywords & Filters ------------------------

# Keywords for filtering jobs
ROLE_KEYWORDS = [
    # AI/ML Keywords
    "ai engineer", "artificial intelligence engineer", "machine learning engineer", "ml engineer",
    "deep learning engineer", "neural network engineer", "computer vision engineer",
    "natural language processing", "nlp engineer", "ai specialist", "ai developer",
    "mlops engineer", "machine learning scientist", "ai research",

    # Data Science Keywords
    "data scientist", "data science", "senior data scientist", "junior data scientist",
    "data analyst", "data analytics", "business analyst", "quantitative analyst",
    "research analyst", "business intelligence analyst", "bi analyst",

    # Data Engineering Keywords
    "data engineer", "data engineering", "big data engineer", "etl developer",
    "data pipeline engineer", "analytics engineer", "database analyst",
    "sql analyst", "data warehouse", "data modeling",

    # LLM and AI Agent Keywords
    "llm engineer", "large language model", "generative ai", "chatbot developer",
    "ai agent", "conversational ai", "prompt engineer", "ai product",
    "llm researcher", "generative ai engineer",

    # Specialized AI Roles
    "computer vision", "image processing", "speech recognition", "recommendation systems",
    "reinforcement learning", "ai consultant", "machine learning consultant",
    "data mining", "predictive analytics", "statistical analysis",
]

# More strict experience filtering for 0-1.5 years
EXPERIENCE_KEYWORDS = [
    "entry level", "junior", "associate", "fresh graduate", "new grad", "graduate",
    "0-1 years", "0-2 years", "1-2 years", "0-1.5 years", "1-1.5 years",
    "no experience required", "recent graduate", "graduate program", "trainee",
    "beginner", "starting career", "new to field",
    "career starter", "entry-level", "junior level", "0 years", "1 year",
    "up to 1.5", "less than 2", "under 2 years", "18 months", "one year",
]

# Updated senior keywords to exclude (stricter for 1.5+ years)
SENIOR_KEYWORDS = [
    # English seniority
    "senior", "sr.", "sr ", "lead", "principal", "staff", "architect", "manager", "director",
    "head of", "chief", "consultant", "specialist", "intermediate", "mid level", "mid-level",
    # French seniority
    "intermédiaire", "expérimenté", "confirmé", "chef d’équipe", "chef d'equipe", "responsable",
    # Ranges and years
    "2+ years", "3+ years", "4+ years", "5+ years", "6+ years", "7+ years", "8+ years",
    "minimum 2 years", "minimum 3 years", "minimum 4 years", "minimum 5 years",
    "2-3 years", "3-5 years", "5-7 years", "experienced professional", "expert level",
    "principal engineer",
]

# Keywords indicating ineligibility due to visa/citizenship requirements
INELIGIBLE_KEYWORDS = [
    "permanent resident", "pr required", "citizenship required",
    "security clearance", "must be citizen", "canadian citizen only",
    "us citizen only", "citizen required", "must be canadian citizen",
    "canadian pr required", "permanent residency required", "clearance required",
    "government clearance", "background clearance", "must have pr", "pr status required",
]

# Explicitly exclude co-op and roles requiring active university enrollment
EXCLUDE_ENROLLMENT_KEYWORDS = [
    "co-op", "co op", "cooperative education", "co-operative",
    "work-study", "work study", "coop program", "co-op term",
    "currently enrolled", "must be enrolled", "enrolled in", "active student",
    "current student", "full-time student", "part-time student",
    "must be a student", "returning to school", "co-op work permit", "coop work permit",
    # Intern keywords (generic)
    "internship",  # generic internship mention
    # French student/intern terms
    "stagiaire", "stage", "étudiant", "etudiant", "étudiante", "etudiante",
    "inscrit", "inscrite", "inscription", "université", "universite", "collège", "college",
]


# ------------------------ Persistence helpers ------------------------

def load_seen_jobs() -> Dict[str, Any]:
    if not os.path.exists(JOBS_FILE):
        return {"last_updated": "", "seen_jobs": {}}
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_updated": "", "seen_jobs": {}}


def save_seen_jobs(data: Dict[str, Any]) -> None:
    try:
        with open(JOBS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving jobs file: {e}")


def create_job_hash(title: str, company: str, location: str) -> str:
    combined = f"{title.lower()}-{company.lower()}-{location.lower()}"
    return hashlib.md5(combined.encode()).hexdigest()


def cleanup_old_jobs(seen_jobs: Dict[str, Any], days_threshold: int = 30) -> Dict[str, Any]:
    cutoff_date = datetime.now() - timedelta(days=days_threshold)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")
    jobs_to_remove = [
        job_hash
        for job_hash, job_data in seen_jobs.get("seen_jobs", {}).items()
        if job_data.get("first_seen", "0000-00-00") < cutoff_str
    ]
    for job_hash in jobs_to_remove:
        del seen_jobs["seen_jobs"][job_hash]
    return seen_jobs


# ------------------------ Rate limiting & cache ------------------------

_minute_window: deque[float] = deque()
_day_window: deque[float] = deque()
_cache: Dict[str, Dict[str, Any]] = {}


def _rate_allow() -> bool:
    now = time.time()
    while _minute_window and now - _minute_window[0] >= 60:
        _minute_window.popleft()
    while _day_window and now - _day_window[0] >= 24 * 3600:
        _day_window.popleft()
    return len(_minute_window) < RATE_MAX_PER_MINUTE and len(_day_window) < RATE_MAX_PER_DAY


def _rate_record() -> None:
    ts = time.time()
    _minute_window.append(ts)
    _day_window.append(ts)


def _cache_key(what: str, where: str, page_num: int, results_per_page: int) -> str:
    return f"{what.lower()}|{where.lower()}|{page_num}|{results_per_page}"


def _cache_get(key: str) -> Optional[List[Dict[str, Any]]]:
    item = _cache.get(key)
    if not item:
        return None
    if time.time() - item["ts"] > CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return item["data"]


def _cache_set(key: str, data: List[Dict[str, Any]]) -> None:
    _cache[key] = {"ts": time.time(), "data": data}


def is_fresh_job(posted_at: str, max_age_days: int) -> bool:
    """Return True if posted_at (ISO string) is within max_age_days from now. Empty value is treated as fresh=False."""
    if not posted_at:
        return False
    try:
        # Adzuna 'created' is ISO, e.g., '2025-09-12T05:34:21Z' or '2025-09-12 05:34:21'
        ts = posted_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
    except Exception:
        return False
    age = datetime.now(timezone.utc) - (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
    return age.days <= max_age_days


# ------------------------ Search helpers (Adzuna) ------------------------

def _adzuna_request(what: str, where: str, page_num: int, results_per_page: int) -> List[Dict[str, Any]]:
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        print("Adzuna credentials missing. Set APP_ID and APP_KEY in environment.")
        return []

    if not _rate_allow():
        print("Adzuna rate limit reached (local guard). Skipping request.")
        return []

    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "what": what,
        "where": where,
        "results_per_page": str(results_per_page),
        "max_days_old": str(ADZUNA_MAX_DAYS_OLD),
        "sort_by": "date",
        "content-type": "application/json",
    }

    url = ADZUNA_BASE_URL.format(page=page_num)

    backoff = 1.0
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, params=params, timeout=20)
            status = resp.status_code
            if status == 429 or 500 <= status < 600:
                if attempt == max_attempts:
                    print(f"Adzuna request failed after retries: HTTP {status}")
                    return []
                retry_after = resp.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after and str(retry_after).isdigit() else backoff
                print(f"Adzuna HTTP {status}. Backing off {sleep_for:.1f}s (attempt {attempt}).")
                time.sleep(sleep_for)
                backoff *= 2
                continue

            resp.raise_for_status()
            data = resp.json() or {}
            results = data.get("results", [])
            _rate_record()
            return results
        except requests.RequestException as e:
            if attempt == max_attempts:
                print(f"Adzuna network error after retries: {e}")
                return []
            print(f"Adzuna network error: {e}. Backoff {backoff:.1f}s (attempt {attempt}).")
            time.sleep(backoff)
            backoff *= 2
        except json.JSONDecodeError:
            print("Adzuna JSON decode error.")
            return []

    return []


def search_jobs_by_query(query: str, page_num: int = 1, where: Optional[str] = None,
                         results_per_page: Optional[int] = None) -> List[Dict[str, Any]]:
    """Search for jobs using Adzuna API with specific query and page number.

    Returns a list of dicts mapped to both legacy and normalized fields.
    """
    where = where or DEFAULT_WHERE
    rpp = results_per_page or DEFAULT_RESULTS_PER_PAGE

    key = _cache_key(query, where, page_num, rpp)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    raw = _adzuna_request(query, where, page_num, rpp)
    mapped: List[Dict[str, Any]] = []
    for it in raw:
        title = it.get("title") or ""
        company = (it.get("company") or {}).get("display_name") or ""
        area = (it.get("location") or {}).get("area") or []
        city = area[-1] if area else ""
        country = ADZUNA_COUNTRY.upper()
        desc = it.get("description") or ""
        url = it.get("redirect_url") or ""
        created = it.get("created") or ""

        mapped.append({
            # Normalized
            "title": title,
            "company": company,
            "location": ", ".join([p for p in [city, country] if p]).strip(", "),
            "description": desc,
            "url": url,
            "posted_at": created,
            # Legacy keys used elsewhere in pipeline
            "job_title": title,
            "employer_name": company,
            "job_city": city,
            "job_country": country,
            "job_description": desc,
            "job_apply_link": url,
            "job_posted_at_datetime_utc": created,
        })

    _cache_set(key, mapped)
    return mapped


def search_all_jobs() -> List[Dict[str, Any]]:
    """AI/ML focused job search with strategic queries (2 pages each) via Adzuna."""
    all_jobs: List[Dict[str, Any]] = []

    ai_ml_queries = [
        # Entry-level AI/ML roles
        "junior machine learning engineer",
        "entry level data scientist",
        "graduate ai engineer",
        "junior data analyst",
        # AI/ML specific roles
        "machine learning engineer new grad",
        "data science internship",
        "nlp engineer entry level",
        "computer vision engineer junior",
        # Data and LLM focused roles
        "data engineer fresh graduate",
        "llm engineer entry level",
    ]

    for query in ai_ml_queries:
        print(f"Searching: {query} in {DEFAULT_WHERE}")
        for page in range(1, 3):  # Pages 1-2
            jobs = search_jobs_by_query(query, page, where=DEFAULT_WHERE, results_per_page=DEFAULT_RESULTS_PER_PAGE)
            if jobs:
                all_jobs.extend(jobs)
                print(f"  Page {page}: {len(jobs)} jobs")
            else:
                break
            time.sleep(0.3)  # gentle pacing per page
        time.sleep(0.8)  # pacing per query

    print(f"Total AI/ML jobs collected: {len(all_jobs)}")
    return all_jobs


# ------------------------ Filtering ------------------------

def is_eligible_job(job: Dict[str, Any]) -> Tuple[bool, str]:
    """Strict filter for AI/ML jobs requiring 0-1.5 years experience maximum."""
    title = (job.get("job_title") or job.get("title") or "").lower()
    description = (job.get("job_description") or job.get("description") or "").lower()
    combined_text = f"{title} {description}"

    # 1) Visa/citizenship restrictions (STRICT)
    for keyword in INELIGIBLE_KEYWORDS:
        if keyword in combined_text:
            return False, f"Requires {keyword}"

    # 1b) Exclude co-op and student-enrollment requirements
    for keyword in EXCLUDE_ENROLLMENT_KEYWORDS:
        if keyword in combined_text:
            return False, "Co-op or student enrollment required"

    # 1c) Exclude intern/internship roles (word-boundary to avoid 'internal')
    if re.search(r"\b(intern|internship|summer intern|fall intern|winter intern|spring intern)\b", combined_text):
        return False, "Intern role (often requires university enrollment)"

    # 2) Senior position check (exclude ANY 2+ years indicators)
    for keyword in SENIOR_KEYWORDS:
        if keyword in combined_text:
            return False, f"Too much experience required: {keyword}"

    # 3) Reject explicit multi-year experience (EN/FR) >= 2 years
    # English patterns: e.g., "2 years", "3+ yrs", "at least 2 years"
    en_years = re.search(r"\b(2|3|4|5|6|7|8|9|10)\s*\+?\s*(years?|yrs)\b", combined_text)
    # French patterns: e.g., "2 ans d'expérience"
    fr_years = re.search(r"\b(2|3|4|5|6|7|8|9|10)\s*(ans)\b.*expérien|experience", combined_text)
    if en_years or fr_years:
        return False, "Requires >= 2 years experience"

    # II/III/IV levels in title often indicate non-junior
    if re.search(r"\b(ii|iii|iv)\b", title):
        return False, "Non-junior level indicated (II/III/IV)"

    # 4) Relevant AI/ML/Data role keywords (STRICT)
    role_matches = [kw for kw in ROLE_KEYWORDS if kw in combined_text]
    if not role_matches:
        ai_ml_terms = [
            "artificial intelligence", "machine learning", "data science", "data analyst",
            "deep learning", "neural network", "computer vision", "natural language",
            "nlp", "data mining", "predictive analytics", "statistical analysis",
            "big data", "data engineer", "business intelligence", "analytics",
            "llm", "generative ai", "chatbot", "recommendation system", "ai agent",
            "prompt engineering", "mlops", "data pipeline", "etl", "sql",
        ]
        if any(term in combined_text for term in ai_ml_terms):
            role_matches = ["ai/ml/data-related"]
        else:
            return False, "No relevant AI/ML/Data role keywords found"

    # 5) Explicit entry-level indicators (strong accept)
    entry_level_matches = [kw for kw in EXPERIENCE_KEYWORDS if kw in combined_text]
    if entry_level_matches:
        return True, f"{role_matches[0]} - {entry_level_matches[0]}"

    # 6) Acceptable explicit ranges (0-1.5 yrs)
    acceptable_experience = [
        "0-1 years", "0-1.5 years", "1-1.5 years", "0 to 1", "0 to 1.5",
        "up to 1", "up to 1.5", "less than 2", "under 2 years", "1 year",
        "one year", "18 months", "0-18 months", "1.5 years max", "maximum 1.5",
    ]
    if any(exp in combined_text for exp in acceptable_experience):
        return True, f"{role_matches[0]} (acceptable experience: 0-1.5 years)"

    # 7) Potentially problematic experience mentions
    problematic_experience = [
        "2 years", "3 years", "4 years", "5 years", "years of experience",
        "years experience", "minimum years", "must have experience",
        "required experience", "proven experience", "extensive experience",
        "solid experience", "strong experience", "professional experience",
    ]
    if any(exp in combined_text for exp in problematic_experience):
        flexible_terms = [
            "preferred but not required", "nice to have", "bonus", "plus",
            "would be great", "ideal but not required", "strongly preferred but not required",
            "some experience helpful", "any experience welcome", "little experience ok",
            "entry level welcome", "new graduates welcome", "fresh graduates welcome",
        ]
        if any(flex in combined_text for flex in flexible_terms):
            return True, f"{role_matches[0]} (flexible experience - entry level welcome)"
        return False, "Experience requirements unclear - likely requires >1.5 years"

    # 8) Training/graduate program indicators
    entry_indicators = [
        "training provided", "will train", "learn on job", "mentorship",
        "graduate program", "rotational program", "development program",
        "internship program", "apprenticeship", "on the job training",
    ]
    if any(ind in combined_text for ind in entry_indicators):
        return True, f"{role_matches[0]} (training provided - good for new grads)"

    # 9) Conservative default: if not explicitly entry-level, reject as ambiguous
    return False, "Ambiguous experience requirements (not clearly entry-level)"


# ------------------------ Email ------------------------

def send_email(new_jobs: List[Dict[str, Any]], run_info: Dict[str, Any]) -> None:
    if not new_jobs:
        return

    try:
        msg = MIMEMultipart()
        msg["From"] = SENDER_EMAIL
        msg["To"] = ", ".join(RECIPIENT_EMAILS)
        msg["Subject"] = f"{len(new_jobs)} New Job Matches Found! - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        html_body = f"""
        <html>
        <body>
            <h2>New Job Opportunities Found!</h2>
            <p><strong>Scan Time:</strong> {datetime.now().strftime('%Y-%m-%d at %H:%M')}</p>
            <p><strong>New Matches:</strong> {len(new_jobs)}</p>
            <p><strong>Total Jobs Scanned:</strong> {run_info.get('total_jobs_scanned', 'Unknown')}</p>
            <h3>Job Matches:</h3>
        """

        for i, job in enumerate(new_jobs, 1):
            html_body += f"""
            <div style="border:1px solid #ddd; padding:15px; margin:10px 0; border-radius:5px;">
                <h4 style="color:#2c3e50; margin:0 0 10px 0;">{i}. {job['title']}</h4>
                <p><strong>Company:</strong> {job['company']}</p>
                <p><strong>Location:</strong> {job['location']}</p>
                <p><strong>Why Matched:</strong> <em>{job['why_matched']}</em></p>
                <p><strong>Apply:</strong> <a href="{job['url']}" target="_blank">View Job Posting</a></p>
            </div>
            """

        html_body += f"""
            <p style="color:#7f8c8d; font-size:12px; margin-top:30px;">
                This automated job alert was sent to {len(RECIPIENT_EMAILS)} recipient(s).<br>
                Jobs are filtered for AI/Tech roles suitable for fresh graduates in Canada.
            </p>
        </body>
        </html>
        """

        msg.attach(MIMEText(html_body, "html"))

        def _send_via_tls(port: int) -> None:
            with smtplib.SMTP(SMTP_SERVER, port, timeout=SMTP_TIMEOUT) as server:
                if SMTP_USE_TLS:
                    server.starttls()
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, RECIPIENT_EMAILS, msg.as_string())

        def _send_via_ssl(port: int) -> None:
            with smtplib.SMTP_SSL(SMTP_SERVER, port, timeout=SMTP_TIMEOUT) as server:
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, RECIPIENT_EMAILS, msg.as_string())

        local_port = SMTP_PORT
        try:
            if SMTP_USE_SSL or local_port == 465:
                _send_via_ssl(local_port)
            else:
                _send_via_tls(local_port)
        except Exception as e1:
            # Fallback for Gmail: try SSL:465 if TLS:587 failed
            if ("gmail.com" in SMTP_SERVER) and not SMTP_USE_SSL and local_port == 587:
                _send_via_ssl(465)
            else:
                raise e1

        print(f"Email sent to {len(RECIPIENT_EMAILS)} recipients with {len(new_jobs)} matches")
        print(f"Recipients: {', '.join(RECIPIENT_EMAILS)}")

    except Exception as e:
        print(f"Error sending email: {e}")


# ------------------------ Scan & schedule ------------------------

def scan_jobs_automated() -> None:
    print(f"\nStarting automated job scan at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        # Load seen jobs and cleanup old ones
        seen_jobs_data = load_seen_jobs()
        seen_jobs_data = cleanup_old_jobs(seen_jobs_data)

        # Search for new jobs
        raw_jobs = search_all_jobs()
        if not raw_jobs:
            print("No jobs found from API")
            return

        new_jobs: List[Dict[str, Any]] = []
        today = datetime.now().strftime("%Y-%m-%d")

        for job in raw_jobs:
            title = job.get("job_title") or job.get("title") or "Unknown Title"
            company = job.get("employer_name") or job.get("company") or "Unknown Company"
            city = job.get("job_city") or ""
            country = job.get("job_country") or ""
            location = f"{city}, {country}".strip(", ") if city or country else job.get("location") or ""
            url = job.get("job_apply_link") or job.get("url") or ""
            posted_at = job.get("job_posted_at_datetime_utc") or job.get("posted_at") or ""

            # Freshness guard
            if not is_fresh_job(posted_at, JOB_MAX_AGE_DAYS):
                continue

            job_hash = create_job_hash(title, company, location)
            if job_hash in seen_jobs_data["seen_jobs"]:
                # Update last_seen
                seen_jobs_data["seen_jobs"][job_hash]["last_seen"] = today
                continue

            is_ok, reason = is_eligible_job(job)
            if is_ok:
                job_info = {
                    "title": title,
                    "company": company,
                    "location": location if location else "Remote/Unknown",
                    "url": url,
                    "why_matched": reason,
                }
                new_jobs.append(job_info)

                seen_jobs_data["seen_jobs"][job_hash] = {
                    "title": title,
                    "company": company,
                    "location": job_info["location"],
                    "first_seen": today,
                    "last_seen": today,
                    "url": url,
                }

        # Update last scan time & persist
        seen_jobs_data["last_updated"] = datetime.now().isoformat()
        save_seen_jobs(seen_jobs_data)

        run_info = {
            "total_jobs_scanned": len(raw_jobs),
            "new_matches": len(new_jobs),
            "total_in_memory": len(seen_jobs_data["seen_jobs"]),
        }

        print(f"Scan complete: {len(new_jobs)} new matches from {len(raw_jobs)} jobs scanned")

        if new_jobs:
            send_email(new_jobs, run_info)
        else:
            print("No new eligible jobs found")

    except Exception as e:
        print(f"Error in automated scan: {e}")


def schedule_jobs() -> None:
    schedule.every().day.at("08:00").do(scan_jobs_automated)
    schedule.every().day.at("12:37").do(scan_jobs_automated)
    schedule.every().day.at("17:00").do(scan_jobs_automated)
    schedule.every().day.at("23:00").do(scan_jobs_automated)

    print("Job scheduler initialized - 8:00, 12:37, 17:00, 23:00 daily")

    while True:
        schedule.run_pending()
        time.sleep(60)


def start_scheduler() -> None:
    scheduler_thread = threading.Thread(target=schedule_jobs, daemon=True)
    scheduler_thread.start()


# ------------------------ FastAPI lifecycle & routes ------------------------

@app.on_event("startup")
async def startup_event():
    if ENABLE_SCHEDULER:
        start_scheduler()
        print("Job scanner started with automatic scheduling")
    else:
        print("In-process scheduler disabled (ENABLE_SCHEDULER=false)")


@app.get("/")
def root():
    return {"ok": True, "message": "Fresh Graduate Job Scanner API", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/health")
def health():
    return {"status": "up", "ts": datetime.now(timezone.utc).isoformat()}


# ------------------------ Unified jobs endpoint ------------------------

def _normalize_where(where: Optional[str]) -> str:
    if where and where.strip():
        return where.strip()
    return "Toronto, ON, Canada"


def _dedup(items: List[JobItem]) -> List[JobItem]:
    seen: Dict[str, JobItem] = {}
    for it in items:
        key = f"{(it.title or '').strip().lower()}|{(it.company or '').strip().lower()}|{(it.location or '').strip().lower()}"
        if key not in seen:
            seen[key] = it
    return list(seen.values())


def _score(item: JobItem, now: datetime) -> float:
    text = f"{item.title.lower()} {item.description.lower()}"
    score = 0.0
    # Strong positives
    for kw, pts in [("junior", 5.0), ("entry level", 5.0), ("new grad", 4.0), ("graduate", 3.5), ("associate", 3.0)]:
        if kw in text:
            score += pts
    # Relevance
    for kw in ["machine learning", "data scientist", "data analyst", "ml", "ai", "llm", "computer vision", "nlp", "analytics"]:
        if kw in text:
            score += 1.2
    # Recency
    try:
        if item.posted_at:
            ts = item.posted_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days = max(0.0, (now - dt).total_seconds() / 86400.0)
            score += max(0.0, 10.0 - days)  # up to +10 for most recent
    except Exception:
        pass
    return score


@app.get("/jobs")
def get_jobs(
    what: str = Query("junior machine learning OR data analyst", min_length=2, max_length=120),
    where: Optional[str] = Query(None),
    page: int = Query(1, ge=1, le=10),
    results_per_page: int = Query(20, ge=10, le=50),
):
    """Return merged, deduped, ranked Canadian job postings from JSearch, Jooble, and Adzuna.

    Strategy: query JSearch first. If results < MIN_RESULTS_PRIMARY, query Jooble, then Adzuna.
    Local caching and rate guards keep total daily calls within ~80/day.
    """
    now = datetime.now(timezone.utc)
    where_val = _normalize_where(where)

    all_items: List[JobItem] = []
    sources_called: List[str] = []

    # Primary: JSearch
    j_items = _jsearch.search(what, where_val, page, results_per_page)
    if j_items:
        all_items.extend(j_items)
        sources_called.append("jsearch")

    # If not enough, call Jooble
    if len(all_items) < settings.min_results_primary:
        jo_items = _jooble.search(what, where_val, page, results_per_page)
        if jo_items:
            all_items.extend(jo_items)
            sources_called.append("jooble")

    # If still not enough, call Adzuna
    if len(all_items) < settings.min_results_primary:
        ad_items = _adzuna.search(what, where_val, page, results_per_page)
        if ad_items:
            all_items.extend(ad_items)
            sources_called.append("adzuna")

    # Dedup and rank
    deduped = _dedup(all_items)
    ranked = sorted(deduped, key=lambda it: _score(it, now), reverse=True)
    limited = ranked[: settings.max_results]

    return {
        "ok": True,
        "sources_called": sources_called,
        "count": len(limited),
        "items": [it.__dict__ for it in limited],
    }


@app.get("/api/scan")
def scan():
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    seen_jobs_data = load_seen_jobs()
    seen_jobs_data = cleanup_old_jobs(seen_jobs_data)

    raw_jobs = search_all_jobs()
    if not raw_jobs:
        return {
            "run_id": run_id,
            "count": 0,
            "matches": [],
            "message": "No jobs found from API",
        }

    new_jobs: List[Dict[str, Any]] = []
    today = datetime.now().strftime("%Y-%m-%d")

    for job in raw_jobs:
        title = job.get("job_title") or job.get("title") or "Unknown Title"
        company = job.get("employer_name") or job.get("company") or "Unknown Company"
        city = job.get("job_city") or ""
        country = job.get("job_country") or ""
        location = f"{city}, {country}".strip(", ") if city or country else job.get("location") or ""
        url = job.get("job_apply_link") or job.get("url") or ""
        posted_at = job.get("job_posted_at_datetime_utc") or job.get("posted_at") or ""

        # Freshness guard
        if not is_fresh_job(posted_at, JOB_MAX_AGE_DAYS):
            continue

        job_hash = create_job_hash(title, company, location)
        if job_hash in seen_jobs_data["seen_jobs"]:
            # Update last_seen
            seen_jobs_data["seen_jobs"][job_hash]["last_seen"] = today
            continue

        is_ok, reason = is_eligible_job(job)
        if is_ok:
            job_info = {
                "title": title,
                "company": company,
                "location": location if location else "Remote/Unknown",
                "url": url,
                "why_matched": reason,
            }
            new_jobs.append(job_info)

            seen_jobs_data["seen_jobs"][job_hash] = {
                "title": title,
                "company": company,
                "location": job_info["location"],
                "first_seen": today,
                "last_seen": today,
                "url": url,
            }

    seen_jobs_data["last_updated"] = datetime.now().isoformat()
    save_seen_jobs(seen_jobs_data)

    return {
        "run_id": run_id,
        "count": len(new_jobs),
        "matches": new_jobs,
        "total_jobs_scanned": len(raw_jobs),
        "total_jobs_in_memory": len(seen_jobs_data["seen_jobs"]),
    }


@app.get("/api/test-email")
def test_email():
    test_jobs = [{
        "title": "Test AI Engineer Position",
        "company": "Test Company",
        "location": "Toronto, CA",
        "url": "https://example.com/job",
        "why_matched": "Test email functionality",
    }]
    run_info = {"total_jobs_scanned": 1, "new_matches": 1}
    send_email(test_jobs, run_info)
    return {"message": "Test email sent"}


@app.get("/api/force-scan")
def force_scan():
    scan_jobs_automated()
    return {"message": "Forced scan completed"}


@app.get("/api/scan-and-email")
def scan_and_email():
    scan_jobs_automated()
    return {"ok": True, "message": "Scan executed and email attempted"}
