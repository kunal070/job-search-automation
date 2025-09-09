# api/run_scan.py
import os
from zoneinfo import ZoneInfo
from datetime import datetime
from .index import scan_jobs_automated

def _should_run_now() -> bool:
    """
    If RUN_HOURS_LOCAL and GATE_TZ are set,
    only run when the local hour is in the allowlist (e.g., 8,12,17,23).
    This makes the single hourly Render job DST-safe.
    """
    hours_csv = os.getenv("RUN_HOURS_LOCAL", "")
    tz_name = os.getenv("GATE_TZ", "")
    if not hours_csv or not tz_name:
        return True  # no gating configured

    allowed = {int(h.strip()) for h in hours_csv.split(",") if h.strip().isdigit()}
    now_local = datetime.now(ZoneInfo(tz_name))
    return now_local.hour in allowed

if __name__ == "__main__":
    if _should_run_now():
        print("⏰ Gate passed — running scan_jobs_automated()")
        scan_jobs_automated()
    else:
        print("⏭️  Gate skipped — current local hour not in RUN_HOURS_LOCAL")
