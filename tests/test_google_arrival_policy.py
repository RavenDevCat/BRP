from datetime import timedelta, datetime
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_google_final_validation import client, NOW, POINTS, response, body_points
import google_final_validation as g


def run(client, durations, estimate=600, fixed=False, rounds=4):
    bodies = []
    def transport(body):
        bodies.append(body)
        value = durations[min(len(bodies)-1, len(durations)-1)]
        if isinstance(value, Exception):
            raise value
        return response(body_points(body), value)
    client.transport = transport
    start = NOW+timedelta(days=1)
    result = g.ValidationSession(client, rounds).validate(POINTS[:2], [0, 0], start,
                                                        start+timedelta(hours=1), estimate, not fixed)
    assert result.departure in [datetime.fromisoformat(body["departureTime"]) for body in bodies]
    return result, bodies, start+timedelta(hours=1)


@pytest.mark.parametrize("early", [0, 1, 179, 180])
def test_acceptable_early_arrival_is_one_call(client, early):
    result, bodies, latest = run(client, [600-early])
    assert len(bodies) == 1 and (latest-result.arrival).total_seconds() == early

def test_over_three_minutes_targets_two_minutes_early(client):
    result, bodies, latest = run(client, [419])
    assert len(bodies) == 2 and (latest-result.arrival).total_seconds() == 120

def test_one_second_late_requires_new_prediction(client):
    result, bodies, latest = run(client, [601])
    assert len(bodies) == 2 and result.arrival == latest

def test_fixed_departure_never_shifts_even_when_late(client):
    result, bodies, latest = run(client, [3700], fixed=True)
    assert len(bodies) == 1 and result.departure == NOW+timedelta(days=1)
    assert result.arrival > latest

def test_optional_failure_retains_verified_feasible_departure(client):
    result, bodies, latest = run(client, [100, g.ValidationUnavailable("google_http_503")])
    assert len(bodies) == 2 and result.arrival <= latest
    assert result.departure == datetime.fromisoformat(bodies[0]["departureTime"])
    usage = client.store.get_usage("google_routes", "compute_routes_pro", "task", client.budget_id)
    assert (usage["attempted"], usage["succeeded"], usage["failed"]) == (2, 1, 1)

def test_budget_preserves_best_without_starting_optional_round(client):
    client.limits = (2, 500, 500)
    result, bodies, latest = run(client, [100])
    assert len(bodies) == 1 and result.arrival < latest-timedelta(minutes=3)

def test_mandatory_remaining_routes_are_reserved(client):
    client.limits = (5, 500, 500)
    client.protected_calls = 3
    _, bodies, _ = run(client, [100])
    assert len(bodies) == 1 and client.can_afford(3)

def test_oscillation_keeps_verified_best_and_caps_at_four(client):
    result, bodies, latest = run(client, [100, 900, 900, 900], rounds=20)
    assert len(bodies) == 4 and result.arrival <= latest
    assert result.departure == datetime.fromisoformat(bodies[0]["departureTime"])

def test_no_feasible_result_is_not_disguised_as_success(client):
    result, bodies, latest = run(client, [7200])
    assert len(bodies) == 2 and result.arrival > latest

def test_no_complete_round_budget_spends_nothing(client):
    client.limits = (1, 500, 500)
    start = NOW+timedelta(days=1)
    with pytest.raises(g.ValidationUnavailable, match="complete_round"):
        g.ValidationSession(client).validate(POINTS, [0, 60, 0], start, start+timedelta(hours=1), 1200, True)
    assert client.calls == 0

def test_atomic_headroom_does_not_charge_reserved_allowance(client):
    periods = [("task", "headroom", 4)]
    store = client.store
    store.reserve_usage("google_routes", "compute_routes_pro", periods, headroom=3)
    with pytest.raises(RuntimeError, match="cap"):
        store.reserve_usage("google_routes", "compute_routes_pro", periods, headroom=3)
    assert store.get_usage("google_routes", "compute_routes_pro", "task", "headroom")["attempted"] == 1

def test_cancel_is_not_swallowed_after_feasible_result(client):
    checks = 0
    def canceled():
        nonlocal checks
        checks += 1
        if client.calls:
            raise InterruptedError("cancel")
    client.check_canceled = canceled
    with pytest.raises(InterruptedError):
        run(client, [100])

def test_fresh_worker_import_order_supports_quota_headroom(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root / "apps/backend")}
    program = '''
import backend_job_runner
import google_final_validation as g
from pathlib import Path
import sys
store = g.SqliteQuotaStore(Path(sys.argv[1]))
store.reserve_usage("google_routes", "compute_routes_pro", [("task", "worker", 2)], headroom=1)
assert store.get_usage("google_routes", "compute_routes_pro", "task", "worker")["attempted"] == 1
try:
    store.reserve_usage("google_routes", "compute_routes_pro", [("task", "worker", 2)], headroom=1)
except RuntimeError:
    pass
else:
    raise AssertionError("headroom was ignored")
'''
    subprocess.run([sys.executable, "-c", program, str(tmp_path / "worker-quota.sqlite")],
                   env=env, cwd=root, check=True, timeout=60, capture_output=True, text=True)
