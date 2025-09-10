# api/index.py
from fastapi import FastAPI
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from typing import List, Dict, Any, Tuple
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

# ---- Render flag: keep your current behavior by default (true for local) ----
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "true").lower() == "true"

# Configuration
JSEARCH_API_KEY = os.getenv("JSEARCH_API_KEY", "your_rapidapi_key_here")
JSEARCH_URL = "https://jsearch.p.rapidapi.com/search"
JOBS_FILE = "jobs_seen.json"

# Email configuration
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "your-email@gmail.com")
SENDER_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "your-app-password")  # App Password for Gmail

# Multiple Recipients - Read from RECIPIENT_EMAILS and split by comma
RECIPIENT_EMAILS_STRING = os.getenv("RECIPIENT_EMAILS", "recipient@gmail.com")
RECIPIENT_EMAILS = [email.strip() for email in RECIPIENT_EMAILS_STRING.split(",") if email.strip()]

print(f"📧 Email will be sent to: {RECIPIENT_EMAILS}")  # Debug line to verify emails

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
    "beginner", "starting career", "internship", "co-op", "new to field",
    "career starter", "entry-level", "junior level", "0 years", "1 year",
    "up to 1.5", "less than 2", "under 2 years", "18 months", "one year",
]

# Updated senior keywords to exclude (stricter for 1.5+ years)
SENIOR_KEYWORDS = [
    "senior", "sr.", "lead", "principal", "manager", "director", "head of", "chief",
    "2+ years", "3+ years", "4+ years", "5+ years", "6+ years", "7+ years", "8+ years",
    "minimum 2 years", "minimum 3 years", "minimum 4 years", "minimum 5 years",
    "2-3 years", "3-5 years", "5-7 years", "experienced professional", "expert level",
    "architect", "team lead", "technical lead", "staff", "principal engineer",
]

# Keywords indicating ineligibility due to visa/citizenship requirements
INELIGIBLE_KEYWORDS = [
    "permanent resident", "pr required", "citizenship required",
    "security clearance", "must be citizen", "canadian citizen only",
    "us citizen only", "citizen required", "must be canadian citizen",
    "canadian pr required", "permanent residency required", "clearance required",
    "government clearance", "background clearance", "must have pr", "pr status required",
]


# ------------------------ Persistence helpers ------------------------

def load_seen_jobs() -> Dict[str, Any]:
    """Load previously seen jobs from JSON file."""
    if not os.path.exists(JOBS_FILE):
        return {"last_updated": "", "seen_jobs": {}}
    try:
        with open(JOBS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"last_updated": "", "seen_jobs": {}}


def save_seen_jobs(data: Dict[str, Any]) -> None:
    """Save seen jobs to JSON file."""
    try:
        with open(JOBS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving jobs file: {e}")


def create_job_hash(title: str, company: str, location: str) -> str:
    """Create unique hash for job deduplication."""
    combined = f"{title.lower()}-{company.lower()}-{location.lower()}"
    return hashlib.md5(combined.encode()).hexdigest()


def cleanup_old_jobs(seen_jobs: Dict[str, Any], days_threshold: int = 30) -> Dict[str, Any]:
    """Remove jobs older than threshold days."""
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


# ------------------------ Search helpers ------------------------

def search_jobs_by_query(query: str, page_num: int = 1) -> List[Dict[str, Any]]:
    """Search for jobs using JSearch API with specific query and page number."""
    headers = {
        "X-RapidAPI-Key": JSEARCH_API_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    params = {
        "query": query,
        "page": str(page_num),
        "num_pages": "1",
        "country": "ca",  # Canada
        "employment_types": "FULLTIME,PARTTIME,CONTRACTOR",
    }

    try:
        response = requests.get(JSEARCH_URL, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])
    except requests.exceptions.RequestException as e:
        print(f"API request failed for query '{query}' page {page_num}: {e}")
        return []
    except json.JSONDecodeError as e:
        print(f"JSON decode error for query '{query}' page {page_num}: {e}")
        return []


def search_all_jobs() -> List[Dict[str, Any]]:
    """AI/ML focused job search with 10 strategic queries (2 pages each)."""
    all_jobs: List[Dict[str, Any]] = []

    ai_ml_queries = [
        # Entry-level AI/ML roles
        "junior machine learning engineer canada",
        "entry level data scientist canada",
        "graduate ai engineer canada",
        "junior data analyst canada",
        # AI/ML specific roles
        "machine learning engineer new grad canada",
        "data science internship canada",
        "nlp engineer entry level canada",
        "computer vision engineer junior canada",
        # Data and LLM focused roles
        "data engineer fresh graduate canada",
        "llm engineer entry level canada",
    ]

    for query in ai_ml_queries:
        print(f"🔍 Searching: {query}")
        for page in range(1, 3):  # Pages 1-2
            jobs = search_jobs_by_query(query, page)
            if jobs:
                all_jobs.extend(jobs)
                print(f"   📄 Page {page}: Found {len(jobs)} jobs")
            else:
                break
            time.sleep(0.5)  # rate limit per page
        time.sleep(1)  # rate limit per query

    print(f"📊 Total AI/ML jobs collected: {len(all_jobs)}")
    return all_jobs


# ------------------------ Filtering ------------------------

def is_eligible_job(job: Dict[str, Any]) -> Tuple[bool, str]:
    """Strict filter for AI/ML jobs requiring 0-1.5 years experience maximum."""
    title = (job.get("job_title") or "").lower()
    description = (job.get("job_description") or "").lower()
    combined_text = f"{title} {description}"

    # 1) Visa/citizenship restrictions (STRICT)
    for keyword in INELIGIBLE_KEYWORDS:
        if keyword in combined_text:
            return False, f"Requires {keyword}"

    # 2) Senior position check (exclude ANY 2+ years indicators)
    for keyword in SENIOR_KEYWORDS:
        if keyword in combined_text:
            return False, f"Too much experience required: {keyword}"

    # 3) Relevant AI/ML/Data role keywords (STRICT)
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

    # 4) Explicit entry-level indicators (strong accept)
    entry_level_matches = [kw for kw in EXPERIENCE_KEYWORDS if kw in combined_text]
    if entry_level_matches:
        return True, f"✅ {role_matches[0]} - {entry_level_matches[0]}"

    # 5) Acceptable explicit ranges (0–1.5 yrs)
    acceptable_experience = [
        "0-1 years", "0-1.5 years", "1-1.5 years", "0 to 1", "0 to 1.5",
        "up to 1", "up to 1.5", "less than 2", "under 2 years", "1 year",
        "one year", "18 months", "0-18 months", "1.5 years max", "maximum 1.5",
    ]
    if any(exp in combined_text for exp in acceptable_experience):
        return True, f"✅ {role_matches[0]} (acceptable experience: 0-1.5 years)"

    # 6) Potentially problematic experience mentions
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
            return True, f"🟡 {role_matches[0]} (flexible experience - entry level welcome)"
        return False, "Experience requirements unclear - likely requires >1.5 years"

    # 7) Training/graduate program indicators
    entry_indicators = [
        "training provided", "will train", "learn on job", "mentorship",
        "graduate program", "rotational program", "development program",
        "internship program", "apprenticeship", "on the job training",
    ]
    if any(ind in combined_text for ind in entry_indicators):
        return True, f"🟢 {role_matches[0]} (training provided - perfect for new grads)"

    # 8) Default: relevant role and no explicit years => treat as suitable
    return True, f"🟢 {role_matches[0]} (no experience requirements - suitable for entry level)"


# ------------------------ Email ------------------------

def send_email(new_jobs: List[Dict[str, Any]], run_info: Dict[str, Any]) -> None:
    """Send email notification with new job matches."""
    if not new_jobs:
        return

    try:
        # Create message
        msg = MIMEMultipart()
        msg["From"] = SENDER_EMAIL
        msg["To"] = ", ".join(RECIPIENT_EMAILS)  # For display in header
        msg["Subject"] = f"🚨 {len(new_jobs)} New Job Matches Found! - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        # Create HTML body
        html_body = f"""
        <html>
        <body>
            <h2>🎯 New Job Opportunities Found!</h2>
            <p><strong>Scan Time:</strong> {datetime.now().strftime('%Y-%m-%d at %H:%M')}</p>
            <p><strong>New Matches:</strong> {len(new_jobs)}</p>
            <p><strong>Total Jobs Scanned:</strong> {run_info.get('total_jobs_scanned', 'Unknown')}</p>
            <h3>📋 Job Matches:</h3>
        """

        for i, job in enumerate(new_jobs, 1):
            html_body += f"""
            <div style="border:1px solid #ddd; padding:15px; margin:10px 0; border-radius:5px;">
                <h4 style="color:#2c3e50; margin:0 0 10px 0;">{i}. {job['title']}</h4>
                <p><strong>🏢 Company:</strong> {job['company']}</p>
                <p><strong>📍 Location:</strong> {job['location']}</p>
                <p><strong>✅ Why Matched:</strong> <em>{job['why_matched']}</em></p>
                <p><strong>🔗 Apply:</strong> <a href="{job['url']}" target="_blank">View Job Posting</a></p>
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

        # Send email to all recipients
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAILS, msg.as_string())

        print(f"✅ Email sent successfully to {len(RECIPIENT_EMAILS)} recipients with {len(new_jobs)} job matches")
        print(f"📧 Recipients: {', '.join(RECIPIENT_EMAILS)}")

    except Exception as e:
        print(f"❌ Error sending email: {e}")
        import traceback
        traceback.print_exc()


# ------------------------ Scan & schedule ------------------------

def scan_jobs_automated() -> None:
    """Automated job scanning function."""
    print(f"\n🔍 Starting automated job scan at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        # Load seen jobs and cleanup old ones
        seen_jobs_data = load_seen_jobs()
        seen_jobs_data = cleanup_old_jobs(seen_jobs_data)

        # Search for new jobs
        raw_jobs = search_all_jobs()
        if not raw_jobs:
            print("⚠️  No jobs found from API")
            return

        new_jobs: List[Dict[str, Any]] = []
        today = datetime.now().strftime("%Y-%m-%d")

        for job in raw_jobs:
            title = job.get("job_title", "Unknown Title") or "Unknown Title"
            company = job.get("employer_name", "Unknown Company") or "Unknown Company"
            city = job.get("job_city") or ""
            country = job.get("job_country") or ""
            location = f"{city}, {country}".strip(", ")
            url = job.get("job_apply_link") or ""

            job_hash = create_job_hash(title, company, location)
            if job_hash in seen_jobs_data["seen_jobs"]:
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

        print(f"📊 Scan complete: {len(new_jobs)} new matches from {len(raw_jobs)} jobs scanned")

        if new_jobs:
            send_email(new_jobs, run_info)
        else:
            print("📭 No new eligible jobs found")

    except Exception as e:
        print(f"❌ Error in automated scan: {e}")


def schedule_jobs() -> None:
    """Set up job scheduling."""
    schedule.every().day.at("08:00").do(scan_jobs_automated)
    schedule.every().day.at("12:37").do(scan_jobs_automated)
    schedule.every().day.at("17:00").do(scan_jobs_automated)
    schedule.every().day.at("23:00").do(scan_jobs_automated)

    print("⏰ Job scheduler initialized - scanning at 8 AM, 12 PM, 5 PM, and 11 PM daily")

    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute


def start_scheduler() -> None:
    """Start the scheduler in a separate thread."""
    scheduler_thread = threading.Thread(target=schedule_jobs, daemon=True)
    scheduler_thread.start()


# ------------------------ FastAPI lifecycle & routes ------------------------

@app.on_event("startup")
async def startup_event():
    """Initialize scheduler when FastAPI starts."""
    if ENABLE_SCHEDULER:
        start_scheduler()
        print("🚀 Job scanner started with automatic scheduling")
    else:
        print("⏸️  In-process scheduler disabled (ENABLE_SCHEDULER=false)")


@app.get("/")
def root():
    return {"ok": True, "message": "Fresh Graduate Job Scanner API", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/health")
def health():
    return {"status": "up", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/scan")
def scan():
    """Manual job scanning endpoint."""
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
        title = job.get("job_title", "Unknown Title") or "Unknown Title"
        company = job.get("employer_name", "Unknown Company") or "Unknown Company"
        city = job.get("job_city") or ""
        country = job.get("job_country") or ""
        location = f"{city}, {country}".strip(", ")
        url = job.get("job_apply_link") or ""

        job_hash = create_job_hash(title, company, location)
        if job_hash in seen_jobs_data["seen_jobs"]:
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
    """Test email functionality."""
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
    """Force an immediate scan (useful for testing)."""
    scan_jobs_automated()
    return {"message": "Forced scan completed"}


# Convenience endpoint for Render (optional): directly trigger scan+email
@app.get("/api/scan-and-email")
def scan_and_email():
    scan_jobs_automated()
    return {"ok": True, "message": "Scan executed and email attempted"}
