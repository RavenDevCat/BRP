import importlib
import unittest


class InteractiveMapDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = importlib.import_module("backend_service")

    def test_build_job_map_data_from_structured_result(self) -> None:
        job_record = {
            "job_id": "job-1",
            "result": {
                "service_direction": "From School",
                "structured_results": {
                    "current_plan": {
                        "points": [
                            {
                                "address": "School",
                                "plot_lat": 31.2,
                                "plot_lng": 121.4,
                                "passenger_count": 0,
                                "is_depot": True,
                            },
                            {
                                "address": "Stop A",
                                "plot_lat": 31.21,
                                "plot_lng": 121.41,
                                "passenger_count": 3,
                                "is_depot": False,
                            },
                        ],
                        "routes": [
                            {
                                "vehicle_id": 1,
                                "bus_type_name": "Large Bus",
                                "load": 3,
                                "bus_capacity": 42,
                                "comfort_capacity": 35,
                                "nodes": [0, 1],
                                "time_s": 600,
                                "distance_m": 1200,
                                "leg_details": [
                                    {
                                        "from_node": 0,
                                        "to_node": 1,
                                        "duration_s": 600,
                                        "distance_m": 1200,
                                        "geometry": [[31.2, 121.4], [31.21, 121.41]],
                                    }
                                ],
                            }
                        ],
                    }
                },
            },
        }

        payload, error = self.service._build_job_map_data(job_record, "current_plan")

        self.assertIsNone(error)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["scenario_key"], "current_plan")
        self.assertEqual(len(payload["routes"]), 1)
        self.assertEqual(len(payload["stops"]), 2)
        self.assertEqual(payload["routes"][0]["geometry"], [[121.4, 31.2], [121.41, 31.21]])
        self.assertEqual(payload["stops"][1]["address"], "Stop A")
        self.assertEqual(payload["bounds"]["min_lng"], 121.4)


if __name__ == "__main__":
    unittest.main()
