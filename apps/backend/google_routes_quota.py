"""Shared monthly policy for the Google timing client and private egress relay."""
from zoneinfo import ZoneInfo

MONTHLY_LIMIT = 10_000
DEFAULT_LIMITS = (0, 0, MONTHLY_LIMIT)


def quota_periods(budget_id, now, limits=DEFAULT_LIMITS):
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    # Keep historical counters for attribution; only the month limits spending.
    return [("task", budget_id, limits[0]),
            ("day", local.date().isoformat(), limits[1]),
            ("month", local.strftime("%Y-%m"), limits[2]),
            ("campaign", "google-final-pilot-v1", 0)]
