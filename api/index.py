# api/index.py
from fastapi import FastAPI
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests
import json
import hashlib
import os
import smtplib
import schedule
import time
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from typing import List, Dict, Any

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

# ---- Render flag: keep your current behavior by default (true for local) ----
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "true").lower() == "true"

# Configuration
JSEARCH_API_KEY = os.getenv("JSEARCH_API_KEY", "your_rapidapi_key_here")
JSEARCH_URL = "https://jsearch.p.rapidapi.com/search"
JOBS_FILE = "jobs_seen.json"

# email configuration
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "your-email@gmail.com")
SENDER_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "your-app-password")  # Use App Password for Gmail

# Multiple Recipients - Read from RECIPIENT_EMAILS and split by comma
RECIPIENT_EMAILS_STRING = os.getenv("RECIPIENT_EMAILS", "recipient@gmail.com")
RECIPIENT_EMAILS = [email.strip() for email in RECIPIENT_EMAILS_STRING.split(",")]

print(f"📧 Email will be sent to: {RECIPIENT_EMAILS}")  # Debug line to verify emails

ROLE_KEYWORDS = [
    # AI/ML Keywords
    "ai engineer", "artificial intelligence", "machine learning", "ml engineer", 
    "data engineer", "data scientist", "nlp engineer", "computer vision", 
    "deep learning", "neural network", "ai specialist", "ai developer",
    
    # Software Development Keywords
    "software engineer", "software developer", "full stack developer", 
    "backend developer", "frontend developer", "web developer", "mobile developer",
    "python developer", "java developer", "javascript developer", "react developer",
    "node.js developer", ".net developer", "php developer",
    
    # Data & Analytics Keywords
    "data analyst", "business intelligence", "data visualization", "sql developer",
    "database administrator", "etl developer", "analytics engineer", "reporting analyst",
    
    # DevOps & Cloud Keywords
    "devops engineer", "cloud engineer", "aws developer", "azure developer", 
    "kubernetes", "docker", "automation engineer", "infrastructure engineer",
    "site reliability engineer", "platform engineer",
    
    # QA & Testing Keywords
    "qa engineer", "test engineer", "automation tester", "quality assurance",
    "software tester", "manual tester",
    
    # Technical Support & IT Keywords
    "technical support", "it support", "help desk", "system administrator",
    "network administrator", "cybersecurity", "security analyst"
]

# More flexible experience keywords
EXPERIENCE_KEYWORDS = [
    "entry level", "junior", "associate", "fresh graduate", "new grad", "graduate",
    "0-1 years", "0-2 years", "1-2 years", "no experience required", "recent graduate",
    "graduate program", "trainee", "beginner", "starting career", "internship",
    "co-op", "new to field", "career starter", "entry-level", "junior level",
    "0 years", "1 year", "2 years", "less than", "up to 2", "minimum 0"
]

# Updated senior keywords to be more specific
SENIOR_KEYWORDS = [
    "senior", "sr.", "lead", "principal", "manager", "director", "head of", "chief",
    "5+ years", "6+ years", "7+ years", "8+ years", "3+ years experience required",
    "4+ years experience required", "5+ years experience required", "minimum 5 years",
    "minimum 4 years", "minimum 3 years", "experienced professional", "expert level",
    "architect", "team lead", "technical lead"
]

INELIGIBLE_KEYWORDS = [
    "permanent resident", "pr required", "citizenship required",
    "security clearance", "must be citizen", "canadian citizen only",
    "us citizen only", "citizen required", "must be canadian citizen",
    "canadian pr required", "permanent residency required", "clearance required",
    "government clearance", "background clearance", "must have pr", "pr status required"
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

def search_jobs_by_query(query: str, page_num: int = 1) -> List[Dict]:
    """Search for jobs using JSearch API with specific query and page number"""
    headers = {
        "X-RapidAPI-Key": JSEARCH_API_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }
    
    params = {
        "query": query,
        "page": str(page_num), 
        "num_pages": "1",
        "country": "ca",  # Canada
        "employment_types": "FULLTIME,PARTTIME,CONTRACTOR"  # Include more employment types
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

def search_all_jobs() -> List[Dict]:
    """Search for jobs using multiple queries and pages to get comprehensive results"""
    all_jobs = []
    
    # More diverse search queries
    search_queries = [
        # Generic tech searches
        "software engineer canada",
        "software developer canada", 
        "python developer canada",
        "web developer canada",
        "full stack developer canada",
        
        # Entry level specific
        "entry level software engineer canada",
        "junior developer canada",
        "graduate software engineer canada",
        "new grad tech canada",
        
        # AI/ML specific
        "data scientist canada",
        "data engineer canada", 
        "machine learning engineer canada",
        "ai engineer canada",
        
        # Other tech roles
        "qa engineer canada",
        "devops engineer canada",
        "technical support canada",
        "database developer canada",
        "automation engineer canada",
        
        # Location specific searches
        "software engineer toronto",
        "software engineer vancouver",
        "software engineer montreal", 
        "software engineer ottawa",
        "software engineer calgary",
        
        # Company size searches
        "startup software engineer canada",
        "remote software engineer canada",
        "tech internship canada"
    ]
    
    for query in search_queries:
        print(f"🔍 Searching: {query}")
        
        # Search multiple pages for each query
        for page in range(1, 4):  # Search pages 1, 2, 3
            jobs = search_jobs_by_query(query, page)
            if jobs:
                all_jobs.extend(jobs)
                print(f"   📄 Page {page}: Found {len(jobs)} jobs")
            else:
                break  # No more jobs on this page, move to next query
            
            time.sleep(0.5)  # Rate limiting between pages
        
        time.sleep(1)  # Rate limiting between queries
    
    print(f"📊 Total jobs collected: {len(all_jobs)}")
    return all_jobs

def is_eligible_job(job: Dict) -> tuple[bool, str]:
    """Strict filter for jobs requiring 0-1.5 years experience maximum"""
    title = (job.get("job_title") or "").lower()
    description = (job.get("job_description") or "").lower()
    combined_text = f"{title} {description}"
    
    # 1. Check for visa/citizenship restrictions first (STRICT)
    for keyword in INELIGIBLE_KEYWORDS:
        if keyword in combined_text:
            return False, f"Requires {keyword}"
    
    # 2. Strict senior position check - exclude ANY job requiring 2+ years
    senior_matches = []
    for keyword in SENIOR_KEYWORDS:
        if keyword in combined_text:
            senior_matches.append(keyword)
    
    # Reject if ANY indicators of 2+ years experience
    if senior_matches:
        return False, f"Too much experience required: {senior_matches[0]}"
    
    # 3. Check for relevant role keywords
    role_matches = []
    for keyword in ROLE_KEYWORDS:
        if keyword in combined_text:
            role_matches.append(keyword)
    
    if not role_matches:
        # Check for basic tech terms
        tech_terms = ["software", "developer", "engineer", "programmer", "coding", "programming", 
                     "technical", "technology", "computer", "IT", "system", "web", "mobile", "data"]
        if any(term in combined_text for term in tech_terms):
            role_matches = ["tech-related"]
        else:
            return False, "No relevant tech/AI role keywords found"
    
    # 4. Look for explicit 0-1.5 years experience indicators
    entry_level_matches = []
    for keyword in EXPERIENCE_KEYWORDS:
        if keyword in combined_text:
            entry_level_matches.append(keyword)
    
    # If explicit 0-1.5 years keywords found - ACCEPT
    if entry_level_matches:
        return True, f"✅ {role_matches[0]} - {entry_level_matches[0]}"
    
    # 5. Check for acceptable experience ranges (0-1.5 years)
    acceptable_experience = [
        "0-1 years", "0-1.5 years", "1-2 years", "0 to 1", "0 to 1.5", 
        "up to 1", "up to 1.5", "less than 2", "under 2 years", "1 year",
        "one year", "18 months", "0-18 months", "1.5 years"
    ]
    
    has_acceptable_exp = any(exp in combined_text for exp in acceptable_experience)
    if has_acceptable_exp:
        return True, f"✅ {role_matches[0]} (acceptable experience range: 0-1.5 years)"
    
    # 6. Check for problematic experience requirements (2+ years)
    problematic_experience = [
        "years of experience", "years experience", "years in", "minimum years",
        "must have experience", "required experience", "proven experience",
        "extensive experience", "solid experience", "strong experience"
    ]
    
    has_experience_req = any(exp in combined_text for exp in problematic_experience)
    
    # If general experience requirements mentioned, be cautious
    if has_experience_req:
        # Only accept if it's clearly flexible and doesn't specify amount
        flexible_terms = [
            "preferred but not required", "nice to have", "bonus", "plus", 
            "would be great", "ideal but not required", "strongly preferred but not required",
            "some experience helpful", "any experience", "little experience"
        ]
        
        if any(flexible in combined_text for flexible in flexible_terms):
            return True, f"🟡 {role_matches[0]} (flexible experience - preferred not required)"
        else:
            return False, "General experience requirements mentioned - unclear if suitable for 0-1.5 years"
    
    # 7. No experience requirements mentioned - perfect for entry level
    return True, f"🟢 {role_matches[0]} (no experience requirements - perfect for fresh graduates)"
    """Check if job is eligible for fresh graduates in AI/Tech roles"""
    title = (job.get("job_title") or "").lower()
    description = (job.get("job_description") or "").lower()
    combined_text = f"{title} {description}"
    
    # Check for visa/citizenship restrictions first
    for keyword in INELIGIBLE_KEYWORDS:
        if keyword in combined_text:
            return False, f"Requires {keyword}"
    
    # Check for senior/experienced positions (exclude)
    for keyword in SENIOR_KEYWORDS:
        if keyword in combined_text:
            return False, f"Too senior: requires {keyword}"
    
    # Check for relevant role keywords
    role_match = None
    for keyword in ROLE_KEYWORDS:
        if keyword in combined_text:
            role_match = keyword
            break
    
    if not role_match:
        return False, "No relevant AI/Tech role keywords found"
    
    # Check for entry-level indicators
    entry_level_match = None
    for keyword in EXPERIENCE_KEYWORDS:
        if keyword in combined_text:
            entry_level_match = keyword
            break
    
    # If explicit entry-level keywords found
    if entry_level_match:
        return True, f"Entry-level {role_match} (matched '{entry_level_match}')"
    
    # If no explicit entry-level mention, check if it's a general posting
    # that doesn't mention years of experience (could be entry-level)
    years_mentioned = any(year in combined_text for year in ["years", "experience"])
    if not years_mentioned:
        return True, f"{role_match} (no experience requirements specified)"
    
    return False, "Experience requirements too high"

    """Search for jobs using JSearch API with specific query"""
    headers = {
        "X-RapidAPI-Key": JSEARCH_API_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }
    
    params = {
        "query": query,
        "page": "1", 
        "num_pages": "2",  # Search more pages for better results
        "country": "ca",  # Canada
        "employment_types": "FULLTIME"
    }
    
    try:
        response = requests.get(JSEARCH_URL, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        return data.get("data", [])
    
    except requests.exceptions.RequestException as e:
        print(f"API request failed for query '{query}': {e}")
        return []
    except json.JSONDecodeError as e:
        print(f"JSON decode error for query '{query}': {e}")
        return []

    """Search for jobs using multiple queries to get comprehensive results"""
    all_jobs = []
    
    # Multiple search queries for better coverage
    search_queries = [
        "software engineer entry level canada",
        "ai engineer junior canada", 
        "data engineer fresh graduate canada",
        "machine learning engineer new grad canada",
        "python developer entry level canada",
        "automation engineer junior canada"
    ]
    
    for query in search_queries:
        jobs = search_jobs_by_query(query)
        all_jobs.extend(jobs)
        time.sleep(1)  # Rate limiting
    
    return all_jobs

def send_email(new_jobs: List[Dict], run_info: Dict):
    """Send email notification with new job matches"""
    if not new_jobs:
        return
    
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = ", ".join(RECIPIENT_EMAILS)  # Join emails with comma for display
        msg['Subject'] = f"🚨 {len(new_jobs)} New Job Matches Found! - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
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
            <div style="border: 1px solid #ddd; padding: 15px; margin: 10px 0; border-radius: 5px;">
                <h4 style="color: #2c3e50; margin: 0 0 10px 0;">{i}. {job['title']}</h4>
                <p><strong>🏢 Company:</strong> {job['company']}</p>
                <p><strong>📍 Location:</strong> {job['location']}</p>
                <p><strong>✅ Why Matched:</strong> <em>{job['why_matched']}</em></p>
                <p><strong>🔗 Apply:</strong> <a href="{job['url']}" target="_blank">View Job Posting</a></p>
            </div>
            """
        
        html_body += f"""
            <p style="color: #7f8c8d; font-size: 12px; margin-top: 30px;">
                This automated job alert was sent to {len(RECIPIENT_EMAILS)} recipient(s).<br>
                Jobs are filtered for AI/Tech roles suitable for fresh graduates in Canada.
            </p>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(html_body, 'html'))
        
        # Send email to all recipients
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            text = msg.as_string()
            # Pass individual emails in the list, not the list itself
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAILS, text)
        
        print(f"✅ Email sent successfully to {len(RECIPIENT_EMAILS)} recipients with {len(new_jobs)} job matches")
        print(f"📧 Recipients: {', '.join(RECIPIENT_EMAILS)}")
        
    except Exception as e:
        print(f"❌ Error sending email: {e}")
        # Add more detailed error info
        import traceback
        traceback.print_exc()
    """Send email notification with new job matches"""
    if not new_jobs:
        return
    
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECIPIENT_EMAILS
        msg['Subject'] = f"🚨 {len(new_jobs)} New Job Matches Found! - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
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
            <div style="border: 1px solid #ddd; padding: 15px; margin: 10px 0; border-radius: 5px;">
                <h4 style="color: #2c3e50; margin: 0 0 10px 0;">{i}. {job['title']}</h4>
                <p><strong>🏢 Company:</strong> {job['company']}</p>
                <p><strong>📍 Location:</strong> {job['location']}</p>
                <p><strong>✅ Why Matched:</strong> <em>{job['why_matched']}</em></p>
                <p><strong>🔗 Apply:</strong> <a href="{job['url']}" target="_blank">View Job Posting</a></p>
            </div>
            """
        
        html_body += f"""
            <p style="color: #7f8c8d; font-size: 12px; margin-top: 30px;">
                This automated job alert was sent to {len(RECIPIENT_EMAILS)} recipient(s).<br>
                Jobs are filtered for AI/Tech roles suitable for fresh graduates in Canada.
            </p>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(html_body, 'html'))
        
        # Send email to all recipients
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            text = msg.as_string()
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAILS, text)
        
        print(f"✅ Email sent successfully to {len(RECIPIENT_EMAILS)} recipients with {len(new_jobs)} job matches")
        print(f"📧 Recipients: {', '.join(RECIPIENT_EMAILS)}")
        
    except Exception as e:
        print(f"❌ Error sending email: {e}")

def scan_jobs_automated():
    """Automated job scanning function"""
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
        
        run_info = {
            "total_jobs_scanned": len(raw_jobs),
            "new_matches": len(new_jobs),
            "total_in_memory": len(seen_jobs_data["seen_jobs"])
        }
        
        print(f"📊 Scan complete: {len(new_jobs)} new matches from {len(raw_jobs)} jobs scanned")
        
        # Send email if new jobs found
        if new_jobs:
            send_email(new_jobs, run_info)
        else:
            print("📭 No new eligible jobs found")
            
    except Exception as e:
        print(f"❌ Error in automated scan: {e}")

def schedule_jobs():
    """Set up job scheduling"""
    # Schedule scans at 8 AM, 12 PM, 5 PM, and 11 PM
    schedule.every().day.at("08:00").do(scan_jobs_automated)
    schedule.every().day.at("12:37").do(scan_jobs_automated)  
    schedule.every().day.at("17:00").do(scan_jobs_automated)
    schedule.every().day.at("23:00").do(scan_jobs_automated)
    
    print("⏰ Job scheduler initialized - scanning at 8 AM, 12 PM, 5 PM, and 11 PM daily")
    
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

def start_scheduler():
    """Start the scheduler in a separate thread"""
    scheduler_thread = threading.Thread(target=schedule_jobs)
    scheduler_thread.daemon = True
    scheduler_thread.start()

@app.on_event("startup")
async def startup_event():
    """Initialize scheduler when FastAPI starts"""
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
    """Manual job scanning endpoint"""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Load seen jobs and cleanup old ones
    seen_jobs_data = load_seen_jobs()
    seen_jobs_data = cleanup_old_jobs(seen_jobs_data)
    
    # Search for new jobs
    raw_jobs = search_all_jobs()
    
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

@app.get("/api/test-email")
def test_email():
    """Test email functionality"""
    test_jobs = [{
        "title": "Test AI Engineer Position",
        "company": "Test Company",
        "location": "Toronto, CA", 
        "url": "https://example.com/job",
        "why_matched": "Test email functionality"
    }]
    
    run_info = {
        "total_jobs_scanned": 1,
        "new_matches": 1
    }
    
    send_email(test_jobs, run_info)
    return {"message": "Test email sent"}

@app.get("/api/force-scan")
def force_scan():
    """Force an immediate scan (useful for testing)"""
    scan_jobs_automated()
    return {"message": "Forced scan completed"}

# Convenience endpoint for Render (optional): directly trigger scan+email
@app.get("/api/scan-and-email")
def scan_and_email():
    scan_jobs_automated()
    return {"ok": True, "message": "Scan executed and email attempted"}
