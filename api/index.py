# api/index.py
from fastapi import FastAPI
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests
import json
import hashlib
import os
from typing import List, Dict, Any

# Load environment variables from .env file (for local testing)
load_dotenv()

app = FastAPI()

# Configuration
JSEARCH_API_KEY = os.getenv("JSEARCH_API_KEY", "your_rapidapi_key_here")
JSEARCH_URL = "https://jsearch.p.rapidapi.com/search"
JOBS_FILE = "jobs_seen.json"

# Keywords for filtering
ELIGIBLE_KEYWORDS = [
    "co-op", "coop", "intern", "internship", "co-operative", 
    "work-study", "student", "new grad", "entry level"
]

INELIGIBLE_KEYWORDS = [
    "permanent resident", "pr required", "citizenship required",
    "security clearance", "must be citizen", "canadian citizen only"
]

# Season/Date filtering for Fall 2025
FALL_KEYWORDS = [
    "fall 2025", "september 2025", "sept 2025", "sep 2025",
    "fall", "september", "sept", "sep", "autumn 2025",
    "starting september", "begin september", "sep-dec", "sept-dec"
]

EXCLUDE_SEASONS = [
    "winter 2025", "winter 2026", "spring 2025", "spring 2026", 
    "summer 2025", "january 2025", "january 2026", "jan 2025", "jan 2026",
    "may 2025", "may 2026", "summer", "january", "jan", "may"
]

def load_seen_jobs() -> Dict:
    """Load previously seen jobs from JSON file"""
    if not os.path.exists(JOBS_FILE):
        return {"last_updated": "", "seen_jobs": {}}
    
    try:
        with open(JOBS_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"last_updated": "", "seen_jobs": {}}

def save_seen_jobs(data: Dict):
    """Save seen jobs to JSON file"""
    try:
        with open(JOBS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving jobs file: {e}")

def create_job_hash(title: str, company: str, location: str) -> str:
    """Create unique hash for job deduplication"""
    combined = f"{title.lower()}-{company.lower()}-{location.lower()}"
    return hashlib.md5(combined.encode()).hexdigest()

def cleanup_old_jobs(seen_jobs: Dict, days_threshold: int = 30):
    """Remove jobs older than threshold days"""
    from datetime import datetime, timedelta
    
    cutoff_date = datetime.now() - timedelta(days=days_threshold)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")
    
    jobs_to_remove = []
    for job_hash, job_data in seen_jobs["seen_jobs"].items():
        if job_data.get("first_seen", "0000-00-00") < cutoff_str:
            jobs_to_remove.append(job_hash)
    
    for job_hash in jobs_to_remove:
        del seen_jobs["seen_jobs"][job_hash]
    
    return seen_jobs

def is_eligible_job(job: Dict) -> tuple[bool, str]:
    """Check if job is eligible based on description and title"""
    title = (job.get("job_title") or "").lower()
    description = (job.get("job_description") or "").lower()
    combined_text = f"{title} {description}"
    
    # Check for ineligible keywords first
    for keyword in INELIGIBLE_KEYWORDS:
        if keyword in combined_text:
            return False, f"Requires {keyword}"
    
    # Check for excluded seasons/terms
    for season in EXCLUDE_SEASONS:
        if season in combined_text:
            return False, f"Wrong term: {season}"
    
    # Check for eligible keywords
    eligible_match = None
    for keyword in ELIGIBLE_KEYWORDS:
        if keyword in combined_text:
            eligible_match = keyword
            break
    
    if not eligible_match:
        return False, "No eligible keywords found"
    
    # Check for Fall 2025 terms (preferred)
    fall_match = None
    for fall_term in FALL_KEYWORDS:
        if fall_term in combined_text:
            fall_match = fall_term
            break
    
    if fall_match:
        return True, f"Fall 2025 {eligible_match} (matched '{fall_match}')"
    
    # If no specific fall term mentioned, check if it's generic enough to include
    # (many co-ops don't specify exact terms)
    current_year = "2025"
    if current_year not in combined_text:
        # Generic co-op without year - could be fall
        return True, f"Generic {eligible_match} (could be Fall 2025)"
    
    return False, "Not Fall 2025 term"

def search_jobs() -> List[Dict]:
    """Search for jobs using JSearch API"""
    
    headers = {
        "X-RapidAPI-Key": JSEARCH_API_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }
    
    # Search parameters
    params = {
        "query": "software co-op fall 2025 september canada",
        "page": "1", 
        "num_pages": "1",
        "country": "ca",
        "employment_types": "INTERN"
    }
    
    try:
        response = requests.get(JSEARCH_URL, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        return data.get("data", [])
    
    except requests.exceptions.RequestException as e:
        print(f"API request failed: {e}")
        return []
    except json.JSONDecodeError as e:
        print(f"JSON decode error: {e}")
        return []

@app.get("/")
def root():
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}

@app.get("/api/health")
def health():
    return {"status": "up", "ts": datetime.now(timezone.utc).isoformat()}

@app.get("/api/scan")
def scan():
    """Main job scanning endpoint"""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Load seen jobs and cleanup old ones
    seen_jobs_data = load_seen_jobs()
    seen_jobs_data = cleanup_old_jobs(seen_jobs_data)
    
    # Search for new jobs
    raw_jobs = search_jobs()
    
    if not raw_jobs:
        return {
            "run_id": run_id,
            "count": 0,
            "matches": [],
            "message": "No jobs found from API"
        }
    
    new_jobs = []
    today = datetime.now().strftime("%Y-%m-%d")
    
    for job in raw_jobs:
        # Extract job details
        title = job.get("job_title", "Unknown Title") or "Unknown Title"
        company = job.get("employer_name", "Unknown Company") or "Unknown Company"
        city = job.get("job_city") or ""
        country = job.get("job_country") or ""
        location = f"{city}, {country}".strip(", ")
        url = job.get("job_apply_link") or ""
        
        # Create hash for deduplication
        job_hash = create_job_hash(title, company, location)
        
        # Skip if we've seen this job before
        if job_hash in seen_jobs_data["seen_jobs"]:
            continue
        
        # Check eligibility
        is_eligible, reason = is_eligible_job(job)
        
        if is_eligible:
            # Add to new jobs list
            job_info = {
                "title": title,
                "company": company,
                "location": location if location else "Remote/Unknown",
                "url": url,
                "why_matched": reason
            }
            new_jobs.append(job_info)
            
            # Mark as seen
            seen_jobs_data["seen_jobs"][job_hash] = {
                "title": title,
                "company": company,
                "location": location if location else "Remote/Unknown",
                "first_seen": today,
                "url": url
            }
    
    # Update last scan time
    seen_jobs_data["last_updated"] = datetime.now().isoformat()
    
    # Save updated seen jobs
    save_seen_jobs(seen_jobs_data)
    
    return {
        "run_id": run_id,
        "count": len(new_jobs),
        "matches": new_jobs,
        "total_jobs_scanned": len(raw_jobs),
        "total_jobs_in_memory": len(seen_jobs_data["seen_jobs"])
    }