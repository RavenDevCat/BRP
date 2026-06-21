from __future__ import annotations

from quota_store_sqlite import SqliteQuotaStore, safe_name


class CrossProcessRateLimiter:
    def __init__(self, name: str, max_qps: float) -> None:
        self.name = safe_name(name)
        self.max_qps = max_qps
        self.store = SqliteQuotaStore()

    def wait(self) -> None:
        if self.max_qps <= 0:
            return
        self.store.reserve_rate_limit(self.name, self.max_qps)
