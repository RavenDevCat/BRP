from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import io
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

import client_runtime as runtime


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
CACHE_DIR = BASE_DIR / "cache"
PLANNER_RESULT_CACHE_PATH = CACHE_DIR / "planner_result_cache.json"
ROUTE_METRICS_CACHE_PATH = CACHE_DIR / "route_metrics_cache.json"
ROUTE_GEOMETRY_CACHE_PATH = CACHE_DIR / "route_geometry_cache.json"
TRAFFIC_PROFILE_MULTIPLIERS: dict[str, float] = {
    "Off-Peak": 1.0,
    "AM Peak": 1.2,
    "PM Peak": 1.3,
}
TRAFFIC_PROFILE_OPTIONS: tuple[str, ...] = tuple(TRAFFIC_PROFILE_MULTIPLIERS.keys())
SERVICE_DIRECTION_OPTIONS: tuple[str, ...] = ("From School", "To School")
BACKEND_SERVICE_TOKEN = os.environ.get("BRP_BACKEND_SERVICE_TOKEN", "").strip()
DEV_USER_EMAIL = os.environ.get("BRP_DEV_USER_EMAIL", "local@brp.dev").strip().lower()


def _backend_auth_headers(user_email: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    normalized_email = str(user_email or DEV_USER_EMAIL).strip().lower()
    if normalized_email:
        headers["X-BRP-User-Email"] = normalized_email
    if BACKEND_SERVICE_TOKEN:
        headers["Authorization"] = f"Bearer {BACKEND_SERVICE_TOKEN}"
    return headers


@dataclass
class PlannerConfig:
    large_bus_name: str = "Large Bus"
    mid_bus_name: str = "Mid Bus"
    small_bus_name: str = "Small Bus"
    large_bus_capacity: int = 42
    mid_bus_capacity: int = 35
    small_bus_capacity: int = 19
    large_bus_max_count: int = 20
    mid_bus_max_count: int = 15
    small_bus_max_count: int = 10
    free_baseline_large_bus_ratio: float = 20.0
    free_baseline_mid_bus_ratio: float = 15.0
    free_baseline_small_bus_ratio: float = 10.0
    express_threshold_km: float = 15.0
    reserved_express_buses: int = 4
    express_skip_inner_km: float = 8.0
    max_route_duration_minutes: int = 60
    stop_service_minutes: int = 1
    subway_search_radius_m: int = 1500
    max_subway_walk_distance_m: int = 800
    nearby_cluster_radius_m: int = 500
    traffic_profile_name: str = "Off-Peak"
    service_direction: str = "From School"
    matrix_nearest_neighbors: int = 10
    matrix_candidate_radius_km: float = 15.0
    operating_cost_per_km: float = 0.0
    revenue_rules: list[dict[str, float | None]] | None = None
    original_output_name: str = "school_bus_routes.html"
    current_plan_output_name: str = "current_plan_routes.html"
    subway_output_name: str = "school_bus_routes_subway_aggregated.html"
    nearby_output_name: str = "school_bus_routes_nearby_aggregated.html"
    further_most_output_name: str = "school_bus_routes_further_most.html"
    further_most_nearby_output_name: str = "school_bus_routes_further_most_nearby.html"
    output_directory_name: str | None = None
    include_subway_aggregation_scenario: bool = True
    include_nearby_aggregation_scenario: bool = True


def ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def clear_route_caches() -> None:
    ensure_cache_dir()
    for cache_path in (
        PLANNER_RESULT_CACHE_PATH,
        ROUTE_METRICS_CACHE_PATH,
        ROUTE_GEOMETRY_CACHE_PATH,
        runtime.GEOCODE_CACHE_PATH,
        runtime.SUBWAY_CACHE_PATH,
    ):
        cache_path.write_text("{}", encoding="utf-8")


def get_excel_sheet_names(excel_path: str | Path) -> list[str]:
    workbook = pd.ExcelFile(excel_path)
    return list(workbook.sheet_names)


def read_excel_sheet(excel_path: str | Path, sheet_name: str | int = 0) -> pd.DataFrame:
    return pd.read_excel(excel_path, sheet_name=sheet_name)


def parse_passenger_count(raw_value: Any, is_first_row: bool) -> int:
    if pd.isna(raw_value):
        if is_first_row:
            return 0
        raise ValueError("blank")

    text_value = str(raw_value).strip()
    if text_value == "":
        if is_first_row:
            return 0
        raise ValueError("blank")

    normalized = text_value.replace(",", "").replace("\u3000", " ")
    if normalized.endswith(".0"):
        normalized = normalized[:-2]

    try:
        numeric_count = float(normalized)
    except ValueError as exc:
        raise ValueError(f"not numeric: {text_value}") from exc

    if not numeric_count.is_integer():
        raise ValueError(f"non-integer: {text_value}")

    passenger_count = int(numeric_count)
    if passenger_count < 0:
        raise ValueError(f"negative: {text_value}")
    return passenger_count


def read_input_records_from_excel(
    excel_path: str | Path,
    address_column: str,
    city_column: str,
    country_column: str,
    passenger_count_column: str,
    sheet_name: str | int = 0,
) -> list[dict[str, Any]]:
    df = read_excel_sheet(excel_path, sheet_name=sheet_name)
    return read_input_records_from_dataframe(
        df,
        address_column=address_column,
        city_column=city_column,
        country_column=country_column,
        passenger_count_column=passenger_count_column,
    )


def read_input_records_from_dataframe(
    df: pd.DataFrame,
    address_column: str,
    city_column: str,
    country_column: str,
    passenger_count_column: str,
) -> list[dict[str, Any]]:
    if address_column not in df.columns:
        raise ValueError(f"Column not found: {address_column}")
    if city_column not in df.columns:
        raise ValueError(f"Column not found: {city_column}")
    if country_column not in df.columns:
        raise ValueError(f"Column not found: {country_column}")
    if passenger_count_column not in df.columns:
        raise ValueError(f"Column not found: {passenger_count_column}")

    records: list[dict[str, Any]] = []
    for row_index, row in df.iterrows():
        raw_address = row[address_column]
        if pd.isna(raw_address) or not str(raw_address).strip():
            continue
        address = str(raw_address).strip()
        city = "" if pd.isna(row[city_column]) else str(row[city_column]).strip()
        country = "" if pd.isna(row[country_column]) else str(row[country_column]).strip()
        raw_count = row[passenger_count_column]
        try:
            passenger_count = parse_passenger_count(raw_count, is_first_row=(row_index == 0))
        except ValueError as exc:
            raise ValueError(
                f"Passenger count is invalid at Excel row {row_index + 2} for address '{address}'. "
                f"Selected column '{passenger_count_column}' contains value {raw_count!r}. "
                "Please use a non-negative whole number."
            ) from exc
        records.append(
            {
                "address": address,
                "city": city,
                "country": country,
                "passenger_count": passenger_count,
                "source_excel_row": row_index + 2,
            }
        )
    if not records:
        raise ValueError("No usable addresses found in the selected column.")
    return records


def build_excel_template_bytes() -> bytes:
    assignments_df = pd.DataFrame(
        {
            "route_id": ["R_FROM", "R_FROM", "R_FROM", "R_TO", "R_TO", "R_TO"],
            "stop_sequence": [1, 2, 3, 1, 2, 3],
            "bus_type": ["Large Bus", "Large Bus", "Large Bus", "Mid Bus", "Mid Bus", "Mid Bus"],
            "country": ["China", "China", "China", "China", "China", "China"],
            "city": ["Shanghai", "Shanghai", "Shanghai", "Shanghai", "Shanghai", "Shanghai"],
            "address": [
                "上海市闵行区马桥镇曙光路1935号",
                "上海市徐汇区漕溪北路398号",
                "上海市静安区南京西路1038号",
                "上海市长宁区长宁路1018号",
                "上海市静安区北京西路1701号",
                "上海市闵行区马桥镇曙光路1935号",
            ],
            "passenger_count": [0, 3, 2, 2, 3, 0],
            "note": [
                "From School example: first row is the shared school / depot, passenger_count = 0",
                "From School stop 1",
                "From School stop 2",
                "To School pickup stop 1",
                "To School pickup stop 2",
                "To School example: last row is the shared school, passenger_count = 0",
            ],
        }
    )
    fleet_df = pd.DataFrame(
        {
            "bus_type": ["Large Bus", "Mid Bus"],
            "seat_count": [45, 28],
            "vehicle_count": [1, 1],
            "note": [
                "Actual supplier seat count for Large Bus",
                "Actual supplier seat count for Mid Bus",
            ],
        }
    )
    notes_df = pd.DataFrame(
        {
            "section": ["Service Direction", "From School", "To School", "Fleet"],
            "guidance": [
                "Use the same workbook structure for both modes. Only the route row order changes.",
                "The first row of each route must be the shared school / depot row and passenger_count must be 0.",
                "The last row of each route must be the shared school row and passenger_count must be 0.",
                "Fill current_plan_fleet with the actual bus types, seat counts, and vehicle counts used today.",
            ],
        }
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        assignments_df.to_excel(writer, index=False, sheet_name="current_plan_assignments")
        fleet_df.to_excel(writer, index=False, sheet_name="current_plan_fleet")
        notes_df.to_excel(writer, index=False, sheet_name="template_notes")
    return buffer.getvalue()


def _address_signature(country: str, city: str, address: str) -> tuple[str, str, str]:
    normalized_country = str(country).strip().lower()
    normalized_city = str(city).strip().lower()
    if normalized_country in {"south korea", "korea", "republic of korea", "대한민국", "한국", "남한"}:
        if normalized_city in {"seongnam", "seongnam-si", "seongnam si", "성남", "성남시"}:
            normalized_city = "seoul"
    return (
        normalized_country,
        normalized_city,
        str(address).strip().lower(),
    )


def _normalize_service_direction(service_direction: str | None) -> str:
    return "To School" if str(service_direction or "").strip() == "To School" else "From School"


def _route_terminal_index(ordered_rows: list[dict[str, Any]], service_direction: str) -> int:
    if not ordered_rows:
        return 0
    return len(ordered_rows) - 1 if _normalize_service_direction(service_direction) == "To School" else 0


def _build_input_records_from_current_plan_rows(
    rows: list[dict[str, Any]],
    service_direction: str = "From School",
) -> list[dict[str, Any]]:
    if not rows:
        return []
    normalized_direction = _normalize_service_direction(service_direction)

    route_groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        route_groups.setdefault(str(row["route_id"]).strip(), []).append(dict(row))

    depot_row: dict[str, Any] | None = None
    planning_rows: list[dict[str, Any]] = []

    for route_id, route_rows in sorted(route_groups.items()):
        ordered_rows = sorted(route_rows, key=lambda item: int(item["stop_sequence"]))
        if not ordered_rows:
            continue
        terminal_row = ordered_rows[_route_terminal_index(ordered_rows, normalized_direction)]
        if int(terminal_row["passenger_count"]) != 0:
            raise ValueError(
                (
                    f"Route `{route_id}` must end with the school row, and that last row must have passenger_count 0."
                    if normalized_direction == "To School"
                    else f"Route `{route_id}` must start with the depot row, and that first row must have passenger_count 0."
                )
            )
        if depot_row is None:
            depot_row = terminal_row
        else:
            first_signature = _address_signature(terminal_row["country"], terminal_row["city"], terminal_row["address"])
            depot_signature = _address_signature(depot_row["country"], depot_row["city"], depot_row["address"])
            if first_signature != depot_signature:
                raise ValueError(
                    "All routes must share the same depot / school address so the system can build a single baseline."
                )

        for row_index, row in enumerate(ordered_rows):
            if row_index == _route_terminal_index(ordered_rows, normalized_direction):
                continue
            planning_rows.append(
                {
                    "country": str(row["country"]).strip(),
                    "city": str(row["city"]).strip(),
                    "address": str(row["address"]).strip(),
                    "passenger_count": int(row["passenger_count"]),
                    "source_excel_row": int(row.get("source_excel_row", 0) or 0),
                }
            )

    if depot_row is None:
        raise ValueError("No usable routes were found in `current_plan_assignments`.")

    input_records = [
        {
            "country": str(depot_row["country"]).strip(),
            "city": str(depot_row["city"]).strip(),
            "address": str(depot_row["address"]).strip(),
            "passenger_count": 0,
            "source_excel_row": int(depot_row.get("source_excel_row", 0) or 0),
        }
    ]
    input_records.extend(planning_rows)
    return input_records


def read_current_plan_from_excel(excel_path: str | Path, service_direction: str = "From School") -> dict[str, Any]:
    normalized_direction = _normalize_service_direction(service_direction)
    assignment_sheet = "current_plan_assignments"
    fleet_sheet = "current_plan_fleet"
    sheet_names = set(get_excel_sheet_names(excel_path))
    required_sheets = {assignment_sheet, fleet_sheet}
    missing_sheets = required_sheets - sheet_names
    if missing_sheets:
        raise ValueError(
            "Current plan workbook requires sheets `current_plan_assignments` and `current_plan_fleet`."
        )

    assignments_df = read_excel_sheet(excel_path, sheet_name=assignment_sheet)
    fleet_df = read_excel_sheet(excel_path, sheet_name=fleet_sheet)

    required_assignment_columns = {
        "route_id",
        "stop_sequence",
        "bus_type",
        "country",
        "city",
        "address",
        "passenger_count",
    }
    required_fleet_columns = {"bus_type", "seat_count", "vehicle_count"}
    missing_assignment_columns = required_assignment_columns - set(assignments_df.columns)
    missing_fleet_columns = required_fleet_columns - set(fleet_df.columns)
    if missing_assignment_columns:
        raise ValueError(
            f"`current_plan_assignments` is missing columns: {', '.join(sorted(missing_assignment_columns))}"
        )
    if missing_fleet_columns:
        raise ValueError(
            f"`current_plan_fleet` is missing columns: {', '.join(sorted(missing_fleet_columns))}"
        )

    fleet: list[dict[str, Any]] = []
    fleet_lookup: dict[str, dict[str, Any]] = {}
    for row_index, row in fleet_df.iterrows():
        bus_type = str(row["bus_type"]).strip()
        if not bus_type:
            raise ValueError(f"`current_plan_fleet` row {row_index + 2} has a blank bus_type.")
        if bus_type in fleet_lookup:
            raise ValueError(f"`current_plan_fleet` contains duplicate bus_type `{bus_type}`.")
        try:
            seat_count = parse_passenger_count(row["seat_count"], is_first_row=False)
        except ValueError as exc:
            raise ValueError(
                f"`current_plan_fleet` row {row_index + 2} has invalid seat_count {row['seat_count']!r}."
            ) from exc
        try:
            vehicle_count = parse_passenger_count(row["vehicle_count"], is_first_row=False)
        except ValueError as exc:
            raise ValueError(
                f"`current_plan_fleet` row {row_index + 2} has invalid vehicle_count {row['vehicle_count']!r}."
            ) from exc
        if seat_count <= 0:
            raise ValueError(f"`current_plan_fleet` row {row_index + 2} must have seat_count > 0.")
        if vehicle_count <= 0:
            raise ValueError(f"`current_plan_fleet` row {row_index + 2} must have vehicle_count > 0.")
        fleet_item = {
            "bus_type": bus_type,
            "seat_count": seat_count,
            "vehicle_count": vehicle_count,
        }
        fleet.append(fleet_item)
        fleet_lookup[bus_type] = fleet_item

    assignments: list[dict[str, Any]] = []
    assignment_rows: list[dict[str, Any]] = []
    stops: list[dict[str, Any]] = []
    route_sequences: set[tuple[str, int]] = set()
    route_bus_type_lookup: dict[str, str] = {}
    for row_index, row in assignments_df.iterrows():
        route_id = str(row["route_id"]).strip()
        bus_type = str(row["bus_type"]).strip()
        address = str(row["address"]).strip()
        if not route_id or not bus_type or not address:
            raise ValueError(
                f"`current_plan_assignments` row {row_index + 2} must include route_id, bus_type, and address."
            )
        if bus_type not in fleet_lookup:
            raise ValueError(
                f"`current_plan_assignments` row {row_index + 2} uses bus_type `{bus_type}` "
                "that is not defined in `current_plan_fleet`."
            )
        try:
            stop_sequence = int(row["stop_sequence"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"`current_plan_assignments` row {row_index + 2} has invalid stop_sequence {row['stop_sequence']!r}."
            ) from exc
        try:
            passenger_count = parse_passenger_count(row["passenger_count"], is_first_row=False)
        except ValueError as exc:
            raise ValueError(
                f"`current_plan_assignments` row {row_index + 2} has invalid passenger_count {row['passenger_count']!r}."
            ) from exc
        sequence_key = (route_id, stop_sequence)
        if sequence_key in route_sequences:
            raise ValueError(
                f"`current_plan_assignments` contains duplicate stop_sequence {stop_sequence} for route `{route_id}`."
            )
        route_sequences.add(sequence_key)
        route_bus_type = route_bus_type_lookup.setdefault(route_id, bus_type)
        if route_bus_type != bus_type:
            raise ValueError(
                f"`current_plan_assignments` mixes bus_type values inside route `{route_id}`. "
                "Each route should use exactly one bus_type."
            )
        assignment_row = {
            "route_id": route_id,
            "stop_sequence": stop_sequence,
            "bus_type": bus_type,
            "country": "" if pd.isna(row["country"]) else str(row["country"]).strip(),
            "city": "" if pd.isna(row["city"]) else str(row["city"]).strip(),
            "address": address,
            "passenger_count": passenger_count,
            "source_excel_row": row_index + 2,
        }
        assignment_rows.append(assignment_row)
        stop_id = f"{route_id}__{stop_sequence}"
        stops.append(
            {
                "stop_id": stop_id,
                "route_id": route_id,
                "stop_sequence": stop_sequence,
                "country": assignment_row["country"],
                "city": assignment_row["city"],
                "address": assignment_row["address"],
                "passenger_count": passenger_count,
                "is_depot": False,
            }
        )
        assignments.append(
            {
                "route_id": route_id,
                "stop_id": stop_id,
                "stop_sequence": stop_sequence,
                "bus_type": bus_type,
            }
        )

    route_groups: dict[str, list[dict[str, Any]]] = {}
    for stop_item in stops:
        route_id = str(stop_item.get("route_id", "")).strip()
        route_groups.setdefault(route_id, []).append(stop_item)
    canonical_input_records = _build_input_records_from_current_plan_rows(assignment_rows, normalized_direction)
    canonical_depot_signature = _address_signature(
        canonical_input_records[0]["country"],
        canonical_input_records[0]["city"],
        canonical_input_records[0]["address"],
    ) if canonical_input_records else None
    for route_id, route_stops in route_groups.items():
        ordered_route_stops = sorted(route_stops, key=lambda item: int(item.get("stop_sequence", 0) or 0))
        if not ordered_route_stops:
            continue
        terminal_stop = ordered_route_stops[_route_terminal_index(ordered_route_stops, normalized_direction)]
        terminal_stop["is_depot"] = True
        if int(terminal_stop.get("passenger_count", 0)) != 0:
            raise ValueError(
                (
                    f"Route `{route_id}` must end with the school row, and that last row must have passenger_count 0."
                    if normalized_direction == "To School"
                    else f"Route `{route_id}` must start with the depot row, and that first row must have passenger_count 0."
                )
            )
        if canonical_depot_signature is not None and _address_signature(
            terminal_stop["country"],
            terminal_stop["city"],
            terminal_stop["address"],
        ) != canonical_depot_signature:
            raise ValueError(
                "All routes must share the same depot / school address so the system can build a single baseline."
            )

    route_ids = sorted({item["route_id"] for item in assignments})
    routes_per_bus_type: dict[str, int] = {}
    for route_id in route_ids:
        bus_type = route_bus_type_lookup[route_id]
        routes_per_bus_type[bus_type] = routes_per_bus_type.get(bus_type, 0) + 1
    for bus_type, route_count in sorted(routes_per_bus_type.items()):
        available_vehicle_count = int(fleet_lookup[bus_type]["vehicle_count"])
        if route_count > available_vehicle_count:
            raise ValueError(
                f"`current_plan_fleet` declares only {available_vehicle_count} `{bus_type}` vehicle(s), "
                f"but assignments use {route_count} route(s) with that type."
            )

    bus_types = sorted({item["bus_type"] for item in assignments})
    return {
        "stops": stops,
        "assignments": assignments,
        "fleet": fleet,
        "input_records": canonical_input_records,
        "summary": {
            "stop_count": len(stops),
            "planning_stop_count": len(canonical_input_records),
            "assignment_count": len(assignments),
            "route_count": len(route_ids),
            "service_direction": normalized_direction,
            "bus_types": bus_types,
            "route_ids": route_ids,
            "fleet_count": len(fleet),
            "fleet_summary": [
                f"{item['bus_type']}: {item['seat_count']} seats x {item['vehicle_count']}"
                for item in fleet
            ],
        },
        "service_direction": normalized_direction,
    }


def summarize_structured_results(results: dict[str, Any], uploaded_address_count: int) -> dict[str, Any]:
    original = results.get("original", {})
    subway = results.get("subway", {})
    nearby = results.get("nearby", {})

    original_valid_stops = int(original.get("stop_count", uploaded_address_count))
    subway_valid_stops = int(subway.get("stop_count", original_valid_stops))
    nearby_valid_stops = int(nearby.get("stop_count", original_valid_stops))

    original_vehicle_count = int(original.get("bus_count", 0))
    subway_vehicle_count = int(subway.get("bus_count", 0))
    nearby_vehicle_count = int(nearby.get("bus_count", 0))

    original_non_depot = max(0, original_valid_stops - 1)
    subway_non_depot = max(0, subway_valid_stops - 1)
    nearby_non_depot = max(0, nearby_valid_stops - 1)

    stop_reduction = original_non_depot - subway_non_depot
    stop_reduction_pct = (stop_reduction / original_non_depot * 100.0) if original_non_depot else 0.0
    nearby_stop_reduction = original_non_depot - nearby_non_depot
    nearby_stop_reduction_pct = (nearby_stop_reduction / original_non_depot * 100.0) if original_non_depot else 0.0

    vehicle_reduction = original_vehicle_count - subway_vehicle_count
    vehicle_reduction_pct = (vehicle_reduction / original_vehicle_count * 100.0) if original_vehicle_count else 0.0
    nearby_vehicle_reduction = original_vehicle_count - nearby_vehicle_count
    nearby_vehicle_reduction_pct = (nearby_vehicle_reduction / original_vehicle_count * 100.0) if original_vehicle_count else 0.0

    return {
        "uploaded_address_count": uploaded_address_count,
        "currency_code": str(results.get("currency_code", "USD")),
        "original_uploaded_stops": uploaded_address_count,
        "original_valid_stops": original_valid_stops,
        "subway_valid_stops": subway_valid_stops,
        "nearby_valid_stops": nearby_valid_stops,
        "original_vehicle_count": original_vehicle_count,
        "subway_vehicle_count": subway_vehicle_count,
        "nearby_vehicle_count": nearby_vehicle_count,
        "original_bus_mix": dict(original.get("bus_mix", {})),
        "subway_bus_mix": dict(subway.get("bus_mix", {})),
        "nearby_bus_mix": dict(nearby.get("bus_mix", {})),
        "original_total_operating_cost": float(original.get("total_operating_cost", 0.0)),
        "subway_total_operating_cost": float(subway.get("total_operating_cost", 0.0)),
        "nearby_total_operating_cost": float(nearby.get("total_operating_cost", 0.0)),
        "original_total_chargeable_revenue": float(original.get("total_chargeable_revenue", 0.0)),
        "subway_total_chargeable_revenue": float(subway.get("total_chargeable_revenue", 0.0)),
        "nearby_total_chargeable_revenue": float(nearby.get("total_chargeable_revenue", 0.0)),
        "original_total_profit_loss": float(original.get("total_profit_loss", 0.0)),
        "subway_total_profit_loss": float(subway.get("total_profit_loss", 0.0)),
        "nearby_total_profit_loss": float(nearby.get("total_profit_loss", 0.0)),
        "stop_reduction": stop_reduction,
        "stop_reduction_pct": stop_reduction_pct,
        "nearby_stop_reduction": nearby_stop_reduction,
        "nearby_stop_reduction_pct": nearby_stop_reduction_pct,
        "vehicle_reduction": vehicle_reduction,
        "vehicle_reduction_pct": vehicle_reduction_pct,
        "nearby_vehicle_reduction": nearby_vehicle_reduction,
        "nearby_vehicle_reduction_pct": nearby_vehicle_reduction_pct,
        "subway_cost_savings": 0.0,
        "nearby_cost_savings": 0.0,
        "subway_profit_improvement": 0.0,
        "nearby_profit_improvement": 0.0,
        "subway_before_stops": original_valid_stops,
        "nearby_before_stops": original_valid_stops,
    }


def summarize_current_plan_assessment(current_plan_assessment: dict[str, Any] | None) -> dict[str, Any]:
    if not current_plan_assessment:
        return {}
    return {
        "route_count": int(current_plan_assessment.get("route_count", 0)),
        "stop_count": int(current_plan_assessment.get("stop_count", 0)),
        "assignment_count": int(current_plan_assessment.get("assignment_count", 0)),
        "bus_mix": dict(current_plan_assessment.get("bus_mix", {})),
        "total_distance_km": float(current_plan_assessment.get("total_distance_m", 0.0)) / 1000.0,
        "total_duration_minutes": float(current_plan_assessment.get("total_duration_s", 0.0)) / 60.0,
        "avg_route_distance_km": float(current_plan_assessment.get("avg_route_distance_m", 0.0)) / 1000.0,
        "avg_route_duration_minutes": float(current_plan_assessment.get("avg_route_duration_s", 0.0)) / 60.0,
        "avg_load_factor_pct": float(current_plan_assessment.get("avg_load_factor", 0.0)) * 100.0,
        "low_load_route_count": int(current_plan_assessment.get("low_load_route_count", 0)),
        "overlong_route_count": int(current_plan_assessment.get("overlong_route_count", 0)),
        "route_summaries": list(current_plan_assessment.get("route_summaries", [])),
        "recommendations": list(current_plan_assessment.get("recommendations", [])),
    }


def _normalize_input_records(input_records: list[dict[str, Any]] | list[str]) -> list[dict[str, Any]]:
    normalized_records: list[dict[str, Any]] = []
    for index, item in enumerate(list(input_records)):
        if isinstance(item, str):
            normalized_records.append(
                {
                    "country": "",
                    "city": "",
                    "address": item,
                    "passenger_count": 0 if index == 0 else 1,
                    "source_excel_row": None,
                }
            )
        else:
            normalized_records.append(
                {
                    "country": str(item.get("country", "")).strip(),
                    "city": str(item.get("city", "")).strip(),
                    "address": str(item["address"]).strip(),
                    "passenger_count": int(item.get("passenger_count", 0 if index == 0 else 1)),
                    "source_excel_row": (
                        int(item.get("source_excel_row"))
                        if item.get("source_excel_row") not in (None, "", 0)
                        else None
                    ),
                }
            )
    return normalized_records


def build_excluded_stops_from_warnings(warnings: list[dict[str, str]]) -> list[dict[str, str]]:
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in warnings:
        address = str(item.get("address", "")).strip()
        if not address or address in seen:
            continue
        excluded.append(
            {
                "address": address,
                "source_excel_rows": str(item.get("source_excel_rows", "")).strip(),
                "reason": "Could not be geocoded or was not accepted as a valid input stop.",
            }
        )
        seen.add(address)
    return excluded


def build_request_output_directory(config: PlannerConfig, job_id: str | None = None) -> Path:
    if job_id:
        return OUTPUT_DIR / job_id
    if config.output_directory_name:
        return OUTPUT_DIR / config.output_directory_name
    return OUTPUT_DIR


def build_output_path_map(config: PlannerConfig, job_id: str | None = None) -> dict[str, str]:
    output_dir = build_request_output_directory(config, job_id=job_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "original": str(output_dir / config.original_output_name),
        "current_plan": str(output_dir / config.current_plan_output_name),
        "subway": str(output_dir / config.subway_output_name),
        "nearby": str(output_dir / config.nearby_output_name),
        "further_most": str(output_dir / config.further_most_output_name),
        "further_most_nearby": str(output_dir / config.further_most_nearby_output_name),
    }


def prepare_client_payload(
    input_records: list[dict[str, Any]] | list[str],
    current_plan_data: dict[str, Any] | None = None,
    config: PlannerConfig | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    normalized_records = _normalize_input_records(input_records)
    if not normalized_records:
        raise ValueError("Address list is empty.")
    config = config or PlannerConfig()

    if progress_callback is not None:
        progress_callback("[CLIENT] Preparing client-side data before backend submission.")

    started_at = time.perf_counter()
    currency_code = runtime.determine_currency_code(normalized_records)
    original_points, geocode_warnings = runtime.geocode_records(normalized_records)
    if progress_callback is not None:
        progress_callback(f"Valid stops: {len(original_points)} / {len(normalized_records)}")

    original_points = [dict(point) for point in original_points]
    for idx, point in enumerate(original_points):
        point["node_id"] = idx

    subway_points = runtime.build_subway_aggregated_points(
        original_points,
        radius_m=config.subway_search_radius_m,
        walk_distance_m=config.max_subway_walk_distance_m,
    ) if config.include_subway_aggregation_scenario else []
    nearby_points = runtime.build_nearby_aggregated_points(
        original_points,
        cluster_radius_m=config.nearby_cluster_radius_m,
    ) if config.include_nearby_aggregation_scenario else []
    return {
        "prepared_payload": {
            "input_records": normalized_records,
            "currency_code": currency_code,
            "original_points": original_points,
            "subway_points": subway_points,
            "nearby_points": nearby_points,
            "current_plan": deepcopy(current_plan_data) if current_plan_data else None,
        },
        "logs": "",
        "geocode_warnings": geocode_warnings,
        "excluded_stops": build_excluded_stops_from_warnings(geocode_warnings),
        "elapsed_seconds": time.perf_counter() - started_at,
    }


def attach_output_paths_to_structured_results(results: dict[str, Any], config: PlannerConfig) -> dict[str, Any]:
    hydrated = deepcopy(results)
    existing_path_map = hydrated.get("output_paths")
    expected_keys = ("original", "current_plan", "subway", "nearby", "further_most", "further_most_nearby")
    if isinstance(existing_path_map, dict) and all(key in existing_path_map for key in expected_keys):
        path_map = {key: str(existing_path_map[key]) for key in expected_keys}
    else:
        path_map = build_output_path_map(config, job_id=str(hydrated.get("job_id") or ""))
    for scenario_key, output_html in path_map.items():
        scenario = hydrated.setdefault(scenario_key, {})
        scenario["output_html"] = output_html
    hydrated["output_paths"] = path_map
    return hydrated


def rerender_html_from_structured_results(results: dict[str, Any], config: PlannerConfig) -> dict[str, Any]:
    hydrated = attach_output_paths_to_structured_results(results, config)
    traffic_profile_name = str(hydrated.get("traffic_profile_name") or config.traffic_profile_name or "Off-Peak")
    traffic_time_multiplier = float(hydrated.get("traffic_time_multiplier") or TRAFFIC_PROFILE_MULTIPLIERS.get(traffic_profile_name, 1.0))
    service_direction = str(hydrated.get("service_direction") or config.service_direction or "From School")
    for scenario_key in ("original", "current_plan", "subway", "nearby", "further_most", "further_most_nearby"):
        scenario = hydrated.get(scenario_key) or {}
        points = scenario.get("points")
        routes = scenario.get("routes")
        output_html = scenario.get("output_html")
        if points is not None and routes is not None and output_html:
            runtime.render_map(
                points,
                routes,
                output_html,
                traffic_profile_name=traffic_profile_name,
                traffic_time_multiplier=traffic_time_multiplier,
                annotation_route_duration_seconds=int(config.max_route_duration_minutes) * 60,
                service_direction=service_direction,
                outlying_private_access_rows=list(scenario.get("outlying_private_access_rows") or []),
            )
    return hydrated


def submit_prepared_payload_to_backend(
    prepared_payload: dict[str, Any],
    config: PlannerConfig,
    backend_base_url: str,
    user_email: str | None = None,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    compute_url = urljoin(backend_base_url.rstrip("/") + "/", "compute")
    parsed_url = urlparse(compute_url)
    session = requests.Session()
    if parsed_url.hostname in {"127.0.0.1", "localhost", "brp.example.com"}:
        session.trust_env = False
    response = session.post(
        compute_url,
        headers=_backend_auth_headers(user_email),
        json={
            "config": asdict(config),
            "prepared_payload": prepared_payload,
        },
        timeout=timeout_seconds,
    )
    if response.status_code >= 400:
        try:
            error_payload = response.json()
        except ValueError:
            response.raise_for_status()
        error_message = str(error_payload.get("error") or f"Backend returned HTTP {response.status_code}.")
        backend_traceback = str(error_payload.get("traceback") or "").strip()
        if backend_traceback:
            raise RuntimeError(f"{error_message}\n\nBackend traceback:\n{backend_traceback}")
        raise RuntimeError(error_message)
    payload = response.json()
    if not isinstance(payload, dict) or "structured_results" not in payload:
        raise RuntimeError("Backend returned an unexpected response payload.")
    return payload


def _build_backend_session(target_url: str) -> requests.Session:
    parsed_url = urlparse(target_url)
    session = requests.Session()
    if parsed_url.hostname in {"127.0.0.1", "localhost", "brp.example.com"}:
        session.trust_env = False
    return session


def _raise_backend_error(response: requests.Response) -> None:
    try:
        error_payload = response.json()
    except ValueError:
        response.raise_for_status()
    error_message = str(error_payload.get("error") or f"Backend returned HTTP {response.status_code}.")
    backend_traceback = str(error_payload.get("traceback") or "").strip()
    if backend_traceback:
        raise RuntimeError(f"{error_message}\n\nBackend traceback:\n{backend_traceback}")
    raise RuntimeError(error_message)


def submit_job_to_backend(
    prepared_payload: dict[str, Any],
    config: PlannerConfig,
    backend_base_url: str,
    metadata: dict[str, Any] | None = None,
    user_email: str | None = None,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    jobs_url = urljoin(backend_base_url.rstrip("/") + "/", "jobs")
    session = _build_backend_session(jobs_url)
    response = session.post(
        jobs_url,
        headers=_backend_auth_headers(user_email),
        json={
            "config": asdict(config),
            "prepared_payload": prepared_payload,
            "metadata": dict(metadata or {}),
        },
        timeout=timeout_seconds,
    )
    if response.status_code >= 400:
        _raise_backend_error(response)
    payload = response.json()
    if not isinstance(payload, dict) or "job_id" not in payload:
        raise RuntimeError("Backend returned an unexpected job submission payload.")
    return payload


def list_backend_jobs(
    backend_base_url: str,
    user_email: str | None = None,
    timeout_seconds: int = 30,
) -> list[dict[str, Any]]:
    jobs_url = urljoin(backend_base_url.rstrip("/") + "/", "jobs")
    session = _build_backend_session(jobs_url)
    response = session.get(jobs_url, headers=_backend_auth_headers(user_email), timeout=timeout_seconds)
    if response.status_code >= 400:
        _raise_backend_error(response)
    payload = response.json()
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise RuntimeError("Backend returned an unexpected jobs payload.")
    return jobs


def get_backend_job(
    backend_base_url: str,
    job_id: str,
    user_email: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    job_url = urljoin(backend_base_url.rstrip("/") + "/", f"jobs/{job_id}")
    session = _build_backend_session(job_url)
    response = session.get(job_url, headers=_backend_auth_headers(user_email), timeout=timeout_seconds)
    if response.status_code >= 400:
        _raise_backend_error(response)
    payload = response.json()
    if not isinstance(payload, dict) or str(payload.get("job_id", "")).strip() != str(job_id).strip():
        raise RuntimeError("Backend returned an unexpected job detail payload.")
    return payload


def cancel_backend_job(
    backend_base_url: str,
    job_id: str,
    user_email: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    cancel_url = urljoin(backend_base_url.rstrip("/") + "/", f"jobs/{job_id}/cancel")
    session = _build_backend_session(cancel_url)
    response = session.post(cancel_url, headers=_backend_auth_headers(user_email), json={}, timeout=timeout_seconds)
    if response.status_code >= 400:
        _raise_backend_error(response)
    payload = response.json()
    if not isinstance(payload, dict) or str(payload.get("job_id", "")).strip() != str(job_id).strip():
        raise RuntimeError("Backend returned an unexpected cancel payload.")
    return payload


def delete_backend_job(
    backend_base_url: str,
    job_id: str,
    user_email: str | None = None,
    timeout_seconds: int = 30,
) -> None:
    delete_url = urljoin(backend_base_url.rstrip("/") + "/", f"jobs/{job_id}")
    session = _build_backend_session(delete_url)
    response = session.delete(delete_url, headers=_backend_auth_headers(user_email), timeout=timeout_seconds)
    if response.status_code >= 400:
        _raise_backend_error(response)


def generate_backend_ai_audit(
    backend_base_url: str,
    job_id: str,
    user_email: str | None = None,
    *,
    force: bool = False,
    language: str = "English",
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    audit_url = urljoin(backend_base_url.rstrip("/") + "/", f"jobs/{job_id}/ai-audit")
    session = _build_backend_session(audit_url)
    response = session.post(
        audit_url,
        headers=_backend_auth_headers(user_email),
        json={"force": bool(force), "language": str(language or "English")},
        timeout=timeout_seconds,
    )
    if response.status_code >= 400:
        _raise_backend_error(response)
    payload = response.json()
    ai_report = payload.get("ai_audit_report") if isinstance(payload, dict) else None
    if not isinstance(ai_report, dict):
        raise RuntimeError("Backend returned an unexpected AI audit payload.")
    return ai_report


def friendly_error_message(exc: Exception) -> str:
    message = str(exc)
    if "No usable addresses found" in message:
        return "The selected address column does not contain any usable addresses."
    if "Column not found" in message:
        return "The selected Excel column could not be found."
    if "Passenger count is invalid" in message or "Passenger count cannot be negative" in message:
        return "One or more passenger-count values are invalid."
    if "404 Client Error" in message or "Connection refused" in message:
        return "The backend planner service could not be reached. Check the backend URL and tunnel."
    return message
