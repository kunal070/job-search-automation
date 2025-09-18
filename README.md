# Job Search Automation

Job Search Automation is a FastAPI service that aggregates job listings from multiple providers, enforces per-provider rate limits, caches responses, and emails tailored job digests. It is designed to run as a daily scanner while still exposing a rich API for ad-hoc queries.

## Highlights
- Aggregates results from JSearch, Jooble, and Adzuna behind a single `/jobs` endpoint
- Shared in-memory caching and provider-aware rate limiting to stay within API quotas
- Email digests delivered through SMTP with support for multiple recipients
- Background scheduler for automatic scans that can be toggled with `ENABLE_SCHEDULER`
- Structured logging to `uvicorn.out.log` and `uvicorn.err.log` for easy monitoring

## Project Structure
- `api/index.py` - FastAPI application, background scheduler, and email delivery pipeline
- `api/settings.py` - Environment-driven configuration loader
- `api/adapters/` - Provider-specific adapters and shared caching utilities
- `api/run_scan.py` - Helper script to trigger a scan from the command line

## Getting Started
### 1. Install Dependencies
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment
Create a `.env` file (already git-ignored) with the required keys:

| Variable | Description |
| --- | --- |
| `JSEARCH_API_KEY` | API key for the JSearch provider |
| `JOOBLE_API_KEY` | API key for the Jooble provider |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | Credentials for Adzuna search |
| `DEFAULT_COUNTRY` | Fallback location filter (default: `Canada`) |
| `MAX_RESULTS` | Maximum jobs to return per request |
| `MIN_RESULTS_PRIMARY` | Minimum jobs fetched from the primary source before falling back |
| `CACHE_TTL_SECONDS` | Cache lifetime in seconds |
| `RATE_LIMITS_JSON` | Optional JSON string to override per-provider rate limits |
| `SMTP_HOST` / `SMTP_PORT` | SMTP server details for email alerts |
| `SMTP_USE_TLS` / `SMTP_USE_SSL` | Toggle encrypted transport |
| `SENDER_EMAIL` / `EMAIL_APP_PASSWORD` | Credentials for the sender mailbox |
| `RECIPIENT_EMAILS` | Comma-separated list of recipients |
| `ENABLE_SCHEDULER` | Set to `false` to disable the background scheduler |

> Tip: copy `.env.example` to `.env` if you maintain a template of secrets for new environments.

## Running the API
```bash
uvicorn api.index:app --reload --port 8000
```

- `GET /api/health` confirms the service is ready
- `GET /jobs` fetches a unified list of job openings
- Logs are written to `uvicorn.out.log` and `uvicorn.err.log`

## Background Scans & Email Alerts
- The scheduler spins up on startup (unless `ENABLE_SCHEDULER=false`) and triggers periodic scans using `schedule`
- `GET /api/scan` fetches the latest jobs without sending email
- `GET /api/scan-and-email` runs a scan and emails the formatted digest
- `GET /api/force-scan` bypasses caching to force a fresh pull from providers
- `GET /api/test-email` sends a smoke-test email using the configured SMTP credentials

## Development Workflow
- Run `python api/run_scan.py` to manually trigger a scan from the CLI
- Adjust provider adapters in `api/adapters/` to tweak request parameters or parsing logic
- Update `api/settings.py` when introducing new environment-driven configuration values

## Deployment Notes
- Create production app passwords or service accounts for the SMTP sender
- Rotate API keys regularly and pass them securely via environment variables or your secret manager of choice
- Consider running Uvicorn behind a process manager (systemd, Supervisor, PM2) and terminating TLS at a reverse proxy such as Nginx or Caddy

Enjoy automated job hunting!
