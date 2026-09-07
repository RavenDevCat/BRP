from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/backend"))
import measurement_reviews as review
import planner_core as core
from runtime_store_sqlite import SqliteRuntimeStore
from amap_geocode_quality import GEOCODE_QUALITY_VERSION


def source():
    points = [dict(address=f"Point {i}", formatted_address=f"Point {i}", amap_poi_name=f"Point {i}",
                   amap_poi_id=str(i), country="China", city="Shanghai", provider="amap",
                   lat=31.20 + (3 if i == 0 else i) * .002,
                   lng=121.40 + (3 if i == 0 else i) * .002,
                   geocode_level="poi", geocode_quality_version=GEOCODE_QUALITY_VERSION,
                   passenger_count=3 if i == 1 else 0, is_depot=i == 0) for i in range(3)]
    return {"job_id": "source", "status": "succeeded", "owner_email": "owner@example.test",
            "finished_at": "2026-09-01T00:00:00+00:00", "config": {"service_direction": "To School"},
            "result": {"structured_results": {"current_plan": {"points": points, "routes": [{
                "route_id": "R1", "vehicle_id": "V2", "nodes": [1, 2, 0], "load": 3,
                "time_s": 9999, "distance_m": 8888, "stop_service_time_s": 120,
                "leg_details": [{"duration_s": 120, "distance_m": 400}] * 2,
                "final_route_traffic_gate": {"provider": "amap", "target_duration_s": 3600,
                    "verified_drive_duration_s": 600, "verified_total_duration_s": 720,
                    "verified_distance_m": 900},
            }]}}}}


def request(record=None, **kwargs):
    return review.build_review_request(record or source(), ["current_plan:R1"],
                                       requested_by="admin@example.test", request_key="repeat-safe", **kwargs)


class Provider:
    def __init__(self, name, *, departure_time, api_call_limit):
        assert name == "amap" and departure_time is None
        self.state = {"api_calls": 0, "api_call_limit": api_call_limit}
        self.calls = []

    def route(self, points, **kwargs):
        self.calls.append(deepcopy(points))
        self.state["api_calls"] += len(points) - 1
        return {"evidence_version": review.EVIDENCE_VERSION, "provider": "amap",
                "status": "verified", "complete": True, "called_at": "2026-09-07T00:00:00+00:00",
                "duration_s": 240, "distance_m": 800,
                "legs": [{"duration_s": 120, "distance_m": 400}] * (len(points) - 1)}


def test_risk_pool_reads_without_mutating_or_disclosing_coordinates():
    original = source()
    snapshot = deepcopy(original)
    result = review.historical_risk_summary(original)
    assert result["routes"] == [{"route_key": "current_plan:R1", "route_id": "R1", "vehicle_id": "V2",
                                 "risk_reasons": ["missing_unified_measurement"], "input_issues": []}]
    assert original == snapshot


def test_verified_current_contract_is_not_in_risk_pool():
    original = source()
    original["result"]["structured_results"]["current_plan"]["routes"][0]["route_evidence"] = {
        "evidence_version": review.EVIDENCE_VERSION, "status": "verified", "complete": True}
    assert not review.historical_risk_summary(original)["routes"]
    with pytest.raises(ValueError, match="risk pool"):
        request(original)


@pytest.mark.parametrize("changes, reason", [
    ({"status": "scheduled", "result": None}, "source_not_finished"),
    ({"status": "failed", "result": None}, "no_saved_result"),
    ({"metadata": {"job_kind": "unsupported"}}, "unsupported_source"),
    ({"result": {"structured_results": {}}}, "no_saved_china_routes"),
])
def test_unavailable_history_is_not_a_zero_risk_claim(changes, reason):
    result = review.historical_risk_summary({**source(), **changes})
    assert result["status"] == "unavailable" and result["source_supported"] is False
    assert result["reason"] == reason


@pytest.mark.parametrize("change, reason", [
    ({"evidence_version": "old"}, "outdated_measurement_contract"),
    ({"status": "needs_review"}, "unresolved_measurement_quality"),
    ({"complete": False}, "unresolved_measurement_quality"),
    ({"issues": [{"code": "detour"}]}, "saved_quality_flags"),
])
def test_saved_quality_reasons(change, reason):
    evidence = {"evidence_version": review.EVIDENCE_VERSION, "status": "verified", "complete": True, **change}
    assert reason in review.route_risk_reasons({"route_evidence": evidence})


@pytest.mark.parametrize("keys", [[], ["missing"], ["current_plan:R1"] * 2])
def test_request_requires_exact_bounded_selection(keys):
    with pytest.raises(ValueError):
        review.build_review_request(source(), keys, requested_by="admin@example.test", request_key="x")


@pytest.mark.parametrize("limit", [0, 501, 1.5, True])
def test_request_rejects_invalid_budget(limit):
    with pytest.raises(ValueError, match="call limit"):
        request(provider_call_limit=limit)


def test_review_preserves_order_vehicle_zero_riders_and_original_result():
    original = source()
    untouched = deepcopy(original)
    payload = request(original)
    checkpoints = []
    result = review.run_measurement_review(payload, provider_factory=Provider, checkpoint=checkpoints.append)
    assert result["status"] == "complete"
    measured = result["routes"][0]
    assert [point["address"] for point in measured["points"]] == ["Point 1", "Point 2", "Point 0"]
    assert measured["points"][1]["passenger_count"] == 0
    assert measured["points"][1]["is_depot"] is False
    assert measured["vehicle_id"] == "V2"
    assert measured["after"]["total_duration_s"] == 360
    assert measured["delta"] == {"drive_duration_s": -360, "distance_m": -100, "total_duration_s": -360}
    assert measured["before"]["planned_distance_m"] == 8888
    assert result["comparison_basis"] == "fresh_traffic_not_controlled_before_after"
    assert result["student_classification_recomputed"] is False
    assert result["time_window_revalidated"] is False
    assert checkpoints[0]["status"] == "running"
    assert original == untouched


def test_missing_old_measurement_is_not_compared_with_osrm_as_live():
    original = source()
    gate = original["result"]["structured_results"]["current_plan"]["routes"][0]["final_route_traffic_gate"]
    for key in ("verified_drive_duration_s", "verified_total_duration_s", "verified_distance_m"):
        gate.pop(key)
    measured = review.run_measurement_review(request(original), provider_factory=Provider)["routes"][0]
    assert set(measured["delta"].values()) == {None}
    assert measured["before"]["planned_total_duration_s"] == 9999


def test_source_stop_missing_never_skips_node_or_reuses_previous_evidence():
    original = source()
    scenario = original["result"]["structured_results"]["current_plan"]
    scenario["routes"].append({**deepcopy(scenario["routes"][0]), "route_id": "R2", "nodes": [1, 99, 0]})
    payload = review.build_review_request(original, ["current_plan:R1", "current_plan:R2"],
                                          requested_by="admin@example.test", request_key="invalid-node")
    class StickyProvider(Provider):
        def route(self, points, **kwargs):
            value = super().route(points, **kwargs)
            self.state["last_route_evidence"] = value
            return value
    result = review.run_measurement_review(payload, provider_factory=StickyProvider)
    assert result["status"] == "partial" and result["provider_api_calls"] == 2
    assert result["routes"][1]["points"][1] == {}
    assert result["routes"][1]["route_evidence"] is None
    assert result["routes"][1]["after"]["total_duration_s"] is None


def test_review_preserves_safe_failure_evidence_not_raw_provider_exception():
    class FailedProvider(Provider):
        def route(self, points, **kwargs):
            self.state["last_route_evidence"] = {"status": "needs_review", "legs": [],
                                                 "issues": [{"code": "pickup_precision_needs_review"}]}
            raise RuntimeError("https://example.test/?key=secret-key")
    result = review.run_measurement_review(request(), provider_factory=FailedProvider)
    assert result["status"] == "partial"
    assert "secret-key" not in str(result)
    assert result["routes"][0]["route_evidence"]["issues"][0]["code"] == "pickup_precision_needs_review"
    assert result["routes"][0]["after"]["drive_duration_s"] is None


def test_canceled_before_start_does_not_construct_provider():
    def forbidden(*args, **kwargs):
        pytest.fail("Canceled review must not construct a provider")
    result = review.run_measurement_review(request(), provider_factory=forbidden, canceled=lambda: True)
    assert result["status"] == "canceled" and result["provider_api_calls"] == 0


def test_changed_request_refused_before_provider_io():
    payload = request()
    payload["routes"][0]["points"].reverse()
    with pytest.raises(ValueError, match="invalid"):
        review.run_measurement_review(payload, provider_factory=lambda *_, **__: pytest.fail("No I/O"))


def test_real_shared_provider_measurement_and_pickup_guard(monkeypatch):
    monkeypatch.setattr(core, "load_legacy_planner", lambda: SimpleNamespace(
        AMAP_KEY="synthetic-key", AMAP_ROUTING_LIMITER=None,
        amap_request_json=lambda *_, **__: pytest.fail("Unexpected outbound provider I/O")))
    calls = []
    def fetch(_planner, coords):
        from amap_driving import gcj02_to_wgs84
        calls.append(coords)
        return {"duration_s": 120, "distance_m": 700,
                "geometry": [list(reversed(gcj02_to_wgs84(*point))) for point in coords]}
    monkeypatch.setattr(core, "_amap_route_segment_stats", fetch)
    result = review.run_measurement_review(request())
    assert result["provider_api_calls"] == len(calls) == 2
    assert result["status"] == "complete"
    calls.clear()
    exhausted = review.run_measurement_review(request(provider_call_limit=1))
    assert exhausted["provider_api_calls"] == len(calls) == 1
    assert exhausted["status"] == "partial"
    original = source()
    original["result"]["structured_results"]["current_plan"]["points"][2].pop("geocode_quality_version")
    calls.clear()
    result = review.run_measurement_review(request(original))
    assert result["status"] == "partial" and not calls
    assert result["routes"][0]["route_evidence"]["issues"][0]["code"] == "pickup_precision_needs_review"


def test_direct_school_adapter_preserves_original_zero_passenger_waypoint():
    original = source()
    scenario = original["result"]["structured_results"]["current_plan"]
    original["metadata"] = {"job_kind": "direct_school_analysis"}
    original["prepared_payload"] = {"original_points": scenario["points"], "current_plan": {"stops": [
        {**scenario["points"][node], "route_id": "R1", "stop_sequence": order} for order, node in enumerate([0, 2, 1])
    ]}}
    original["result"] = {"provider": "amap", "service_direction": "From School",
                          "parameters": {"stop_service_minutes": 0},
                          "routes": [{"route_id": "R1", "provider_duration_min": 10,
                                      "total_duration_min": 10, "provider_distance_km": .9}]}
    original["prepared_payload"]["original_points"][1]["passenger_count"] = 999
    payload = request(original)
    assert payload["routes"][0]["stop_service_time_s"] == 0
    assert len(payload["routes"][0]["points"]) == 3
    assert payload["routes"][0]["service_direction"] == "From School"
    assert payload["routes"][0]["points"][0]["is_depot"] is True
    assert payload["routes"][0]["points"][2]["passenger_count"] == 3


def test_duplicate_saved_route_ids_are_not_silently_merged():
    original = source()
    routes = original["result"]["structured_results"]["current_plan"]["routes"]
    routes.append(deepcopy(routes[0]))
    with pytest.raises(ValueError, match="ambiguous"):
        request(original)


def test_store_rejects_complete_result_with_omitted_or_changed_routes(tmp_path):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(source())
    payload = request()
    row = store.create_route_measurement_review(payload)
    store.claim_route_measurement_review(row["review_id"], "worker")
    result = review.run_measurement_review(payload, provider_factory=Provider)
    for invalid in ({**result, "routes": []}, {**result, "source_job_id": "another-source"}):
        with pytest.raises(ValueError, match="does not match"):
            store.save_route_measurement_review(row["review_id"], "worker", invalid, terminal=True)
    assert store.get_route_measurement_review(row["review_id"])["status"] == "running"


def test_store_appends_results_idempotently_and_keeps_original_immutable(tmp_path):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    original = source()
    store.upsert_job(original)
    before = store.get_job("source")
    payload = request(original)
    created = store.create_route_measurement_review(payload)
    duplicate = store.create_route_measurement_review(deepcopy(payload))
    assert duplicate["review_id"] == created["review_id"]
    review_id = created["review_id"]
    assert store.claim_route_measurement_review(review_id, "worker-1")
    assert store.claim_route_measurement_review(review_id, "worker-2") is None
    result = review.run_measurement_review(payload, provider_factory=Provider)
    assert not store.save_route_measurement_review(review_id, "worker-2", result, terminal=True)
    assert store.save_route_measurement_review(review_id, "worker-1", result, terminal=True)
    assert not store.save_route_measurement_review(review_id, "worker-1", {"status": "partial"}, terminal=True)
    saved = store.get_route_measurement_review(review_id)
    assert saved["status"] == "succeeded" and saved["result"] == result
    assert store.get_job("source") == before
    assert len(store.list_route_measurement_reviews("source")) == 1
    another = review.build_review_request(original, ["current_plan:R1"],
                                          requested_by="admin@example.test", request_key="another-sample")
    assert store.create_route_measurement_review(another)["review_id"] != review_id
    assert len(store.list_route_measurement_reviews("source")) == 2
    assert store.get_route_measurement_review(review_id) == saved


def test_store_cancel_rejects_late_worker_checkpoint(tmp_path):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(source())
    row = store.create_route_measurement_review(request())
    review_id = row["review_id"]
    store.claim_route_measurement_review(review_id, "worker")
    assert store.cancel_route_measurement_review(review_id)
    assert not store.save_route_measurement_review(review_id, "worker", {"status": "running"})
    assert store.get_route_measurement_review(review_id)["status"] == "canceled"


def test_store_rejects_stale_or_changed_request_and_request_key_collision(tmp_path):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    original = source()
    store.upsert_job(original)
    payload = request(original)
    store.create_route_measurement_review(payload)
    with pytest.raises(ValueError, match="different review"):
        store.create_route_measurement_review(request(original, provider_call_limit=99))
    original["result"]["changed"] = True
    store.upsert_job(original)
    with pytest.raises(ValueError, match="Source changed"):
        store.create_route_measurement_review(payload)


def test_store_parallel_create_and_claim_have_one_winner(tmp_path):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(source())
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: store.create_route_measurement_review(request()), range(2)))
    assert rows[0]["review_id"] == rows[1]["review_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda token: store.claim_route_measurement_review(rows[0]["review_id"], token), ["a", "b"]))
    assert sum(row is not None for row in claims) == 1


def test_saved_worker_appends_actual_result_without_touching_parent(tmp_path):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(source())
    parent = store.get_job("source")
    row = store.create_route_measurement_review(request())
    finished = review.execute_saved_review(store, row["review_id"], "worker", provider_factory=Provider)
    assert finished["status"] == "succeeded"
    assert finished["result"]["routes"][0]["after"]["distance_m"] == 800
    assert store.get_job("source") == parent
    def forbidden(*_, **__):
        pytest.fail("Finished reviews must not run again")
    assert review.execute_saved_review(store, row["review_id"], "worker-2", provider_factory=forbidden) == finished


def test_saved_worker_cancel_during_measurement_does_not_publish_late_result(tmp_path):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(source())
    row = store.create_route_measurement_review(request())
    class CancelingProvider(Provider):
        def route(self, points, **kwargs):
            result = super().route(points, **kwargs)
            store.cancel_route_measurement_review(row["review_id"])
            return result
    finished = review.execute_saved_review(store, row["review_id"], "worker", provider_factory=CancelingProvider)
    assert finished["status"] == "canceled" and finished["result"] is None


def test_saved_worker_unexpected_failure_is_terminal_without_losing_checkpoint(tmp_path, monkeypatch):
    store = SqliteRuntimeStore(tmp_path / "runtime.sqlite")
    store.upsert_job(source())
    payload = request()
    row = store.create_route_measurement_review(payload)
    original_run = review.run_measurement_review
    def safely_failed(_request, *, checkpoint, **kwargs):
        result = original_run(payload, provider_factory=Provider)
        result["status"] = "running"
        checkpoint(result)
        raise RuntimeError("Sensitive exception text must not be stored")
    monkeypatch.setattr(review, "run_measurement_review", safely_failed)
    finished = review.execute_saved_review(store, row["review_id"], "worker", provider_factory=Provider)
    assert finished["status"] == "failed"
    assert finished["result"]["routes"][0]["after"]["distance_m"] == 800
    assert "Sensitive exception text" not in str(finished)
