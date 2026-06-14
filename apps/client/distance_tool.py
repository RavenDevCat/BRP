from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
from typing import Any

import pandas as pd
import requests

import client_runtime as runtime


OSRM_USE_BUILTIN_DEFAULTS = os.environ.get("OSRM_USE_BUILTIN_DEFAULTS", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
OSRM_BASE_URL = os.environ.get(
    "OSRM_BASE_URL",
    "http://127.0.0.1:5002" if OSRM_USE_BUILTIN_DEFAULTS else "",
).strip()
OSRM_LOCATION_DEFAULTS: dict[tuple[str, str], str] = {
    ("CHINA", "SHANGHAI"): "http://127.0.0.1:5002",
    ("CHINA", "BEIJING"): "http://127.0.0.1:5003",
    ("CHINA", "SUZHOU"): "http://127.0.0.1:5004",
    ("CHINA", "XIAN"): "http://127.0.0.1:5005",
    ("SOUTH KOREA", "SEOUL"): "http://127.0.0.1:5006",
    ("SOUTH KOREA", ""): "http://127.0.0.1:5006",
    ("THAILAND", "BANGKOK"): "http://127.0.0.1:5007",
    ("BANGKOK", ""): "http://127.0.0.1:5007",
    ("BK", ""): "http://127.0.0.1:5007",
}
OSRM_REQUEST_TIMEOUT = 30
OSRM_TABLE_DESTINATION_CHUNK_SIZE = 80


def get_excel_sheet_names(excel_path: str | Path) -> list[str]:
    workbook = pd.ExcelFile(excel_path)
    return list(workbook.sheet_names)


def read_excel_sheet(excel_path: str | Path, sheet_name: str | int = 0) -> pd.DataFrame:
    return pd.read_excel(excel_path, sheet_name=sheet_name)


def _canonical_country(country: str) -> str:
    normalized = country.strip().lower()
    if normalized in {"south korea", "republic of korea", "korea", "대한민국", "한국", "남한"}:
        return "SOUTH KOREA"
    if normalized in {"china", "中国", "中华人民共和国"}:
        return "CHINA"
    if normalized in {"bangkok", "bk", "bangkok market"}:
        return "BANGKOK"
    if normalized in {"thailand", "thai", "th", "ประเทศไทย", "ไทย"}:
        return "THAILAND"
    return country.strip().upper()


def _canonical_city(city: str) -> str:
    normalized = city.strip().lower()
    aliases = {
        "shanghai": "SHANGHAI",
        "上海": "SHANGHAI",
        "beijing": "BEIJING",
        "北京": "BEIJING",
        "suzhou": "SUZHOU",
        "苏州": "SUZHOU",
        "xian": "XIAN",
        "xi'an": "XIAN",
        "xi an": "XIAN",
        "西安": "XIAN",
        "seoul": "SEOUL",
        "서울": "SEOUL",
        "seongnam": "SEOUL",
        "seongnam-si": "SEOUL",
        "seongnam si": "SEOUL",
        "성남": "SEOUL",
        "성남시": "SEOUL",
        "bangkok": "BANGKOK",
        "bangkok metropolis": "BANGKOK",
        "krung thep": "BANGKOK",
        "krung thep maha nakhon": "BANGKOK",
        "กรุงเทพ": "BANGKOK",
        "กรุงเทพฯ": "BANGKOK",
        "กรุงเทพมหานคร": "BANGKOK",
    }
    return aliases.get(normalized, city.strip().upper())


def _normalize_osrm_key_part(value: str) -> str:
    normalized = []
    for char in value.strip().upper():
        normalized.append(char if char.isalnum() else "_")
    return "".join(normalized).strip("_")


def _country_aliases(country: str) -> list[str]:
    normalized = country.strip().lower()
    aliases = [country]
    if normalized in {"south korea", "republic of korea", "korea", "대한민국", "한국", "남한"}:
        aliases.extend(["South Korea", "Korea"])
    if normalized in {"china", "中国", "中华人民共和国"}:
        aliases.extend(["China"])
    if normalized in {"bangkok", "bk", "bangkok market", "thailand", "thai", "th", "ประเทศไทย", "ไทย"}:
        aliases.extend(["Bangkok", "BK", "Thailand", "TH"])
    deduped: list[str] = []
    for item in aliases:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def resolve_osrm_base_url(points: list[dict[str, Any]]) -> str:
    for point in points:
        country = _canonical_country(str(point.get("country", "")).strip())
        city = _canonical_city(str(point.get("city", "")).strip())
        candidate_env_keys: list[str] = []
        if country and city:
            for country_alias in _country_aliases(country):
                candidate_env_keys.append(
                    f"OSRM_BASE_URL_{_normalize_osrm_key_part(country_alias)}_{_normalize_osrm_key_part(city)}"
                )
        if country:
            for country_alias in _country_aliases(country):
                candidate_env_keys.append(f"OSRM_BASE_URL_{_normalize_osrm_key_part(country_alias)}")
        if city:
            candidate_env_keys.append(f"OSRM_BASE_URL_{_normalize_osrm_key_part(city)}")
        for env_key in candidate_env_keys:
            env_value = os.environ.get(env_key, "").strip()
            if env_value:
                return env_value
        if OSRM_USE_BUILTIN_DEFAULTS:
            builtin_url = OSRM_LOCATION_DEFAULTS.get((country, city))
            if builtin_url:
                return builtin_url
            builtin_url = OSRM_LOCATION_DEFAULTS.get((country, ""))
            if builtin_url:
                return builtin_url
    if OSRM_BASE_URL:
        return OSRM_BASE_URL
    raise RuntimeError(
        "No OSRM endpoint is configured for these points. "
        "Set OSRM_BASE_URL_<COUNTRY>_<CITY>, OSRM_BASE_URL_<COUNTRY>, or OSRM_BASE_URL "
        "in this server's local environment."
    )


def _osrm_coordinates(points: list[dict[str, Any]]) -> str:
    return ";".join(f"{float(point['lng'])},{float(point['lat'])}" for point in points)


def _request_osrm_table(
    origin_point: dict[str, Any],
    destination_points: list[dict[str, Any]],
) -> tuple[list[float | None], list[float | None]]:
    if not destination_points:
        return [], []

    all_points = [origin_point, *destination_points]
    coordinates = _osrm_coordinates(all_points)
    destination_indexes = ";".join(str(index) for index in range(1, len(all_points)))
    response = requests.get(
        f"{resolve_osrm_base_url(all_points)}/table/v1/driving/{coordinates}",
        params={
            "annotations": "duration,distance",
            "sources": "0",
            "destinations": destination_indexes,
        },
        timeout=OSRM_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok":
        raise RuntimeError(f"OSRM table request failed: {payload}")

    durations = list((payload.get("durations") or [[None]])[0])
    distances = list((payload.get("distances") or [[None]])[0])
    return durations, distances


def compute_osrm_metrics_from_origin(
    origin_point: dict[str, Any],
    destination_points: list[dict[str, Any]],
) -> list[dict[str, float | None]]:
    metrics: list[dict[str, float | None]] = []
    for start in range(0, len(destination_points), OSRM_TABLE_DESTINATION_CHUNK_SIZE):
        chunk = destination_points[start:start + OSRM_TABLE_DESTINATION_CHUNK_SIZE]
        durations, distances = _request_osrm_table(origin_point, chunk)
        for duration_s, distance_m in zip(durations, distances):
            metrics.append(
                {
                    "duration_s": float(duration_s) if duration_s is not None else None,
                    "distance_m": float(distance_m) if distance_m is not None else None,
                }
            )
    return metrics


def compute_osrm_route_leg_metrics(points: list[dict[str, Any]]) -> list[dict[str, float | None]]:
    metrics: list[dict[str, float | None]] = []
    if len(points) < 2:
        return metrics
    for origin_point, destination_point in zip(points[:-1], points[1:]):
        durations, distances = _request_osrm_table(origin_point, [destination_point])
        duration_s = durations[0] if durations else None
        distance_m = distances[0] if distances else None
        metrics.append(
            {
                "duration_s": float(duration_s) if duration_s is not None else None,
                "distance_m": float(distance_m) if distance_m is not None else None,
            }
        )
    return metrics


def _straight_leg_geometry(origin_point: dict[str, Any], destination_point: dict[str, Any]) -> list[tuple[float, float]]:
    return [
        (float(origin_point["lat"]), float(origin_point["lng"])),
        (float(destination_point["lat"]), float(destination_point["lng"])),
    ]


def _request_osrm_route_leg_detail(
    origin_point: dict[str, Any],
    destination_point: dict[str, Any],
) -> dict[str, Any]:
    points = [origin_point, destination_point]
    response = requests.get(
        f"{resolve_osrm_base_url(points)}/route/v1/driving/{_osrm_coordinates(points)}",
        params={
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        },
        timeout=OSRM_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok":
        raise RuntimeError(f"OSRM route request failed: {payload}")

    route = dict((payload.get("routes") or [{}])[0])
    coordinates = list((route.get("geometry") or {}).get("coordinates") or [])
    geometry = [
        (float(lat_lng[1]), float(lat_lng[0]))
        for lat_lng in coordinates
        if isinstance(lat_lng, (list, tuple)) and len(lat_lng) >= 2
    ]
    if len(geometry) < 2:
        geometry = _straight_leg_geometry(origin_point, destination_point)
    return {
        "duration_s": float(route["duration"]) if route.get("duration") is not None else None,
        "distance_m": float(route["distance"]) if route.get("distance") is not None else None,
        "geometry": geometry,
    }


def compute_osrm_route_leg_details(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    if len(points) < 2:
        return details
    for origin_point, destination_point in zip(points[:-1], points[1:]):
        try:
            detail = _request_osrm_route_leg_detail(origin_point, destination_point)
        except Exception:
            try:
                durations, distances = _request_osrm_table(origin_point, [destination_point])
                duration_s = durations[0] if durations else None
                distance_m = distances[0] if distances else None
            except Exception:
                duration_s = None
                distance_m = None
            detail = {
                "duration_s": float(duration_s) if duration_s is not None else None,
                "distance_m": float(distance_m) if distance_m is not None else None,
                "geometry": _straight_leg_geometry(origin_point, destination_point),
            }
        details.append(detail)
    return details


def _row_value(row: pd.Series, column_name: str | None, fallback: str = "") -> str:
    if not column_name:
        return fallback.strip()
    raw_value = row.get(column_name, "")
    return "" if pd.isna(raw_value) else str(raw_value).strip()


def build_distance_input_rows(
    source_df: pd.DataFrame,
    *,
    address_column: str,
    city_column: str | None = None,
    country_column: str | None = None,
    default_city: str = "",
    default_country: str = "",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row_index, row in source_df.iterrows():
        address = _row_value(row, address_column)
        rows.append(
            {
                "source_excel_row": row_index + 2,
                "country": _row_value(row, country_column, default_country),
                "city": _row_value(row, city_column, default_city),
                "address": address,
            }
        )
    return rows


def _find_first_matching_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    normalized_columns = {str(column).strip().lower().replace(" ", "_"): str(column) for column in columns}
    for candidate in candidates:
        normalized_candidate = candidate.strip().lower().replace(" ", "_")
        if normalized_candidate in normalized_columns:
            return normalized_columns[normalized_candidate]
    for column in columns:
        normalized_column = str(column).strip().lower().replace(" ", "_")
        if any(candidate.strip().lower().replace(" ", "_") in normalized_column for candidate in candidates):
            return str(column)
    return None


def infer_current_plan_columns(source_df: pd.DataFrame) -> dict[str, str | None]:
    columns = [str(column) for column in source_df.columns]
    return {
        "route": _find_first_matching_column(columns, ("route_id", "route", "route_name", "bus_route", "line")),
        "sequence": _find_first_matching_column(columns, ("stop_sequence", "sequence", "stop_order", "order")),
        "bus_type": _find_first_matching_column(columns, ("bus_type", "vehicle_type", "vehicle", "bus")),
        "address": _find_first_matching_column(columns, ("address", "stop_address", "pickup_address", "dropoff_address", "location")),
        "city": _find_first_matching_column(columns, ("city", "district")),
        "country": _find_first_matching_column(columns, ("country",)),
    }


def _coerce_sort_number(value: Any, fallback: float) -> float:
    try:
        if pd.isna(value):
            return fallback
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def build_current_plan_route_input_rows(
    source_df: pd.DataFrame,
    *,
    route_column: str,
    address_column: str,
    sequence_column: str | None = None,
    bus_type_column: str | None = None,
    city_column: str | None = None,
    country_column: str | None = None,
    default_city: str = "",
    default_country: str = "",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row_index, row in source_df.iterrows():
        rows.append(
            {
                "source_excel_row": row_index + 2,
                "route_id": _row_value(row, route_column) or "Unknown",
                "stop_sequence": _coerce_sort_number(_row_value(row, sequence_column), float(row_index + 1)),
                "bus_type": _row_value(row, bus_type_column),
                "country": _row_value(row, country_column, default_country),
                "city": _row_value(row, city_column, default_city),
                "address": _row_value(row, address_column),
            }
        )
    return rows


def is_electric_bus_type(bus_type: str) -> bool:
    raw_value = str(bus_type or "").strip().lower()
    normalized = re.sub(r"[_\-/]+", " ", raw_value)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False
    if any(keyword in raw_value for keyword in ("\u65b0\u80fd\u6e90", "\u7535\u52a8", "\u96fb\u52d5")):
        return True
    electric_patterns = (
        r"\be\s*bus\b",
        r"\bebus\b",
        r"\belectric(?:al)?\b",
        r"\bev\b",
        r"\bbev\b",
        r"\bbattery\b",
        r"\bzero\s+emission\b",
        r"\bnew\s+energy\b",
    )
    return any(re.search(pattern, normalized) for pattern in electric_patterns)


def geocode_records_for_distance_tool(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    resolved_rows: list[dict[str, Any]] = []
    cache_changed = False
    for record in records:
        address = str(record.get("address", "")).strip()
        if not address:
            resolved_rows.append(
                {
                    **record,
                    "status": "blank_address",
                    "warning": "Address cell is blank.",
                }
            )
            continue

        point, warning, changed = runtime.resolve_geocoded_point(
            str(record.get("country", "")).strip(),
            str(record.get("city", "")).strip(),
            address,
            [int(record.get("source_excel_row", 0) or 0)],
        )
        cache_changed = cache_changed or changed
        if point is None:
            resolved_rows.append(
                {
                    **record,
                    "status": "geocode_failed",
                    "warning": str((warning or {}).get("warning", "Could not geocode this address.")),
                    "suggestion": str((warning or {}).get("suggestion", "")).strip(),
                    "source_excel_rows": str((warning or {}).get("source_excel_rows", "")).strip(),
                }
            )
            continue

        resolved_rows.append(
            {
                **record,
                "status": "ok",
                "warning": "",
                "suggestion": "",
                "source_excel_rows": str(record.get("source_excel_row", "") or ""),
                "provider": str(point.get("provider", "")).strip(),
                "formatted_address": str(point.get("formatted_address", "")).strip(),
                "lat": float(point.get("lat", 0.0) or 0.0),
                "lng": float(point.get("lng", 0.0) or 0.0),
                "point": dict(point),
            }
        )
    if cache_changed:
        runtime.save_json_cache(runtime.GEOCODE_CACHE_PATH, runtime.GEOCODE_CACHE)
    return resolved_rows, cache_changed


def build_distance_result_dataframe(
    source_df: pd.DataFrame,
    input_rows: list[dict[str, Any]],
    geocoded_rows: list[dict[str, Any]],
    *,
    origin_record: dict[str, Any],
    origin_point: dict[str, Any],
    distance_mode: str,
) -> pd.DataFrame:
    enriched_df = source_df.copy()
    enriched_df["_source_excel_row"] = [row["source_excel_row"] for row in input_rows]

    road_metric_queue: list[dict[str, Any]] = [row for row in geocoded_rows if row.get("status") == "ok"]
    road_metrics: list[dict[str, float | None]] = []
    road_metric_by_row: dict[int, dict[str, float | None]] = {}
    if distance_mode == "road" and road_metric_queue:
        road_metrics = compute_osrm_metrics_from_origin(
            origin_point,
            [dict(row["point"]) for row in road_metric_queue],
        )
        road_metric_by_row = {
            int(row["source_excel_row"]): metric
            for row, metric in zip(road_metric_queue, road_metrics)
        }

    result_rows: list[dict[str, Any]] = []
    for geocoded_row in geocoded_rows:
        source_excel_row = int(geocoded_row["source_excel_row"])
        base_row = {
            "source_excel_row": source_excel_row,
            "origin_country": str(origin_record.get("country", "")).strip(),
            "origin_city": str(origin_record.get("city", "")).strip(),
            "origin_address": str(origin_record.get("address", "")).strip(),
            "origin_formatted_address": str(origin_point.get("formatted_address", "")).strip(),
            "input_country": str(geocoded_row.get("country", "")).strip(),
            "input_city": str(geocoded_row.get("city", "")).strip(),
            "input_address": str(geocoded_row.get("address", "")).strip(),
            "status": str(geocoded_row.get("status", "")).strip(),
            "geocode_provider": str(geocoded_row.get("provider", "")).strip(),
            "formatted_address": str(geocoded_row.get("formatted_address", "")).strip(),
            "warning": str(geocoded_row.get("warning", "")).strip(),
            "suggestion": str(geocoded_row.get("suggestion", "")).strip(),
        }

        if geocoded_row.get("status") != "ok":
            result_rows.append(
                {
                    **base_row,
                    "distance_km": None,
                    "duration_min": None,
                }
            )
            continue

        point = dict(geocoded_row["point"])
        if distance_mode == "straight_line":
            distance_km = runtime.haversine_distance_km(
                float(origin_point["lat"]),
                float(origin_point["lng"]),
                float(point["lat"]),
                float(point["lng"]),
            )
            duration_min = None
        else:
            metric = road_metric_by_row.get(source_excel_row, {})
            distance_m = metric.get("distance_m")
            duration_s = metric.get("duration_s")
            distance_km = (float(distance_m) / 1000.0) if distance_m is not None else None
            duration_min = (float(duration_s) / 60.0) if duration_s is not None else None

        result_rows.append(
            {
                **base_row,
                "distance_km": round(distance_km, 3) if distance_km is not None else None,
                "duration_min": round(duration_min, 1) if duration_min is not None else None,
            }
        )

    result_df = enriched_df.merge(
        pd.DataFrame(result_rows),
        how="left",
        left_on="_source_excel_row",
        right_on="source_excel_row",
    )
    return result_df.drop(columns=["_source_excel_row"])


def build_current_plan_route_cost_dataframe(
    input_rows: list[dict[str, Any]],
    geocoded_rows: list[dict[str, Any]],
    *,
    diesel_price_per_liter: float,
    fuel_efficiency_km_per_liter: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    geocoded_by_row = {
        int(row.get("source_excel_row", 0) or 0): dict(row)
        for row in geocoded_rows
    }
    route_groups: dict[str, list[dict[str, Any]]] = {}
    for row in input_rows:
        route_id = str(row.get("route_id", "") or "Unknown").strip() or "Unknown"
        enriched = {**row, **geocoded_by_row.get(int(row.get("source_excel_row", 0) or 0), {})}
        route_groups.setdefault(route_id, []).append(enriched)

    route_result_rows: list[dict[str, Any]] = []
    leg_result_rows: list[dict[str, Any]] = []
    for route_id, route_rows in route_groups.items():
        ordered_rows = sorted(
            route_rows,
            key=lambda item: (
                _coerce_sort_number(item.get("stop_sequence"), float("inf")),
                _coerce_sort_number(item.get("source_excel_row"), float("inf")),
            ),
        )
        route_bus_type = next((str(row.get("bus_type", "")).strip() for row in ordered_rows if str(row.get("bus_type", "")).strip()), "")
        skip_diesel_cost = is_electric_bus_type(route_bus_type)
        valid_rows = [row for row in ordered_rows if row.get("status") == "ok" and row.get("point")]
        failed_count = len(ordered_rows) - len(valid_rows)
        leg_metrics = compute_osrm_route_leg_metrics([dict(row["point"]) for row in valid_rows]) if len(valid_rows) >= 2 else []
        total_distance_m = 0.0
        total_duration_s = 0.0
        for leg_index, (from_row, to_row, metric) in enumerate(zip(valid_rows[:-1], valid_rows[1:], leg_metrics), start=1):
            distance_m = metric.get("distance_m")
            duration_s = metric.get("duration_s")
            distance_km = float(distance_m or 0.0) / 1000.0
            duration_min = float(duration_s or 0.0) / 60.0
            total_distance_m += float(distance_m or 0.0)
            total_duration_s += float(duration_s or 0.0)
            leg_result_rows.append(
                {
                    "route_id": route_id,
                    "leg": leg_index,
                    "from_stop_sequence": from_row.get("stop_sequence"),
                    "from_address": str(from_row.get("address", "")).strip(),
                    "to_stop_sequence": to_row.get("stop_sequence"),
                    "to_address": str(to_row.get("address", "")).strip(),
                    "distance_km": round(distance_km, 3),
                    "duration_min": round(duration_min, 1),
                }
            )

        total_distance_km = total_distance_m / 1000.0
        fuel_liters = None if skip_diesel_cost else (total_distance_km / fuel_efficiency_km_per_liter if fuel_efficiency_km_per_liter > 0 else 0.0)
        fuel_cost = None if fuel_liters is None else fuel_liters * diesel_price_per_liter
        route_result_rows.append(
            {
                "route_id": route_id,
                "bus_type": route_bus_type or "Unknown",
                "diesel_cost_status": "skipped_electric_bus" if skip_diesel_cost else "estimated",
                "stops_in_file": len(ordered_rows),
                "resolved_stops": len(valid_rows),
                "failed_stops": failed_count,
                "drive_legs": len(leg_metrics),
                "route_distance_km": round(total_distance_km, 3),
                "route_duration_min": round(total_duration_s / 60.0, 1),
                "estimated_diesel_liters": round(fuel_liters, 2) if fuel_liters is not None else None,
                "estimated_one_way_fuel_cost": round(fuel_cost, 2) if fuel_cost is not None else None,
            }
        )

    route_df = pd.DataFrame(route_result_rows).sort_values(
        by=["route_distance_km", "route_id"],
        ascending=[False, True],
    )
    leg_df = pd.DataFrame(leg_result_rows)
    return route_df, leg_df


def build_download_excel_bytes(results_df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        results_df.to_excel(writer, index=False, sheet_name="distance_results")
    return buffer.getvalue()
