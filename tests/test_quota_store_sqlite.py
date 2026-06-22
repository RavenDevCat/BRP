from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

from quota_store_sqlite import SqliteQuotaStore, safe_name  # noqa: E402


class SqliteQuotaStoreTests(unittest.TestCase):
    def test_safe_name_normalizes_provider_identifiers(self) -> None:
        self.assertEqual(safe_name("Google Routes / Directions"), "google_routes_directions")
        self.assertEqual(safe_name(""), "default")

    def test_reserve_usage_enforces_period_caps_and_marks_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SqliteQuotaStore(Path(tmpdir) / "quota.sqlite")

            result = store.reserve_usage(
                "google_routes",
                "directions",
                [("month", "2026-06", 3), ("day", "2026-06-21", 2)],
                count=2,
                provider_label="google_routes",
                sku_estimate="routes_compute_routes_pro",
            )
            self.assertEqual(result["month"]["attempted"], 2)
            self.assertEqual(result["day"]["attempted"], 2)

            with self.assertRaisesRegex(RuntimeError, "usage cap would be exceeded"):
                store.reserve_usage(
                    "google_routes",
                    "directions",
                    [("month", "2026-06", 3), ("day", "2026-06-21", 2)],
                    count=1,
                )

            store.mark_usage_result(
                "google_routes",
                "directions",
                [("month", "2026-06"), ("day", "2026-06-21")],
                succeeded=True,
            )
            store.mark_usage_result(
                "google_routes",
                "directions",
                [("month", "2026-06"), ("day", "2026-06-21")],
                succeeded=False,
            )

            usage = store.get_usage("google_routes", "directions", "month", "2026-06")
            self.assertEqual(usage["attempted"], 2)
            self.assertEqual(usage["succeeded"], 1)
            self.assertEqual(usage["failed"], 1)

    def test_migrates_legacy_flat_month_usage_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            usage_path = Path(tmpdir) / "google_geocode_usage.json"
            usage_path.write_text(json.dumps({"2026-06": 7}), encoding="utf-8")
            store = SqliteQuotaStore(Path(tmpdir) / "quota.sqlite")

            for _ in range(2):
                store.migrate_flat_month_usage(
                    source_path=usage_path,
                    provider="google_geocode",
                    counter="geocode",
                )

            usage = store.get_usage("google_geocode", "geocode", "month", "2026-06")
            self.assertEqual(usage["attempted"], 7)

    def test_migrates_legacy_bucket_usage_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            usage_path = Path(tmpdir) / "kakao_navi_usage.json"
            usage_path.write_text(
                json.dumps(
                    {
                        "2026-06": {"attempted": 4, "succeeded": 3, "failed": 1},
                        "2026-06-21": {"used": 2, "succeeded": 2},
                    }
                ),
                encoding="utf-8",
            )
            store = SqliteQuotaStore(Path(tmpdir) / "quota.sqlite")

            for _ in range(2):
                store.migrate_bucket_usage(
                    source_path=usage_path,
                    provider="kakao_navi",
                    counter="future_directions",
                )

            month_usage = store.get_usage("kakao_navi", "future_directions", "month", "2026-06")
            day_usage = store.get_usage("kakao_navi", "future_directions", "day", "2026-06-21")
            self.assertEqual(month_usage["attempted"], 4)
            self.assertEqual(month_usage["succeeded"], 3)
            self.assertEqual(month_usage["failed"], 1)
            self.assertEqual(day_usage["attempted"], 2)
            self.assertEqual(day_usage["succeeded"], 2)

    def test_migrates_legacy_nested_period_usage_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            usage_path = Path(tmpdir) / "google_geocode_relay_usage.json"
            usage_path.write_text(
                json.dumps({"days": {"2026-06-21": 2}, "months": {"2026-06": 9}}),
                encoding="utf-8",
            )
            store = SqliteQuotaStore(Path(tmpdir) / "quota.sqlite")

            for _ in range(2):
                store.migrate_nested_period_usage(
                    source_path=usage_path,
                    provider="google_geocode_relay",
                    counter="geocode",
                )

            day_usage = store.get_usage("google_geocode_relay", "geocode", "day", "2026-06-21")
            month_usage = store.get_usage("google_geocode_relay", "geocode", "month", "2026-06")
            self.assertEqual(day_usage["attempted"], 2)
            self.assertEqual(month_usage["attempted"], 9)

    def test_sums_google_usage_across_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SqliteQuotaStore(Path(tmpdir) / "quota.sqlite")
            store.reserve_usage("google_geocode", "geocode", [("month", "2026-06", 0)], count=7)
            store.reserve_usage("google_geocode_relay", "geocode", [("month", "2026-06", 0)], count=9)
            store.reserve_usage("kakao_navi", "directions", [("month", "2026-06", 0)], count=4)

            self.assertEqual(store.sum_usage(period_type="month", period_key="2026-06", contains="google"), 16)


if __name__ == "__main__":
    unittest.main()
