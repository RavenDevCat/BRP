"""Monthly-only quotas: no external requests or production counters."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from test_google_final_validation import client, NOW, POINTS
import google_final_validation as g
from google_routes_quota import MONTHLY_LIMIT, quota_periods
from test_google_routes_relay import cfg, envelope, fake_session, auth, relay
from fastapi.testclient import TestClient
from types import SimpleNamespace


def seed(client, count):
    client.store.reserve_usage("google_routes", "compute_routes_pro", client.periods(), count=count)


def test_existing_usage_preserved_and_247_call_workbook_allowed(client):
    seed(client, 65)
    assert client.limits == (0, 0, 10000)
    assert client.can_afford(247)
    assert client.can_afford(9935)
    assert not client.can_afford(9936)
    assert client.store.get_usage("google_routes", "compute_routes_pro", "month", "2030-01")["attempted"] == 65


def test_old_task_day_and_campaign_ceilings_do_not_block(client):
    seed(client, 700)
    assert client.can_afford(247)
    client.route(POINTS, NOW+timedelta(days=1))
    assert client.calls == 1
    for kind, key, _ in client.periods():
        assert client.store.get_usage("google_routes", "compute_routes_pro", kind, key)["attempted"] == 701


def test_exact_month_limit_allowed_then_blocks_and_survives_restart(client):
    seed(client, 9999)
    client.route(POINTS, NOW+timedelta(days=1))
    restarted = g.GoogleRoutesClient("another-task", client.store, transport=client.transport, now=client.now)
    assert not restarted.can_afford(1)
    with pytest.raises(g.ValidationUnavailable, match="budget_cap"):
        restarted.route(POINTS, NOW+timedelta(days=1))
    assert restarted.calls == 0


def test_shanghai_month_rollover_not_forecast_month(client):
    seed(client, 10000)
    client.now = lambda: datetime(2030, 1, 31, 15, 59, 59, tzinfo=timezone.utc)
    assert not client.can_afford(1)
    client.now = lambda: datetime(2030, 1, 31, 16, 0, tzinfo=timezone.utc)
    assert client.can_afford(10000)
    client.route(POINTS, client.now()+timedelta(days=1))
    assert client.store.get_usage("google_routes", "compute_routes_pro", "month", "2030-01")["attempted"] == 10000
    assert client.store.get_usage("google_routes", "compute_routes_pro", "month", "2030-02")["attempted"] == 1


def test_concurrent_tasks_share_atomic_monthly_cap(client):
    seed(client, 9997)
    def reserve(i):
        try:
            client.store.reserve_usage("google_routes", "compute_routes_pro", quota_periods(str(i), NOW))
            return True
        except RuntimeError:
            return False
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(reserve, range(12))) == 3
    assert not client.can_afford(1)


def test_failed_attempt_still_counts_in_month(client):
    seed(client, 9999)
    client.transport = lambda body: {}
    with pytest.raises(g.ValidationUnavailable):
        client.route(POINTS, NOW+timedelta(days=1))
    assert not client.can_afford(1)
    assert client.store.get_usage("google_routes", "compute_routes_pro", "month", "2030-01")["failed"] == 1


def test_relay_ignores_retired_campaign_override_and_old_counters(cfg, envelope, monkeypatch):
    monkeypatch.setenv("BRP_GOOGLE_ROUTES_RELAY_CAMPAIGN_LIMIT", "1")
    periods = quota_periods(envelope["budget_id"], datetime.now(timezone.utc))
    cfg.store.reserve_usage(relay.PROVIDER, relay.COUNTER, periods, count=9999)
    restarted = relay.RelayConfig()
    monkeypatch.setattr(restarted.store, "reserve_rate_limit", lambda *args: None)
    calls = []
    def upstream(*args):
        calls.append(1)
        return SimpleNamespace(status_code=200, json=lambda: {"routes": []})
    fake_session(monkeypatch, upstream)
    api = TestClient(relay.create_app(restarted))
    assert api.post("/compute-routes", json=envelope, headers=auth()).status_code == 200
    assert api.post("/compute-routes", json=envelope, headers=auth()).status_code == 429
    assert len(calls) == 1


def test_review_claim_stays_enforced_without_legacy_call_limit(tmp_path):
    from runtime_store_sqlite import SqliteRuntimeStore
    from test_full_measurement_review import source, create
    store = SqliteRuntimeStore(tmp_path/'reviews.sqlite')
    row = create(store, source())
    with store.connect() as conn:
        conn.execute("UPDATE route_measurement_reviews SET status='running', worker_token='owner', api_calls=500 WHERE review_id=?", (row['review_id'],))
    assert not store.reserve_route_measurement_calls(row['review_id'], 'owner', 1)
    assert not store.reserve_route_measurement_calls(row['review_id'], 'wrong-owner', 1, enforce_limit=False)
    assert store.reserve_route_measurement_calls(row['review_id'], 'owner', 1, enforce_limit=False)
    with store.connect() as conn:
        conn.execute("UPDATE route_measurement_reviews SET status='paused' WHERE review_id=?", (row['review_id'],))
    assert not store.reserve_route_measurement_calls(row['review_id'], 'owner', 1, enforce_limit=False)
