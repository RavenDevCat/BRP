from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import api_app  # noqa: E402


def proposal(stop_index: int, route_id: str, *, base_load: int, capacity: int) -> dict:
    return {
        "type": "insert_stop",
        "new_stop": {
            "index": stop_index,
            "address": f"New {stop_index + 1}",
            "lat": 31.1 + stop_index * 0.001,
            "lng": 121.1 + stop_index * 0.001,
            "passenger_count": 1,
        },
        "route_id": route_id,
        "insert_after_order": 0,
        "insert_before_order": 1,
        "capacity_before": base_load,
        "capacity_after": base_load + 1,
        "capacity_limit": capacity,
        "base_stop_count": 1,
        "stop_count_after": 2,
        "feasible": True,
        "warnings": [],
        "delta_duration_s": 60 if route_id == "R1" else 90,
        "score": 60 if route_id == "R1" else 90,
    }


def route_stop(order: int, address: str, *, depot: bool = False) -> dict:
    return {
        "id": f"R1:{order}",
        "route_id": "R1",
        "route_index": 0,
        "order": order,
        "node_index": order,
        "address": address,
        "passenger_count": 0 if depot else 1,
        "is_depot": depot,
        "lat": 31.0 + order * 0.01,
        "lng": 121.0 + order * 0.01,
    }


def insert_action(stop_index: int, slot_index: int, position_kind: str) -> dict:
    item = proposal(stop_index, "R1", base_load=5, capacity=15)
    item.update(
        {
            "insert_slot_index": slot_index,
            "position_kind": position_kind,
            "insert_after_order": None,
            "insert_before_order": None,
        }
    )
    return item


class RouteInsertAdvisorTests(unittest.TestCase):
    def test_to_school_slots_include_first_pickup_and_keep_school_terminal(self) -> None:
        stops = [
            route_stop(0, "Stop A"),
            route_stop(1, "Stop B"),
            route_stop(2, "School", depot=True),
        ]

        slots = api_app._insert_route_slots(stops)

        self.assertEqual([item["insert_slot_index"] for item in slots], [0, 1, 2])
        self.assertEqual(
            [item["position_kind"] for item in slots],
            ["first_pickup", "between_stops", "last_pickup"],
        )
        self.assertIsNone(slots[0]["before"])
        self.assertEqual(slots[0]["after"]["address"], "Stop A")

    def test_from_school_slots_include_final_dropoff_and_keep_school_terminal(self) -> None:
        stops = [
            route_stop(0, "School", depot=True),
            route_stop(1, "Stop A"),
            route_stop(2, "Stop B"),
        ]

        slots = api_app._insert_route_slots(stops)

        self.assertEqual([item["insert_slot_index"] for item in slots], [1, 2, 3])
        self.assertEqual(
            [item["position_kind"] for item in slots],
            ["first_dropoff", "between_stops", "last_dropoff"],
        )
        self.assertEqual(slots[-1]["before"]["address"], "Stop B")
        self.assertIsNone(slots[-1]["after"])

    def test_proposal_builder_can_recommend_a_new_first_pickup(self) -> None:
        stops = [
            route_stop(0, "Stop A"),
            route_stop(1, "Stop B"),
            route_stop(2, "School", depot=True),
        ]
        new_stop = {
            "index": 0,
            "address": "New first pickup",
            "lat": 30.99,
            "lng": 120.99,
            "passenger_count": 1,
        }
        map_data = {
            "routes": [
                {
                    "id": "R1",
                    "route_index": 0,
                    "duration_s": 1800,
                    "distance_m": 20_000,
                    "bus_capacity": 15,
                    "load": 5,
                    "stop_count": 2,
                }
            ],
            "stops": stops,
        }
        selected_plan = {
            "status": "ready",
            "feasible": True,
            "actions": [],
        }

        with (
            mock.patch.object(
                api_app,
                "_insert_geocode_stops",
                return_value=([new_stop], []),
            ),
            mock.patch.object(api_app, "_refine_insert_proposals_with_osrm"),
            mock.patch.object(
                api_app,
                "_insert_build_selected_plan",
                return_value=(selected_plan, map_data),
            ),
        ):
            result = api_app._build_route_insert_proposals(
                {"job_id": "boundary-test"},
                {
                    "_map_data": map_data,
                    "new_stops": [{"address": "New first pickup"}],
                    "constraints": {
                        "country": "China",
                        "city": "Shanghai",
                    },
                },
            )

        selected = result["recommendations"][0]["selected"]
        self.assertEqual(selected["position_kind"], "first_pickup")
        self.assertEqual(selected["insert_slot_index"], 0)
        self.assertIsNone(selected["insert_after_order"])
        self.assertEqual(selected["insert_before_order"], 0)

    def test_route_sequence_applies_first_and_final_boundary_slots(self) -> None:
        to_school = [
            route_stop(0, "Stop A"),
            route_stop(1, "School", depot=True),
        ]
        first = insert_action(0, 0, "first_pickup")
        from_school = [
            route_stop(0, "School", depot=True),
            route_stop(1, "Stop A"),
        ]
        final = insert_action(1, 2, "last_dropoff")

        first_sequence = api_app._insert_route_sequence(to_school, [first])
        final_sequence = api_app._insert_route_sequence(from_school, [final])

        self.assertEqual(
            [item["address"] for item in first_sequence],
            ["New 1", "Stop A", "School"],
        )
        self.assertEqual(
            [item["address"] for item in final_sequence],
            ["School", "Stop A", "New 2"],
        )

    def test_osrm_refines_leading_and_trailing_boundary_legs(self) -> None:
        class Planner:
            OSRM_BASE_URL = "original"

            @staticmethod
            def resolve_osrm_base_url(_points: list[dict]) -> str:
                return "boundary"

            @staticmethod
            def build_osrm_full_matrix(
                _points: list[dict],
            ) -> tuple[list[list[int]], list[list[int]]]:
                return [[0, 75], [75, 0]], [[0, 900], [900, 0]]

        leading = insert_action(0, 0, "first_pickup")
        leading.update(
            {
                "new_stop": {
                    "index": 0,
                    "address": "New 1",
                    "lat": 31.0,
                    "lng": 120.99,
                },
                "insert_before_point": route_stop(0, "Stop A"),
            }
        )
        trailing = insert_action(1, 2, "last_dropoff")
        trailing.update(
            {
                "new_stop": {
                    "index": 1,
                    "address": "New 2",
                    "lat": 31.0,
                    "lng": 121.02,
                },
                "insert_after_point": route_stop(1, "Stop A"),
            }
        )

        with mock.patch.object(
            api_app.backend_service, "load_legacy_planner", return_value=Planner()
        ):
            api_app._refine_insert_proposals_with_osrm(
                [leading, trailing], country="China", city="Shanghai", limit=2
            )

        self.assertTrue(leading["refined"])
        self.assertTrue(trailing["refined"])
        self.assertEqual(leading["delta_duration_s"], 75)
        self.assertEqual(trailing["delta_distance_m"], 900)

    def test_joint_selection_avoids_combined_capacity_overflow(self) -> None:
        stops = [
            {"index": 0, "address": "New 1", "passenger_count": 1},
            {"index": 1, "address": "New 2", "passenger_count": 1},
        ]
        proposals = [
            proposal(0, "R1", base_load=10, capacity=11),
            proposal(0, "R2", base_load=5, capacity=10),
            proposal(1, "R1", base_load=10, capacity=11),
            proposal(1, "R2", base_load=5, capacity=10),
        ]

        selected = api_app._insert_select_joint_plan(proposals, stops)

        self.assertEqual([item["route_id"] for item in selected], ["R1", "R2"])
        self.assertTrue(all(item["feasible"] for item in selected))

    def test_explicit_alternate_becomes_selected_recommendation(self) -> None:
        stops = [{"index": 0, "address": "New 1", "passenger_count": 1}]
        proposals = [
            proposal(0, "R1", base_load=5, capacity=10),
            proposal(0, "R2", base_load=5, capacity=10),
        ]
        selected = api_app._insert_select_joint_plan(
            proposals,
            stops,
            [{"new_stop_index": 0, "type": "insert_stop", "route_id": "R2", "insert_after_order": 0, "insert_before_order": 1}],
        )
        recommendations = api_app._insert_build_recommendations(
            proposals, stops, selected
        )

        self.assertEqual(recommendations[0]["selected"]["route_id"], "R2")
        self.assertEqual(recommendations[0]["primary"]["route_id"], "R1")

    def test_scenario_bundle_is_bounded_and_keeps_whole_plan_choices(self) -> None:
        stops = [
            {"index": 0, "address": "New 1", "passenger_count": 1},
            {"index": 1, "address": "New 2", "passenger_count": 1},
        ]
        proposals = [
            proposal(stop_index, route_id, base_load=2, capacity=10)
            for stop_index in range(2)
            for route_id in ("R1", "R2", "R3")
        ]
        primary = api_app._insert_select_joint_plan(proposals, stops)

        scenarios = api_app._insert_scenario_selections(
            proposals, stops, primary, limit=4
        )

        self.assertGreater(len(scenarios), 1)
        self.assertLessEqual(len(scenarios), 4)
        self.assertTrue(all(len(items) == len(stops) for items in scenarios))
        self.assertEqual(
            len({api_app._insert_selected_signature(items) for items in scenarios}),
            len(scenarios),
        )

    def test_history_saves_scenario_bundle_without_workbook_bytes(self) -> None:
        result = {
            "status": "ok",
            "proposal_status": "ready",
            "scenarios": [
                {
                    "id": "recommended",
                    "selected_plan": {
                        "feasible": True,
                        "affected_route_count": 1,
                        "total_added_duration_s": 120,
                        "total_added_distance_m": 800,
                    },
                }
            ],
            "summary": {"source_label": "plan.xlsx", "new_stop_count": 1},
        }
        with mock.patch.object(
            api_app.backend_service.ROUTE_INSERT_ADVISOR_HISTORY_STORE,
            "create",
            return_value={"run_id": "history-1"},
        ) as create:
            saved = api_app.backend_service._handle_route_insert_history_create(
                {
                    "scenario": {
                        "file_name": "plan.xlsx",
                        "new_stops": "New address",
                    },
                    "route_insert_result": result,
                    "file_base64": "must-not-be-saved",
                },
                "owner@example.com",
            )

        self.assertEqual(saved["job"]["run_id"], "history-1")
        stored_payload = create.call_args.args[0]
        self.assertNotIn("file_base64", stored_payload)
        self.assertEqual(
            stored_payload["route_insert_result"]["scenarios"][0]["id"],
            "recommended",
        )

    def test_selected_map_combines_new_stops_and_keeps_original_comparison(self) -> None:
        map_data = {
            "job_id": "workbook-preview",
            "scenario_key": "current_plan",
            "scenario_name": "Current Plan",
            "routes": [
                {
                    "id": "R1",
                    "route_index": 0,
                    "bus_type_name": "18-fbus",
                    "load": 5,
                    "bus_capacity": 15,
                    "stop_count": 1,
                    "distance_m": 5000,
                    "duration_s": 600,
                    "raw_duration_s": 600,
                    "stop_service_time_s": 60,
                    "geometry": [[121.0, 31.0], [121.2, 31.2]],
                    "display_geometry": None,
                    "stop_ids": ["R1:0", "R1:1"],
                }
            ],
            "stops": [
                {
                    "id": "R1:0",
                    "route_id": "R1",
                    "route_index": 0,
                    "order": 0,
                    "node_index": 0,
                    "address": "Stop A",
                    "passenger_count": 5,
                    "is_depot": False,
                    "lat": 31.0,
                    "lng": 121.0,
                },
                {
                    "id": "R1:1",
                    "route_id": "R1",
                    "route_index": 0,
                    "order": 1,
                    "node_index": 1,
                    "address": "School",
                    "passenger_count": 0,
                    "is_depot": True,
                    "lat": 31.2,
                    "lng": 121.2,
                },
            ],
            "route_connectors": [],
            "private_links": [],
            "summary": {},
        }
        actions = [
            proposal(0, "R1", base_load=5, capacity=15),
            proposal(1, "R1", base_load=5, capacity=15),
        ]
        actions[0]["capacity_after"] = 6
        actions[1]["capacity_after"] = 7

        def measurement(
            points: list[dict], _country: str, _cache: dict | None = None
        ) -> dict:
            inserted = len(points) - 2
            duration = 600 + inserted * 90
            distance = 5000 + inserted * 1000
            return {
                "geometry": [[point["lng"], point["lat"]] for point in points],
                "display_geometry": [[point["lng"], point["lat"]] for point in points],
                "display_geometry_source": "amap_cn",
                "display_geometry_message": "",
                "duration_s": duration,
                "distance_m": distance,
                "leg_durations_s": [duration / (len(points) - 1)] * (len(points) - 1),
                "leg_distances_m": [distance / (len(points) - 1)] * (len(points) - 1),
                "provider_verified": True,
                "warnings": [],
            }

        with mock.patch.object(api_app, "_insert_route_measurement", side_effect=measurement):
            plan, selected_map = api_app._insert_build_selected_plan(
                map_data,
                actions,
                country="China",
                constraints={},
                suggested_config={
                    "time_window_start": "06:30",
                    "time_window_end": "08:00",
                    "stop_service_minutes": 1,
                },
            )

        self.assertTrue(plan["feasible"])
        self.assertEqual(plan["inserted_stop_count"], 2)
        self.assertEqual(len(selected_map["routes"]), 1)
        self.assertEqual(selected_map["routes"][0]["stop_count"], 3)
        self.assertEqual(
            {stop.get("display_label") for stop in selected_map["stops"]},
            {None, "N1", "N2"},
        )
        self.assertEqual(selected_map["private_links"][0]["access_type"], "original_route")


if __name__ == "__main__":
    unittest.main()
