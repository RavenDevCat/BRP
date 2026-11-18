"""Directed routing, heterogeneous split capacity, and OSRM table regressions."""
from collections import Counter
from itertools import permutations
from pathlib import Path
import random
import sys

import pytest

for path in ("apps", "apps/client", "apps/backend"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / path))
import distance_tool as table
import planner_core as planner
from fleet_refinement import _rank_splits, refine_measured_plan
from test_fleet_live_refinement import plan


def cost(order, m):
    return sum(m[a][b] for a, b in zip(order, order[1:]))


@pytest.mark.parametrize("fixed_end", [False, True])
@pytest.mark.parametrize("seed", range(7))
def test_directed_route_matches_exhaustive(fixed_end, seed):
    rng = random.Random(seed)
    m = [[rng.randrange(1, 100) for _ in range(8)] for _ in range(8)]
    stops = list(range(1, 8))
    def path(p):
        return [*p, 0] if fixed_end else [0, *p]
    result = planner._optimize_route_node_order(path(stops), m, fixed_end=fixed_end)
    assert result[-1 if fixed_end else 0] == 0
    assert sorted(result) == list(range(8))
    assert cost(result, m) == min(cost(path(p), m) for p in permutations(stops))


@pytest.mark.parametrize("n", [8, 9, 11, 12, 40])
@pytest.mark.parametrize("fixed_end", [False, True])
def test_route_with_repeats_preserves_ties_and_never_regresses(n, fixed_end):
    ids = [0, *range(1, n), 1]
    if fixed_end:
        ids.reverse()
    ties = [[1] * n for _ in range(n)]
    assert planner._optimize_route_node_order(ids, ties, fixed_end=fixed_end) == ids
    rng = random.Random(42)
    m = [[rng.randrange(200) for _ in range(n)] for _ in range(n)]
    actual = planner._optimize_route_node_order(ids, m, fixed_end=fixed_end)
    assert Counter(actual) == Counter(ids)
    assert actual[-1 if fixed_end else 0] == 0 and cost(actual, m) <= cost(ids, m)


def test_nine_stop_greedy_trap():
    m = [[100] * 10 for _ in range(10)]
    for i in range(9):
        m[i][i+1] = 2
    m[0][2] = 1
    assert cost(planner._optimize_route_node_order(list(range(10)), m), m) == 18


def certified(children):
    for r in children:
        r["route_evidence"] = {"status": "verified", "complete": True}
        r["final_route_traffic_gate"] = {"status": "passed", "verified_total_duration_s": 60}


@pytest.mark.parametrize("direction", ["from_school", "to_school"])
def test_heterogeneous_split_checks_non_midpoints(direction):
    source = plan(direction)
    for p in source["routes"][0]["ordered_points"]:
        if p["address"] != "School":
            p["student_count"] = [1, 4, 4, 1][int(p["address"])-1]
    pool = [{"capacity": 10}, {"capacity": 2}]
    res = refine_measured_plan(source, pool, certified)
    assert [r["students"] for r in res["route_rows"]] == [1, 9]
    assert [r["selected_vehicle"]["capacity"] for r in res["routes"]] == [2, 10]
    assert [n for r in res["routes"] for n in r["order"] if n] == [1, 2, 3, 4]
    assert not res["refinement"]["unresolved_routes"]
    assert len(res["refinement"]["attempts"]) == 1


def test_cuts_reserve_other_buses_and_use_directed_road_cost():
    stops = [(i, {"student_count": n, "static_school_drive_s": t, "static_next_drive_s": u})
             for i, (n,t,u) in enumerate(zip([1,4,4,1], [99,10,2,60], [5,5,200,0]), 1)]
    pool = [{"capacity": 10}, {"capacity": 2}, {"capacity": 6}]
    other = {"ordered_points": [{"student_count": 6}, {}]}
    assert _rank_splits(stops, "to_school", [other], pool) == [3, 1]
    assert _rank_splits(stops, "from_school", [other], pool) == [3, 1]
    # The same road legs, but a different school endpoint, change the cheapest cut.
    for _, p in stops:
        p["static_next_drive_s"] = 5
    assert _rank_splits(stops, "to_school", [other], pool)[0] == 3
    assert _rank_splits(stops, "from_school", [other], pool)[0] == 1
    assert not _rank_splits(stops, "to_school", [other], [{"capacity":10},{"capacity":2}])


def test_missing_child_is_not_evidence_and_retry_uses_next_cut():
    calls = []
    def measure(children):
        calls.append(1)
        certified(children)
        if len(calls) == 1:
            children[0]["final_route_traffic_gate"]["verified_total_duration_s"] = None
    res = refine_measured_plan(plan(), [{"capacity":13}] * 2, measure)
    assert [a["accepted"] for a in res["refinement"]["attempts"]] == [False, True]
    assert [a["cut"] for a in res["refinement"]["attempts"]] == [2, 1]
    assert not res["refinement"]["unresolved_routes"]


def test_exhausted_budget_is_not_a_capacity_proof():
    def no_calls(_):
        raise AssertionError("unexpected measurement")
    for caps, reason in [([10,10],"measurement_budget_exhausted"), ([10],"no_capacity_feasible_cut")]:
        res = refine_measured_plan(plan(), [{"capacity":n} for n in caps], no_calls, budget_available=lambda:False)
        assert res["routes"][0]["refinement_reason"] == reason
        assert res["refinement"]["unresolved_routes"] == ["G01"]


class Reply:
    status_code = 200
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self.payload


def raw_metric(a, b, mode):
    if (a, b) == (1, 2):
        return None if mode == "durations" else 0
    if (a, b) == (2, 1):
        return 0 if mode == "durations" else None
    return a * 1000 + b + (10 if mode == "distances" else 0)


@pytest.mark.parametrize("n,server_cap", [(124,1000), (14,5), (1,5), (0,5)])
def test_tiles_reproduce_every_directed_cell(monkeypatch, n, server_cap):
    points = [{"lng":121 + i/1000, "lat":31, "coordinate_system":"WGS84"} for i in range(n)]
    calls = []
    def get(url, params, timeout):
        ids = [round((float(v.split(",")[0])-121)*1000) for v in url.rsplit("/",1)[1].split(";")]
        rows, cols = ([ids[int(i)] for i in params[k].split(";")] for k in ("sources","destinations"))
        calls.append(len(ids))
        assert len(ids) <= 100 and len(rows)*len(cols) <= 2500
        if len(ids) > server_cap:
            return Reply({"code":"TooBig"})
        return Reply({"code":"Ok", **{k:[[raw_metric(a,b,k) for b in cols] for a in rows]
                                       for k in ("durations", "distances")}})
    monkeypatch.setattr(table.requests, "get", get)
    monkeypatch.setattr(table, "maybe_ensure_osrm_base_url", lambda _:None)
    monkeypatch.setenv("OSRM_MAX_TABLE_SIZE", "100")
    monkeypatch.setenv("BRP_OSRM_TABLE_MAX_CELLS", "2500")
    monkeypatch.setenv("BRP_OSRM_TABLE_MAX_URL_BYTES","7500")
    outputs = table.compute_osrm_matrix(points, unreachable=1e9)
    for mode, matrix in zip(("durations","distances"), outputs):
        for i in range(n):
            for j in range(n):
                expected = raw_metric(i,j,mode)
                assert matrix[i][j] == (0 if i==j else 1e9 if expected is None else expected)
    if n == 124:
        assert len(calls) == 9
    elif n < 2:
        assert not calls
    else:
        assert len(calls) > 1


@pytest.mark.parametrize("fixed_end", [True, False])
def test_stop_id_integration_uses_forward_distance_and_time(fixed_end):
    stops = ["a","b","c"]
    ids = dict(school=0, a=1, b=2, c=3)
    m = [[0,9,8,1], [1,0,1,8], [5,1,0,1], [8,6,1,0]]
    distance = [[3+i+v*9 for i,v in enumerate(row)] for row in m]
    path = [*stops,"school"] if fixed_end else ["school",*stops]
    ds, dt, order = planner._route_metric_from_stop_ids(path, ids, m, distance,
        service_direction="To School" if fixed_end else "From School")
    assert order[-1 if fixed_end else 0] == 0 and ds == cost(order, distance)
    assert dt == min(cost([*x,0] if fixed_end else [0,*x], m) for x in permutations([1,2,3]))


def test_table_routing_region_and_414_recovery(monkeypatch):
    calls = []
    points = [{"lng": float(i+1), "lat":1., "city":"Shanghai" if i%2 else "Beijing",
               "country":"China","coordinate_system":"WGS84"} for i in range(12)]
    monkeypatch.setattr(table, "maybe_ensure_osrm_base_url", lambda _:None)
    monkeypatch.setenv("OSRM_BASE_URL_CHINA_BEIJING","http://beijing.example")
    monkeypatch.setenv("OSRM_BASE_URL_CHINA_SHANGHAI","http://shanghai.example")
    monkeypatch.setenv("BRP_OSRM_TABLE_MAX_URL_BYTES","250")
    def get(url, params, timeout):
        calls.append(url)
        loc = [int(float(p.split(",")[0]))-1 for p in url.rsplit("/",1)[1].split(";")]
        rows, cols = ([loc[int(x)] for x in params[k].split(";")] for k in ("sources","destinations"))
        assert all(("beijing" in url) == (a%2==0) for a in rows)
        assert len(table.requests.Request("GET",url,params=params).prepare().url) <= 250
        if len(loc) > 5:
            reply = Reply({})
            reply.status_code = 414
            return reply
        v = [[a*100+b for b in cols] for a in rows]
        return Reply({"code":"Ok","durations":v,"distances":v})
    monkeypatch.setattr(table.requests,"get",get)
    dt, _ = table.compute_osrm_matrix(points,unreachable=1e9)
    assert len(calls)>2
    assert dt == [[0 if i==j else i*100+j for j in range(12)] for i in range(12)]


def test_partial_table_is_rejected(monkeypatch):
    monkeypatch.setattr(table.requests, "get", lambda *a, **kw:Reply({"code":"Ok","durations":[[0]],"distances":[]}))
    with pytest.raises(RuntimeError, match="incomplete table shape"):
        table._read_table("https://unused.invalid", {}, 1, 1)
