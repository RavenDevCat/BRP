from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import requests

import client_runtime as runtime


OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "http://127.0.0.1:5002")
OSRM_LOCATION_DEFAULTS: dict[tuple[str, str], str] = {
    ("CHINA", "SHANGHAI"): "http://127.0.0.1:5002",
    ("CHINA", "BEIJING"): "http://127.0.0.1:5003",
    ("CHINA", "SUZHOU"): "http://127.0.0.1:5004",
    ("CHINA", "XIAN"): "http://127.0.0.1:5005",
    ("SOUTH KOREA", "SEOUL"): "http://127.0.0.1:5006",
    ("SOUTH KOREA", ""): "http://127.0.0.1:5006",
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
    }
    return aliases.get(normalized, city.strip().upper())


def resolve_osrm_base_url(points: list[dict[str, Any]]) -> str:
    for point in points:
        country = _canonical_country(str(point.get("country", "")).strip())
        city = _canonical_city(str(point.get("city", "")).strip())
        builtin_url = OSRM_LOCATION_DEFAULTS.get((country, city))
        if builtin_url:
            return builtin_url
        builtin_url = OSRM_LOCATION_DEFAULTS.get((country, ""))
        if builtin_url:
            return builtin_url
    return OSRM_BASE_URL


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


def build_download_excel_bytes(results_df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        results_df.to_excel(writer, index=False, sheet_name="distance_results")
    return buffer.getvalue()
