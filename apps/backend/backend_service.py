from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import asdict, fields
from datetime import datetime, timezone
import importlib
import io
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

try:
    from .planner_core import (
        PlannerConfig,
        build_excel_template_bytes,
        build_baseline_template_workbook_bytes,
        rerender_html_from_structured_results,
        run_backend_planner_with_prepared_data,
    )
    from .ai_audit import generate_ai_audit_report
except ImportError:  # pragma: no cover - supports running from apps/backend directly.
    from planner_core import (
        PlannerConfig,
        build_excel_template_bytes,
        build_baseline_template_workbook_bytes,
        rerender_html_from_structured_results,
        run_backend_planner_with_prepared_data,
    )
    from ai_audit import generate_ai_audit_report


BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent
CLIENT_DIR = BASE_DIR.parent / "client"
DEMO_DATA_DIR = CLIENT_DIR / "demodata"
DEFAULT_JOBS_DIR = REPO_ROOT / "state" / "jobs"
RAW_JOBS_DIR = os.environ.get("BRP_BACKEND_JOBS_DIR", "").strip()
JOBS_DIR = Path(RAW_JOBS_DIR or str(DEFAULT_JOBS_DIR)).expanduser()
SIDE_TOOLS_DIR = REPO_ROOT / "state" / "side_tools"
JOB_RUNNER_PATH = BASE_DIR / "backend_job_runner.py"
SERVICE_TOKEN = os.environ.get("BRP_BACKEND_SERVICE_TOKEN", "").strip()
DEV_USER_EMAIL = os.environ.get("BRP_DEV_USER_EMAIL", "local@brp.dev").strip().lower()
AUTH_PROVIDER = (
    os.environ.get("BRP_AUTH_PROVIDER")
    or os.environ.get("BRP_AUTH_MODE")
    or "cloudflare_header"
).strip().lower()
AUTH_LOGIN_URL = os.environ.get("BRP_AUTH_LOGIN_URL", "").strip()
AUTH_LOGOUT_URL = os.environ.get("BRP_AUTH_LOGOUT_URL", "").strip()
AUTH_DISPLAY_NAME = os.environ.get("BRP_AUTH_DISPLAY_NAME", "").strip()
try:
    MAX_CONCURRENT_JOBS = max(0, int(os.environ.get("BRP_MAX_CONCURRENT_JOBS", "0") or "0"))
except ValueError:
    MAX_CONCURRENT_JOBS = 0
RAW_JOB_CONCURRENCY_DIR = os.environ.get("BRP_JOB_CONCURRENCY_DIR", "").strip()
JOB_CONCURRENCY_DIR = Path(RAW_JOB_CONCURRENCY_DIR or str(REPO_ROOT / "state" / "job_concurrency")).expanduser()
try:
    JOB_QUEUE_POLL_SECONDS = max(1.0, float(os.environ.get("BRP_JOB_QUEUE_POLL_SECONDS", "5") or "5"))
except ValueError:
    JOB_QUEUE_POLL_SECONDS = 5.0
try:
    JOB_SLOT_ATTACH_STALE_SECONDS = max(
        30.0,
        float(os.environ.get("BRP_JOB_SLOT_ATTACH_STALE_SECONDS", "300") or "300"),
    )
except ValueError:
    JOB_SLOT_ATTACH_STALE_SECONDS = 300.0
MAX_WORKBOOK_UPLOAD_BYTES = int(os.environ.get("BRP_MAX_WORKBOOK_UPLOAD_BYTES", str(20 * 1024 * 1024)))
ADMIN_EMAILS = {
    item.strip().lower()
    for item in os.environ.get("BRP_ADMIN_EMAILS", "").split(",")
    if item.strip()
}
MAP_ARTIFACT_KEYS = {
    "current_plan": "current_plan",
    "original": "original",
    "free_optimization": "original",
    "subway": "subway",
    "nearby": "nearby",
    "further_most": "further_most",
    "further_most_nearby": "further_most_nearby",
}
MAP_ARTIFACT_TOP_LEVEL_KEYS = {
    "current_plan": "current_plan_html",
    "original": "original_html",
    "subway": "subway_html",
    "nearby": "nearby_html",
    "further_most": "further_most_html",
    "further_most_nearby": "further_most_nearby_html",
}
WORKBOOK_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DISTANCE_CHECKER_JOBS_PATH = CLIENT_DIR / "cache" / "distance_checker_jobs.json"
MAX_DISTANCE_CHECKER_JOBS = 80
GOOGLE_GEOCODE_USAGE_PATH = CLIENT_DIR / "cache" / "google_geocode_usage.json"
GOOGLE_GEOCODE_MONTHLY_LIMIT = 10_000


def _env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


GOOGLE_GEOCODE_USAGE_VISIBLE = _env_flag("BRP_SHOW_GOOGLE_GEOCODE_USAGE", False)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _build_planner_config(config_payload: dict[str, Any]) -> PlannerConfig:
    allowed_field_names = {field.name for field in fields(PlannerConfig)}
    filtered_payload = {
        key: value
        for key, value in dict(config_payload or {}).items()
        if key in allowed_field_names
    }
    return PlannerConfig(**filtered_payload)


def _planner_config_payload(config_payload: dict[str, Any]) -> dict[str, Any]:
    return asdict(_build_planner_config(config_payload))


def _client_core_module() -> Any:
    client_dir = str(CLIENT_DIR)
    if client_dir not in sys.path:
        sys.path.insert(0, client_dir)
    return importlib.import_module("client_core")


def _distance_tool_module() -> Any:
    client_dir = str(CLIENT_DIR)
    if client_dir not in sys.path:
        sys.path.insert(0, client_dir)
    return importlib.import_module("distance_tool")


def _client_module(module_name: str) -> Any:
    client_dir = str(CLIENT_DIR)
    if client_dir not in sys.path:
        sys.path.insert(0, client_dir)
    return importlib.import_module(module_name)


def _build_client_planner_config(client_core: Any, config_payload: dict[str, Any]) -> Any:
    allowed_field_names = {field.name for field in fields(client_core.PlannerConfig)}
    filtered_payload = {
        key: value
        for key, value in dict(config_payload or {}).items()
        if key in allowed_field_names
    }
    return client_core.PlannerConfig(**filtered_payload)


def _build_job_display_name(source_label: str, custom_name: str = "") -> str:
    default_name = Path(str(source_label or "")).stem.strip() or "Untitled job"
    normalized_custom_name = " ".join(str(custom_name or "").strip().split())
    if not normalized_custom_name:
        return default_name
    return f"{default_name} - {normalized_custom_name}"


def _decode_workbook_bytes(payload: dict[str, Any]) -> tuple[str, bytes]:
    source_label = str(payload.get("file_name") or payload.get("source_label") or "workbook.xlsx").strip()
    suffix = Path(source_label).suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise ValueError("Workbook upload must be an .xlsx or .xlsm file.")
    raw_base64 = str(payload.get("file_base64") or "").strip()
    if not raw_base64:
        raise ValueError("Missing workbook file_base64 payload.")
    if "," in raw_base64 and raw_base64.split(",", 1)[0].startswith("data:"):
        raw_base64 = raw_base64.split(",", 1)[1]
    workbook_bytes = base64.b64decode(raw_base64, validate=False)
    if not workbook_bytes:
        raise ValueError("Uploaded workbook is empty.")
    if len(workbook_bytes) > MAX_WORKBOOK_UPLOAD_BYTES:
        raise ValueError(f"Workbook upload exceeds {MAX_WORKBOOK_UPLOAD_BYTES // (1024 * 1024)} MB.")
    return source_label, workbook_bytes


def _with_temp_workbook(payload: dict[str, Any], callback: Any) -> Any:
    source_label, workbook_bytes = _decode_workbook_bytes(payload)
    suffix = Path(source_label).suffix.lower()
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(workbook_bytes)
            temp_path = temp_file.name
        return callback(source_label, temp_path)
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass


def _dataframe_records(dataframe: Any) -> list[dict[str, Any]]:
    return json.loads(dataframe.to_json(orient="records", force_ascii=False))


def _distance_checker_jobs() -> list[dict[str, Any]]:
    try:
        payload = json.loads(DISTANCE_CHECKER_JOBS_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def _save_distance_checker_job(job: dict[str, Any]) -> None:
    DISTANCE_CHECKER_JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    jobs = _distance_checker_jobs()
    jobs.insert(0, job)
    DISTANCE_CHECKER_JOBS_PATH.write_text(
        json.dumps(_json_safe(jobs[:MAX_DISTANCE_CHECKER_JOBS]), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def _handle_distance_workbook_preview(payload: dict[str, Any]) -> dict[str, Any]:
    distance_tool = _distance_tool_module()

    def read_preview(source_label: str, temp_path: str) -> dict[str, Any]:
        sheet_names = list(distance_tool.get_excel_sheet_names(temp_path))
        if not sheet_names:
            raise ValueError("Workbook has no readable sheets.")
        requested_sheet = str(payload.get("selected_sheet") or "").strip()
        selected_sheet = requested_sheet if requested_sheet in sheet_names else sheet_names[0]
        source_df = distance_tool.read_excel_sheet(temp_path, sheet_name=selected_sheet)
        columns = [str(column) for column in list(source_df.columns)]
        inferred = distance_tool.infer_current_plan_columns(source_df)
        return {
            "source_label": source_label,
            "sheet_names": sheet_names,
            "selected_sheet": selected_sheet,
            "columns": columns,
            "row_count": int(len(source_df)),
            "sample_rows": _dataframe_records(source_df.head(8)),
            "suggested_columns": {
                "address": inferred.get("address") or (columns[0] if columns else ""),
                "city": inferred.get("city") or "",
                "country": inferred.get("country") or "",
                "route": inferred.get("route") or "",
                "sequence": inferred.get("sequence") or "",
                "bus_type": inferred.get("bus_type") or "",
            },
        }

    return _with_temp_workbook(payload, read_preview)


def _handle_reference_distance_check(payload: dict[str, Any]) -> dict[str, Any]:
    distance_tool = _distance_tool_module()
    origin = dict(payload.get("origin") or {})
    origin_country = str(origin.get("country") or "").strip()
    origin_city = str(origin.get("city") or "").strip()
    origin_address = str(origin.get("address") or "").strip()
    distance_mode = str(payload.get("distance_mode") or "road").strip()
    if distance_mode not in {"road", "straight_line"}:
        raise ValueError("distance_mode must be road or straight_line.")
    if not origin_address:
        raise ValueError("Reference stop address is required.")

    def run_check(source_label: str, temp_path: str) -> dict[str, Any]:
        sheet_names = list(distance_tool.get_excel_sheet_names(temp_path))
        requested_sheet = str(payload.get("selected_sheet") or "").strip()
        selected_sheet = requested_sheet if requested_sheet in sheet_names else (sheet_names[0] if sheet_names else "")
        if not selected_sheet:
            raise ValueError("Workbook has no readable sheets.")
        source_df = distance_tool.read_excel_sheet(temp_path, sheet_name=selected_sheet)
        columns = {str(column) for column in list(source_df.columns)}
        address_column = str(payload.get("address_column") or "").strip()
        city_column = str(payload.get("city_column") or "").strip() or None
        country_column = str(payload.get("country_column") or "").strip() or None
        if address_column not in columns:
            raise ValueError("Select a valid address column.")
        if city_column and city_column not in columns:
            raise ValueError("Select a valid city column or leave it blank.")
        if country_column and country_column not in columns:
            raise ValueError("Select a valid country column or leave it blank.")

        origin_rows, _ = distance_tool.geocode_records_for_distance_tool(
            [
                {
                    "source_excel_row": 1,
                    "country": origin_country,
                    "city": origin_city,
                    "address": origin_address,
                }
            ]
        )
        origin_row = dict(origin_rows[0])
        if origin_row.get("status") != "ok":
            raise RuntimeError(str(origin_row.get("warning") or "Reference stop could not be geocoded."))

        input_rows = distance_tool.build_distance_input_rows(
            source_df,
            address_column=address_column,
            city_column=city_column,
            country_column=country_column,
            default_city=origin_city,
            default_country=origin_country,
        )
        geocoded_rows, _ = distance_tool.geocode_records_for_distance_tool(input_rows)
        results_df = distance_tool.build_distance_result_dataframe(
            source_df,
            input_rows,
            geocoded_rows,
            origin_record=origin_row,
            origin_point=dict(origin_row["point"]),
            distance_mode=distance_mode,
        )
        records = _dataframe_records(results_df)
        ok_count = sum(1 for row in records if str(row.get("status")) == "ok")
        failed_count = sum(1 for row in records if str(row.get("status")) == "geocode_failed")
        blank_count = sum(1 for row in records if str(row.get("status")) == "blank_address")
        job = {
            "job_id": uuid4().hex[:12],
            "type": "reference_distance",
            "created_at": utc_now_iso(),
            "label": f"{Path(source_label).stem} from {origin_address}",
            "metadata": {
                "source_label": source_label,
                "selected_sheet": selected_sheet,
                "origin_country": origin_country,
                "origin_city": origin_city,
                "origin_address": origin_address,
                "distance_mode": distance_mode,
                "address_column": address_column,
                "city_column": city_column or "",
                "country_column": country_column or "",
            },
            "results": records,
        }
        _save_distance_checker_job(job)
        return {
            "job": {key: value for key, value in job.items() if key != "results"},
            "summary": {
                "row_count": len(records),
                "resolved_count": ok_count,
                "failed_count": failed_count,
                "blank_count": blank_count,
                "distance_mode": distance_mode,
            },
            "results": records,
        }

    return _with_temp_workbook(payload, run_check)


def _handle_current_plan_route_cost(payload: dict[str, Any]) -> dict[str, Any]:
    distance_tool = _distance_tool_module()
    default_city = str(payload.get("default_city") or "").strip()
    default_country = str(payload.get("default_country") or "").strip()
    diesel_price_per_liter = float(payload.get("diesel_price_per_liter") or 0.0)
    fuel_efficiency_km_per_liter = float(payload.get("fuel_efficiency_km_per_liter") or 0.0)
    currency_code = str(payload.get("currency_code") or "").strip().upper()
    currency_label = str(payload.get("currency_label") or currency_code or "").strip()
    if diesel_price_per_liter < 0:
        raise ValueError("diesel_price_per_liter must be zero or greater.")
    if fuel_efficiency_km_per_liter <= 0:
        raise ValueError("fuel_efficiency_km_per_liter must be greater than zero.")

    def run_route_cost(source_label: str, temp_path: str) -> dict[str, Any]:
        sheet_names = list(distance_tool.get_excel_sheet_names(temp_path))
        requested_sheet = str(payload.get("selected_sheet") or "").strip()
        selected_sheet = requested_sheet if requested_sheet in sheet_names else (sheet_names[0] if sheet_names else "")
        if not selected_sheet:
            raise ValueError("Workbook has no readable sheets.")
        source_df = distance_tool.read_excel_sheet(temp_path, sheet_name=selected_sheet)
        columns = {str(column) for column in list(source_df.columns)}
        route_column = str(payload.get("route_column") or "").strip()
        address_column = str(payload.get("address_column") or "").strip()
        sequence_column = str(payload.get("sequence_column") or "").strip() or None
        bus_type_column = str(payload.get("bus_type_column") or "").strip() or None
        city_column = str(payload.get("city_column") or "").strip() or None
        country_column = str(payload.get("country_column") or "").strip() or None
        required_columns = {"route_column": route_column, "address_column": address_column}
        for label, column in required_columns.items():
            if column not in columns:
                raise ValueError(f"Select a valid {label.replace('_', ' ')}.")
        optional_columns = {
            "sequence_column": sequence_column,
            "bus_type_column": bus_type_column,
            "city_column": city_column,
            "country_column": country_column,
        }
        for label, column in optional_columns.items():
            if column and column not in columns:
                raise ValueError(f"Select a valid {label.replace('_', ' ')} or leave it blank.")

        input_rows = distance_tool.build_current_plan_route_input_rows(
            source_df,
            route_column=route_column,
            address_column=address_column,
            sequence_column=sequence_column,
            bus_type_column=bus_type_column,
            city_column=city_column,
            country_column=country_column,
            default_city=default_city,
            default_country=default_country,
        )
        geocoded_rows, _ = distance_tool.geocode_records_for_distance_tool(input_rows)
        route_results_df, leg_results_df = distance_tool.build_current_plan_route_cost_dataframe(
            input_rows,
            geocoded_rows,
            diesel_price_per_liter=diesel_price_per_liter,
            fuel_efficiency_km_per_liter=fuel_efficiency_km_per_liter,
        )
        route_records = _dataframe_records(route_results_df)
        leg_records = _dataframe_records(leg_results_df)
        total_distance = sum(float(row.get("route_distance_km") or 0.0) for row in route_records)
        total_cost = sum(float(row.get("estimated_one_way_fuel_cost") or 0.0) for row in route_records)
        unresolved_routes = sum(1 for row in route_records if float(row.get("failed_stops") or 0.0) > 0)
        electric_routes = sum(1 for row in route_records if str(row.get("diesel_cost_status") or "") == "skipped_electric_bus")
        job = {
            "job_id": uuid4().hex[:12],
            "type": "route_cost",
            "created_at": utc_now_iso(),
            "label": f"{Path(source_label).stem} route cost",
            "metadata": {
                "source_label": source_label,
                "selected_sheet": selected_sheet,
                "default_city": default_city,
                "default_country": default_country,
                "currency_code": currency_code,
                "currency_label": currency_label,
                "diesel_price_per_liter": diesel_price_per_liter,
                "fuel_efficiency_km_per_liter": fuel_efficiency_km_per_liter,
                "route_column": route_column,
                "address_column": address_column,
                "sequence_column": sequence_column or "",
                "bus_type_column": bus_type_column or "",
                "city_column": city_column or "",
                "country_column": country_column or "",
            },
            "route_results": route_records,
            "leg_results": leg_records,
        }
        _save_distance_checker_job(job)
        return {
            "job": {key: value for key, value in job.items() if key not in {"route_results", "leg_results"}},
            "summary": {
                "route_count": len(route_records),
                "leg_count": len(leg_records),
                "total_one_way_distance_km": round(total_distance, 3),
                "estimated_one_way_fuel_cost": round(total_cost, 2),
                "routes_with_unresolved_stops": unresolved_routes,
                "electric_routes_skipped": electric_routes,
                "currency_code": currency_code,
                "currency_label": currency_label,
            },
            "route_results": route_records,
            "leg_results": leg_records,
        }

    return _with_temp_workbook(payload, run_route_cost)


def _parse_rider_counts_payload(value: Any) -> list[int]:
    if isinstance(value, list):
        chunks = [str(item) for item in value]
    else:
        chunks = str(value or "").replace("\n", ",").split(",")
    rider_counts: list[int] = []
    for chunk in chunks:
        text = str(chunk or "").strip()
        if not text:
            continue
        try:
            rider_count = int(float(text))
        except ValueError as exc:
            raise ValueError(f"Invalid rider group value: {text!r}") from exc
        if rider_count <= 0:
            raise ValueError(f"Rider group values must be greater than zero: {text!r}")
        rider_counts.append(rider_count)
    if not rider_counts:
        raise ValueError("Enter at least one rider group or upload a demand workbook.")
    return rider_counts


def _demand_workbook_from_payload(payload: dict[str, Any]) -> tuple[Any | None, str]:
    if not str(payload.get("file_base64") or "").strip():
        return None, ""
    source_label, workbook_bytes = _decode_workbook_bytes(payload)
    demand_input = _client_module("demand_input")
    return demand_input.read_demand_workbook(io.BytesIO(workbook_bytes)), source_label


def _handle_fleet_planner_preview(payload: dict[str, Any]) -> dict[str, Any]:
    demand_input = _client_module("demand_input")
    fleet_selector = _client_module("fleet_selector")
    planning_assumptions = _client_module("planning_assumptions")
    vehicle_catalog = _client_module("vehicle_catalog")

    market = str(payload.get("market") or "KR").strip().upper()
    mode = str(payload.get("mode") or "balanced").strip()
    monitor_seats = int(payload.get("monitor_seats") or 0)
    demand_workbook, source_label = _demand_workbook_from_payload(payload)
    workbook_payload: dict[str, Any] | None = None
    if demand_workbook is not None:
        rider_counts = [int(item["student_count"]) for item in demand_workbook.riders]
        workbook_payload = {
            "source_label": source_label,
            "school": dict(demand_workbook.school),
            "summary": dict(demand_workbook.summary),
            "warnings": list(demand_workbook.warnings),
            "riders": _dataframe_records(demand_input.demand_riders_to_dataframe(demand_workbook.riders)),
        }
    else:
        rider_counts = _parse_rider_counts_payload(payload.get("rider_counts"))

    assumptions = planning_assumptions.get_planning_assumptions(
        market,
        mode=mode,
        monitor_seats=monitor_seats,
    )
    recommendations: list[dict[str, Any]] = []
    decision_details: list[dict[str, Any]] = []
    for rider_count in rider_counts:
        selection = fleet_selector.select_vehicle_for_group(
            int(rider_count),
            market=market,
            mode=mode,
            monitor_seats=monitor_seats,
            assumptions=assumptions,
        )
        selected = dict(selection.selected_vehicle or {})
        recommendations.append(
            {
                "riders": rider_count,
                "recommended_vehicle": selected.get("display_name", "No feasible vehicle"),
                "student_capacity": selected.get("student_capacity", ""),
                "load_factor": selected.get("load_factor"),
                "empty_seats": selected.get("empty_seats", ""),
                "feasible_options": len(selection.feasible_options),
                "rejected_options": len(selection.rejected_options),
            }
        )
        decision_details.append(
            {
                "riders": rider_count,
                "selected_vehicle": selected,
                "feasible_options": selection.feasible_options[:8],
                "rejected_options": selection.rejected_options[:8],
            }
        )

    mix_summary = fleet_selector.estimate_vehicle_mix_for_groups(
        rider_counts,
        market=market,
        mode=mode,
        monitor_seats=monitor_seats,
    )
    catalog = []
    for vehicle in vehicle_catalog.get_vehicle_catalog(assumptions.market, monitor_seats=assumptions.monitor_seats):
        catalog.append(
            {
                "vehicle": vehicle.get("display_name"),
                "category": vehicle.get("category"),
                "propulsion": vehicle.get("propulsion"),
                "listed_seats": vehicle.get("listed_seats"),
                "monitor_seats": vehicle.get("monitor_seats"),
                "student_capacity": vehicle.get("student_capacity"),
                "notes": vehicle.get("notes"),
            }
        )

    return {
        "summary": {
            "market": assumptions.market,
            "mode": assumptions.mode,
            "monitor_seats": assumptions.monitor_seats,
            "group_count": len(rider_counts),
            "total_riders": sum(rider_counts),
            "source": "demand_workbook" if demand_workbook is not None else "manual_rider_groups",
        },
        "assumptions": assumptions.to_dict(),
        "demand_workbook": workbook_payload,
        "recommendations": recommendations,
        "mix_summary": mix_summary,
        "decision_details": decision_details,
        "catalog": catalog,
    }


def _handle_fleet_planner_geocode(payload: dict[str, Any]) -> dict[str, Any]:
    demand_input = _client_module("demand_input")
    demand_workbook, source_label = _demand_workbook_from_payload(payload)
    if demand_workbook is None:
        raise ValueError("Upload a demand workbook before running geocode preview.")
    geocode_result = demand_input.geocode_demand_workbook(demand_workbook)
    return {
        "source_label": source_label,
        "summary": dict(geocode_result.get("summary") or {}),
        "school": dict(geocode_result.get("school") or {}),
        "demand_points": list(geocode_result.get("demand_points") or []),
        "rows": _dataframe_records(demand_input.demand_geocode_results_to_dataframe(geocode_result)),
        "map_html": demand_input.build_demand_geocode_map_html(geocode_result),
    }


def _handle_fleet_planner_clusters(payload: dict[str, Any]) -> dict[str, Any]:
    demand_clustering = _client_module("demand_clustering")
    market = str(payload.get("market") or "KR").strip().upper()
    mode = str(payload.get("mode") or "balanced").strip()
    monitor_seats = int(payload.get("monitor_seats") or 0)
    sector_count = int(payload.get("sector_count") or 8)
    if sector_count not in {4, 8, 12}:
        raise ValueError("sector_count must be 4, 8, or 12.")
    geocode_result = dict(payload.get("geocode_result") or {})
    if not geocode_result:
        raise ValueError("Run demand geocode before building clusters.")
    cluster_result = demand_clustering.build_demand_clusters(
        geocode_result,
        market=market,
        mode=mode,
        monitor_seats=monitor_seats,
        sector_count=sector_count,
    )
    return {
        "summary": dict(cluster_result.get("summary") or {}),
        "school": dict(cluster_result.get("school") or {}),
        "clusters": list(cluster_result.get("clusters") or []),
        "failed_points": list(cluster_result.get("failed_points") or []),
        "rows": _dataframe_records(demand_clustering.demand_clusters_to_dataframe(cluster_result)),
        "stop_rows": _dataframe_records(demand_clustering.cluster_points_to_dataframe(cluster_result)),
        "map_html": demand_clustering.build_demand_cluster_map_html(cluster_result),
    }


def _handle_fleet_planner_route_preview(payload: dict[str, Any]) -> dict[str, Any]:
    demand_clustering = _client_module("demand_clustering")
    demand_routing = _client_module("demand_routing")
    market = str(payload.get("market") or "KR").strip().upper()
    mode = str(payload.get("mode") or "balanced").strip()
    monitor_seats = int(payload.get("monitor_seats") or 0)
    service_direction = str(payload.get("service_direction") or "to_school").strip()
    if service_direction not in {"to_school", "from_school"}:
        raise ValueError("service_direction must be to_school or from_school.")
    max_route_duration_minutes = int(payload.get("max_route_duration_minutes") or 0) or None
    cluster_result = dict(payload.get("cluster_result") or {})
    if not cluster_result:
        raise ValueError("Build demand clusters before route preview.")

    route_preview = demand_routing.build_osrm_route_preview(
        cluster_result,
        service_direction=service_direction,
        max_route_duration_minutes=max_route_duration_minutes,
    )
    overlong_route_ids = {
        str(row.get("cluster_id", "")).strip()
        for row in list(route_preview.get("route_rows") or [])
        if max_route_duration_minutes and float(row.get("duration_min", 0.0) or 0.0) > float(max_route_duration_minutes)
    }
    if overlong_route_ids:
        refined_cluster_result = demand_clustering.split_cluster_result_by_route_limit(
            cluster_result,
            overlong_route_ids,
            market=market,
            mode=mode,
            monitor_seats=monitor_seats,
        )
        route_preview = demand_routing.build_osrm_route_preview(
            refined_cluster_result,
            service_direction=service_direction,
            max_route_duration_minutes=max_route_duration_minutes,
        )
        route_preview["refinement_note"] = (
            "One or more clusters exceeded the route-duration target and were split once by distance from school."
        )

    return _route_plan_response(route_preview, workbook_file_name="fleet_planner_generated_plan.xlsx")


def _service_direction_label(service_direction: str) -> str:
    return "To School" if str(service_direction).strip().lower() == "to_school" else "From School"


def _route_plan_response(route_preview: dict[str, Any], *, workbook_file_name: str) -> dict[str, Any]:
    demand_routing = _client_module("demand_routing")
    workbook_bytes = demand_routing.build_generated_plan_workbook_bytes(route_preview)
    return {
        "summary": dict(route_preview.get("summary") or {}),
        "school": dict(route_preview.get("school") or {}),
        "routes": list(route_preview.get("routes") or []),
        "rows": _dataframe_records(demand_routing.route_preview_to_dataframe(route_preview)),
        "stop_rows": _dataframe_records(demand_routing.route_preview_stop_detail_to_dataframe(route_preview)),
        "map_html": demand_routing.build_route_preview_map_html(route_preview),
        "refinement_note": str(route_preview.get("refinement_note") or ""),
        "workbook_file_name": workbook_file_name,
        "workbook_base64": base64.b64encode(workbook_bytes).decode("ascii"),
    }


def _handle_fleet_planner_global_plan(payload: dict[str, Any]) -> dict[str, Any]:
    demand_global_optimizer = _client_module("demand_global_optimizer")
    market = str(payload.get("market") or "KR").strip().upper()
    mode = str(payload.get("mode") or "balanced").strip()
    monitor_seats = int(payload.get("monitor_seats") or 0)
    service_direction = str(payload.get("service_direction") or "to_school").strip()
    if service_direction not in {"to_school", "from_school"}:
        raise ValueError("service_direction must be to_school or from_school.")
    geocode_result = dict(payload.get("geocode_result") or {})
    if not geocode_result:
        raise ValueError("Run demand geocode before building a global plan.")
    global_plan = demand_global_optimizer.build_global_ortools_plan(
        geocode_result,
        market=market,
        mode=mode,
        monitor_seats=monitor_seats,
        service_direction=service_direction,
    )
    return _route_plan_response(global_plan, workbook_file_name="fleet_planner_global_plan.xlsx")


def _handle_fleet_planner_history_create(payload: dict[str, Any], user_email: str) -> dict[str, Any]:
    preview_result = dict(payload.get("preview_result") or {})
    global_plan_result = dict(payload.get("global_plan_result") or {})
    if not preview_result:
        raise ValueError("Run Fleet preview before saving history.")
    if not global_plan_result:
        raise ValueError("Build an optimized plan before saving history.")

    scenario = dict(payload.get("scenario") or {})
    plan_summary = dict(global_plan_result.get("summary") or {})
    preview_summary = dict(preview_result.get("summary") or {})
    history_payload = {
        "title": str(payload.get("title") or "").strip(),
        "scenario": scenario,
        "preview_result": preview_result,
        "geocode_result": dict(payload.get("geocode_result") or {}),
        "cluster_result": dict(payload.get("cluster_result") or {}),
        "route_preview_result": dict(payload.get("route_preview_result") or {}),
        "global_plan_result": global_plan_result,
        "summary": {
            "market": scenario.get("market") or preview_summary.get("market"),
            "mode": scenario.get("mode") or preview_summary.get("mode"),
            "monitor_seats": scenario.get("monitor_seats") or preview_summary.get("monitor_seats"),
            "service_direction": scenario.get("service_direction") or plan_summary.get("service_direction"),
            "routes": plan_summary.get("route_count"),
            "students": preview_summary.get("total_riders"),
            "total_distance_km": plan_summary.get("total_distance_km"),
            "total_duration_min": plan_summary.get("total_duration_min"),
        },
    }
    return {"job": FLEET_PLANNER_HISTORY_STORE.create(history_payload, owner_email=user_email)}


def _list_demo_workbooks() -> list[dict[str, Any]]:
    if not DEMO_DATA_DIR.exists():
        return []
    demos: list[dict[str, Any]] = []
    for path in sorted(DEMO_DATA_DIR.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() not in {".xlsx", ".xlsm"}:
            continue
        if path.name.lower().startswith("demand-"):
            continue
        stat = path.stat()
        demos.append(
            {
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat(),
            }
        )
    return demos


def _resolve_demo_workbook_path(name: str) -> Path | None:
    demo_name = Path(str(name or "")).name
    if not demo_name or Path(demo_name).suffix.lower() not in {".xlsx", ".xlsm"}:
        return None
    root = DEMO_DATA_DIR.resolve()
    candidate = (root / demo_name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _read_current_plan_upload(payload: dict[str, Any]) -> tuple[Any, str, dict[str, Any]]:
    client_core = _client_core_module()
    source_label, workbook_bytes = _decode_workbook_bytes(payload)
    config_payload = dict(payload.get("config") or {})
    service_direction = str(
        config_payload.get("service_direction")
        or payload.get("service_direction")
        or "From School"
    )
    suffix = Path(source_label).suffix.lower()
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(workbook_bytes)
            temp_path = temp_file.name
        current_plan = client_core.read_current_plan_from_excel(temp_path, service_direction=service_direction)
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass
    return client_core, source_label, current_plan


def _find_subway_aggregation_block_reason(client_core: Any, records: list[dict[str, Any]]) -> str | None:
    is_likely_english_korean_address = getattr(client_core.runtime, "is_likely_english_korean_address", None)
    if not is_likely_english_korean_address:
        return None
    for item in records:
        country = str(item.get("country", "")).strip()
        address = str(item.get("address", "")).strip()
        if is_likely_english_korean_address(country, address):
            return (
                "Subway aggregation is unavailable for South Korea rows that use English-only addresses, "
                "because those stops require Google geocoding."
            )
    return None


def _suggest_planner_config_from_current_plan(
    current_plan: dict[str, Any],
    config_payload: dict[str, Any],
) -> dict[str, Any]:
    suggested = _planner_config_payload(config_payload)
    normalized_fleet: list[dict[str, Any]] = []
    for item in list(current_plan.get("fleet") or []):
        bus_type = str(item.get("bus_type", "")).strip()
        if not bus_type:
            continue
        normalized_fleet.append(
            {
                "bus_type": bus_type,
                "seat_count": int(item.get("seat_count", 0) or 0),
                "vehicle_count": int(item.get("vehicle_count", 0) or 0),
            }
        )
    normalized_fleet.sort(
        key=lambda item: (
            -int(item["seat_count"]),
            -int(item["vehicle_count"]),
            str(item["bus_type"]).lower(),
        )
    )
    slot_defaults = [
        ("large", "Large Bus", 42),
        ("mid", "Mid Bus", 35),
        ("small", "Small Bus", 19),
    ]
    for index, (slot_key, default_name, default_capacity) in enumerate(slot_defaults):
        fleet_item = normalized_fleet[index] if index < len(normalized_fleet) else {}
        slot_name = str(fleet_item.get("bus_type", default_name)).strip() or default_name
        seat_count = int(fleet_item.get("seat_count", default_capacity) or default_capacity)
        vehicle_count = int(fleet_item.get("vehicle_count", 0) or 0)
        suggested[f"{slot_key}_bus_name"] = slot_name
        suggested[f"{slot_key}_bus_capacity"] = seat_count
        suggested[f"{slot_key}_bus_max_count"] = vehicle_count
        suggested[f"free_baseline_{slot_key}_bus_ratio"] = float(vehicle_count)
    suggested["service_direction"] = str(current_plan.get("service_direction") or suggested.get("service_direction") or "From School")
    if "include_subway_aggregation_scenario" not in config_payload:
        suggested["include_subway_aggregation_scenario"] = False
    if "include_nearby_aggregation_scenario" not in config_payload:
        suggested["include_nearby_aggregation_scenario"] = False
    return suggested


def _workbook_preview_response(payload: dict[str, Any]) -> dict[str, Any]:
    client_core, source_label, current_plan = _read_current_plan_upload(payload)
    config_payload = dict(payload.get("config") or {})
    input_records = [dict(item) for item in list(current_plan.get("input_records") or [])]
    block_reason = _find_subway_aggregation_block_reason(client_core, input_records)
    suggested_config = _suggest_planner_config_from_current_plan(current_plan, config_payload)
    if block_reason:
        suggested_config["include_subway_aggregation_scenario"] = False
    return {
        "source_label": source_label,
        "selected_sheet": "current_plan_assignments",
        "job_default_name": _build_job_display_name(source_label),
        "summary": dict(current_plan.get("summary") or {}),
        "fleet": list(current_plan.get("fleet") or []),
        "input_record_count": _service_input_record_count(input_records),
        "subway_aggregation_block_reason": block_reason,
        "suggested_config": suggested_config,
    }


def _handle_workbook_preview(payload: dict[str, Any]) -> dict[str, Any]:
    return _workbook_preview_response(payload)


def _handle_workbook_submit(payload: dict[str, Any], user_email: str) -> dict[str, Any]:
    client_core, source_label, current_plan = _read_current_plan_upload(payload)
    config_payload = _planner_config_payload(dict(payload.get("config") or {}))
    input_records = [dict(item) for item in list(current_plan.get("input_records") or [])]
    block_reason = _find_subway_aggregation_block_reason(client_core, input_records)
    if block_reason:
        config_payload["include_subway_aggregation_scenario"] = False
    client_config = _build_client_planner_config(client_core, config_payload)
    client_prep = client_core.prepare_client_payload(
        input_records,
        current_plan_data=current_plan,
        config=client_config,
    )
    job_custom_name = str(payload.get("job_custom_name") or "").strip()
    job_default_name = _build_job_display_name(source_label)
    job_name = _build_job_display_name(source_label, job_custom_name)
    metadata = {
        "job_name": job_name,
        "job_default_name": job_default_name,
        "job_custom_name": job_custom_name,
        "source_label": source_label,
        "selected_sheet": "current_plan_assignments",
        "planner_config": dict(config_payload),
        "client_prep": {
            "geocode_warnings": list(client_prep.get("geocode_warnings") or []),
            "excluded_stops": list(client_prep.get("excluded_stops") or []),
            "elapsed_seconds": float(client_prep.get("elapsed_seconds", 0.0) or 0.0),
            "logs": str(client_prep.get("logs", "") or ""),
        },
    }
    summary = JOB_STORE.create_job(
        config_payload,
        dict(client_prep["prepared_payload"]),
        metadata=metadata,
        owner_email=user_email,
    )
    spawned = _spawn_job_worker(str(summary["job_id"]))
    if spawned:
        summary["worker_pid"] = spawned.get("worker_pid")
    return {
        "job": summary,
        "source_label": source_label,
        "selected_sheet": "current_plan_assignments",
        "summary": dict(current_plan.get("summary") or {}),
        "client_prep": metadata["client_prep"],
        "subway_aggregation_block_reason": block_reason,
    }


def _normalize_email(value: object) -> str:
    return str(value or "").strip().lower()


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _is_admin_email(email: str) -> bool:
    normalized_email = _normalize_email(email)
    return bool(normalized_email and normalized_email in ADMIN_EMAILS)


def _auth_display_name() -> str:
    if AUTH_DISPLAY_NAME:
        return AUTH_DISPLAY_NAME
    if AUTH_PROVIDER in {"microsoft_sso_pending", "microsoft_oidc", "saml2"}:
        return "Microsoft SSO"
    if AUTH_PROVIDER in {"cloudflare", "cloudflare_header"}:
        return "Cloudflare Access"
    if AUTH_PROVIDER == "local":
        return "Local development"
    return AUTH_PROVIDER.replace("_", " ").title()


def _auth_login_url() -> str:
    if AUTH_LOGIN_URL:
        return AUTH_LOGIN_URL
    if AUTH_PROVIDER == "microsoft_sso_pending":
        return "/api/auth/login"
    return "/"


def _auth_logout_url() -> str:
    if AUTH_LOGOUT_URL:
        return AUTH_LOGOUT_URL
    if AUTH_PROVIDER in {"cloudflare", "cloudflare_header"}:
        return "/cdn-cgi/access/logout"
    return "/"


def _auth_config_payload() -> dict[str, Any]:
    return {
        "provider": AUTH_PROVIDER,
        "display_name": _auth_display_name(),
        "login_url": _auth_login_url(),
        "logout_url": _auth_logout_url(),
        "sso_ready": AUTH_PROVIDER not in {"microsoft_sso_pending"},
        "admin_source": "local_env",
    }


def _service_input_record_count(input_records: list[dict[str, Any]]) -> int:
    if not input_records:
        return 0
    count = 0
    for item in input_records:
        try:
            passenger_count = int(item.get("passenger_count", 0) or 0)
        except Exception:
            passenger_count = 0
        if passenger_count > 0:
            count += 1
    return count


def _summarize_prepared_payload(prepared_payload: dict[str, Any]) -> dict[str, Any]:
    input_records = list(prepared_payload.get("input_records") or [])
    current_plan = dict(prepared_payload.get("current_plan") or {})
    current_plan_summary = dict(current_plan.get("summary") or {})
    return {
        "input_record_count": _service_input_record_count(input_records),
        "input_point_count": len(input_records),
        "country_samples": sorted(
            {
                str(item.get("country", "")).strip()
                for item in input_records
                if str(item.get("country", "")).strip()
            }
        )[:10],
        "city_samples": sorted(
            {
                str(item.get("city", "")).strip()
                for item in input_records
                if str(item.get("city", "")).strip()
            }
        )[:10],
        "has_current_plan": bool(current_plan),
        "current_plan_route_count": int(current_plan_summary.get("route_count", 0) or 0),
        "current_plan_assignment_count": int(current_plan_summary.get("assignment_count", 0) or 0),
        "current_plan_service_stop_count": int(current_plan_summary.get("service_stop_count", current_plan_summary.get("stop_count", 0)) or 0),
        "current_plan_scheduled_assignment_count": int(current_plan_summary.get("scheduled_assignment_count", 0) or 0),
    }


def _google_geocode_usage_month_key() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m")


def _google_geocode_usage_payload() -> dict[str, Any]:
    if not GOOGLE_GEOCODE_USAGE_VISIBLE:
        return {"enabled": False}
    month_key = _google_geocode_usage_month_key()
    try:
        payload = json.loads(GOOGLE_GEOCODE_USAGE_PATH.read_text(encoding="utf-8")) if GOOGLE_GEOCODE_USAGE_PATH.exists() else {}
    except Exception:
        payload = {}
    try:
        used = max(0, int(dict(payload).get(month_key, 0) or 0))
    except Exception:
        used = 0
    return {
        "enabled": True,
        "month_key": month_key,
        "used": used,
        "limit": GOOGLE_GEOCODE_MONTHLY_LIMIT,
        "label": f"Google geocode usage this month: {used:,} / {GOOGLE_GEOCODE_MONTHLY_LIMIT:,}",
    }


def _pid_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _seconds_since_iso(value: object) -> float | None:
    try:
        timestamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except Exception:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()


class JobConcurrencyGate:
    def __init__(self, limit: int, slot_dir: Path) -> None:
        self.limit = max(0, int(limit or 0))
        self.slot_dir = slot_dir

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def acquire(self, job_id: str) -> Path | None:
        if not self.enabled:
            return None
        self.slot_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup_stale_slots()
        for slot_number in range(1, self.limit + 1):
            slot_path = self.slot_dir / f"slot-{slot_number}"
            try:
                slot_path.mkdir()
            except FileExistsError:
                continue
            metadata = {
                "job_id": job_id,
                "acquired_at": utc_now_iso(),
                "launcher_pid": os.getpid(),
                "worker_pid": None,
            }
            self._write_metadata(slot_path, metadata)
            return slot_path
        return None

    def attach_worker(self, slot_path: Path | None, worker_pid: int) -> None:
        if not slot_path:
            return
        if not slot_path.exists():
            return
        metadata = self._read_metadata(slot_path)
        metadata["worker_pid"] = int(worker_pid)
        metadata["attached_at"] = utc_now_iso()
        try:
            self._write_metadata(slot_path, metadata)
        except FileNotFoundError:
            return

    def release(self, slot_path: str | Path | None) -> None:
        if not slot_path:
            return
        try:
            resolved_slot = Path(slot_path).resolve()
            resolved_root = self.slot_dir.resolve()
            resolved_slot.relative_to(resolved_root)
            if resolved_slot.name.startswith("slot-"):
                shutil.rmtree(resolved_slot, ignore_errors=True)
        except Exception:
            return

    def cleanup_stale_slots(self) -> None:
        if not self.slot_dir.exists():
            return
        for slot_path in self.slot_dir.glob("slot-*"):
            if not slot_path.is_dir():
                continue
            metadata = self._read_metadata(slot_path)
            worker_pid = _safe_int(metadata.get("worker_pid"))
            if worker_pid and not _pid_is_alive(worker_pid):
                self.release(slot_path)
                continue
            slot_age = _seconds_since_iso(metadata.get("acquired_at"))
            if slot_age is None:
                try:
                    slot_age = time.time() - slot_path.stat().st_mtime
                except OSError:
                    slot_age = None
            if not worker_pid and slot_age is not None and slot_age > JOB_SLOT_ATTACH_STALE_SECONDS:
                self.release(slot_path)

    def _metadata_path(self, slot_path: Path) -> Path:
        return slot_path / "metadata.json"

    def _read_metadata(self, slot_path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(self._metadata_path(slot_path).read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _write_metadata(self, slot_path: Path, metadata: dict[str, Any]) -> None:
        self._metadata_path(slot_path).write_text(
            json.dumps(_json_safe(metadata), ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )


class JobStore:
    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir
        self.index_path = jobs_dir / "index.json"
        self.lock = threading.Lock()
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self.index_path.write_text("[]", encoding="utf-8")
        self.rebuild_index_from_jobs_if_needed()
        self.reconcile_running_jobs()

    def _job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _load_index_unlocked(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return payload
        except Exception:
            pass
        return []

    def _save_index_unlocked(self, entries: list[dict[str, Any]]) -> None:
        self.index_path.write_text(json.dumps(_json_safe(entries), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    def _load_job_unlocked(self, job_id: str) -> dict[str, Any] | None:
        job_path = self._job_path(job_id)
        if not job_path.exists():
            return None
        try:
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def _save_job_unlocked(self, job_id: str, record: dict[str, Any]) -> None:
        self._job_path(job_id).write_text(json.dumps(_json_safe(record), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    def _upsert_index_entry_unlocked(self, record: dict[str, Any]) -> None:
        job_id = str(record.get("job_id", "")).strip()
        index_entries = self._load_index_unlocked()
        summary = {
            "job_id": job_id,
            "owner_email": _normalize_email(record.get("owner_email")),
            "status": str(record.get("status", "queued")),
            "created_at": record.get("created_at"),
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
            "metadata": deepcopy(record.get("metadata") or {}),
            "prepared_payload_summary": deepcopy(record.get("prepared_payload_summary") or {}),
            "error": record.get("error"),
        }
        updated = False
        for idx, entry in enumerate(index_entries):
            if str(entry.get("job_id", "")).strip() == job_id:
                index_entries[idx] = summary
                updated = True
                break
        if not updated:
            index_entries.insert(0, summary)
        self._save_index_unlocked(index_entries)

    def rebuild_index_from_jobs_if_needed(self) -> None:
        with self.lock:
            if self._load_index_unlocked():
                return
            records: list[dict[str, Any]] = []
            for job_path in self.jobs_dir.glob("*.json"):
                if job_path.name == self.index_path.name:
                    continue
                try:
                    payload = json.loads(job_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(payload, dict) and str(payload.get("job_id", "")).strip():
                    records.append(payload)
            if not records:
                return
            records.sort(
                key=lambda item: str(item.get("created_at") or item.get("started_at") or item.get("finished_at") or ""),
                reverse=True,
            )
            for record in records:
                self._upsert_index_entry_unlocked(record)

    def create_job(
        self,
        config_payload: dict[str, Any],
        prepared_payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        owner_email: str = "",
    ) -> dict[str, Any]:
        job_id = uuid4().hex[:12]
        created_at = utc_now_iso()
        normalized_owner_email = _normalize_email(owner_email)
        record = {
            "job_id": job_id,
            "owner_email": normalized_owner_email,
            "status": "queued",
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "worker_pid": None,
            "job_slot_path": None,
            "config": deepcopy(config_payload or {}),
            "prepared_payload": deepcopy(prepared_payload or {}),
            "prepared_payload_summary": _summarize_prepared_payload(prepared_payload or {}),
            "metadata": deepcopy(metadata or {}),
            "result": None,
            "error": None,
            "traceback": None,
        }
        with self.lock:
            self._save_job_unlocked(job_id, record)
            self._upsert_index_entry_unlocked(record)
        return {
            "job_id": job_id,
            "owner_email": normalized_owner_email,
            "status": "queued",
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "metadata": deepcopy(metadata or {}),
            "prepared_payload_summary": record["prepared_payload_summary"],
            "error": None,
        }

    def update_job(self, job_id: str, **changes: Any) -> dict[str, Any] | None:
        with self.lock:
            record = self._load_job_unlocked(job_id)
            if not record:
                return None
            record.update(changes)
            self._save_job_unlocked(job_id, record)
            self._upsert_index_entry_unlocked(record)
            return deepcopy(record)

    def begin_ai_audit(self, job_id: str, *, force: bool = False) -> tuple[str, dict[str, Any] | None]:
        with self.lock:
            record = self._load_job_unlocked(job_id)
            if not record:
                return "missing", None
            existing_report = record.get("ai_audit_report")
            if isinstance(existing_report, dict) and existing_report and not force:
                return "cached", deepcopy(record)
            if str(record.get("ai_audit_status", "")).strip().lower() == "running":
                return "running", deepcopy(record)
            record["ai_audit_status"] = "running"
            record["ai_audit_started_at"] = utc_now_iso()
            record["ai_audit_finished_at"] = None
            record["ai_audit_error"] = None
            self._save_job_unlocked(job_id, record)
            self._upsert_index_entry_unlocked(record)
            return "started", deepcopy(record)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            record = self._load_job_unlocked(job_id)
            return deepcopy(record) if record else None

    def list_jobs(self, user_email: str = "", include_all: bool = False) -> list[dict[str, Any]]:
        with self.lock:
            entries = self._load_index_unlocked()
            if include_all:
                return deepcopy(entries)
            normalized_user_email = _normalize_email(user_email)
            return [
                deepcopy(entry)
                for entry in entries
                if _normalize_email(entry.get("owner_email")) == normalized_user_email
            ]

    def list_queued_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            records: list[dict[str, Any]] = []
            seen_job_ids: set[str] = set()
            index_entries = self._load_index_unlocked()
            for entry in index_entries:
                job_id = str(entry.get("job_id", "")).strip()
                if not job_id or job_id in seen_job_ids:
                    continue
                seen_job_ids.add(job_id)
                record = self._load_job_unlocked(job_id)
                if record and str(record.get("status", "")).strip().lower() == "queued":
                    records.append(deepcopy(record))
            for job_path in self.jobs_dir.glob("*.json"):
                if job_path.name == self.index_path.name:
                    continue
                job_id = job_path.stem
                if job_id in seen_job_ids:
                    continue
                record = self._load_job_unlocked(job_id)
                if record and str(record.get("status", "")).strip().lower() == "queued":
                    records.append(deepcopy(record))
            records.sort(key=lambda item: str(item.get("created_at") or ""))
            return records

    def delete_job(self, job_id: str) -> bool:
        with self.lock:
            job_path = self._job_path(job_id)
            if not job_path.exists():
                return False
            job_path.unlink(missing_ok=True)
            index_entries = [
                entry
                for entry in self._load_index_unlocked()
                if str(entry.get("job_id", "")).strip() != job_id
            ]
            self._save_index_unlocked(index_entries)
            return True

    def reconcile_running_jobs(self) -> None:
        with self.lock:
            index_entries = self._load_index_unlocked()
            for entry in index_entries:
                job_id = str(entry.get("job_id", "")).strip()
                if not job_id:
                    continue
                record = self._load_job_unlocked(job_id)
                if not record:
                    continue
                status = str(record.get("status", "")).strip().lower()
                if status == "queued":
                    record["worker_pid"] = None
                    record["job_slot_path"] = None
                    self._save_job_unlocked(job_id, record)
                    continue
                if status == "running":
                    record["status"] = "failed"
                    record["finished_at"] = utc_now_iso()
                    record["error"] = "Job was interrupted because the backend service restarted."
                    record["traceback"] = None
                    record["worker_pid"] = None
                    record["job_slot_path"] = None
                    self._save_job_unlocked(job_id, record)
            refreshed_entries = []
            for entry in self._load_index_unlocked():
                job_id = str(entry.get("job_id", "")).strip()
                record = self._load_job_unlocked(job_id) if job_id else None
                if record:
                    refreshed_entries.append(
                        {
                            "job_id": job_id,
                            "owner_email": _normalize_email(record.get("owner_email")),
                            "status": str(record.get("status", "queued")),
                            "created_at": record.get("created_at"),
                            "started_at": record.get("started_at"),
                            "finished_at": record.get("finished_at"),
                            "metadata": deepcopy(record.get("metadata") or {}),
                            "prepared_payload_summary": deepcopy(record.get("prepared_payload_summary") or {}),
                            "error": record.get("error"),
                        }
                    )
            self._save_index_unlocked(refreshed_entries)


class SideToolHistoryStore:
    def __init__(self, root_dir: Path, tool_key: str) -> None:
        self.tool_key = tool_key
        self.tool_dir = root_dir / tool_key
        self.index_path = self.tool_dir / "index.json"
        self.lock = threading.Lock()
        self.tool_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self.index_path.write_text("[]", encoding="utf-8")

    def _record_path(self, run_id: str) -> Path:
        return self.tool_dir / f"{run_id}.json"

    def _load_index_unlocked(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return payload
        except Exception:
            pass
        return []

    def _save_index_unlocked(self, entries: list[dict[str, Any]]) -> None:
        self.index_path.write_text(
            json.dumps(_json_safe(entries), ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )

    def _summary_for_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": str(record.get("run_id") or ""),
            "tool_key": self.tool_key,
            "owner_email": _normalize_email(record.get("owner_email")),
            "title": str(record.get("title") or ""),
            "created_at": record.get("created_at"),
            "summary": deepcopy(record.get("summary") or {}),
        }

    def create(self, payload: dict[str, Any], owner_email: str) -> dict[str, Any]:
        run_id = uuid4().hex[:12]
        created_at = utc_now_iso()
        normalized_owner_email = _normalize_email(owner_email)
        title = str(payload.get("title") or "").strip() or f"Fleet Planner Run - {datetime.now().strftime('%Y-%m-%d %H%M')}"
        record = {
            "run_id": run_id,
            "tool_key": self.tool_key,
            "owner_email": normalized_owner_email,
            "title": title,
            "created_at": created_at,
            "scenario": deepcopy(payload.get("scenario") or {}),
            "preview_result": deepcopy(payload.get("preview_result") or {}),
            "geocode_result": deepcopy(payload.get("geocode_result") or {}),
            "cluster_result": deepcopy(payload.get("cluster_result") or {}),
            "route_preview_result": deepcopy(payload.get("route_preview_result") or {}),
            "global_plan_result": deepcopy(payload.get("global_plan_result") or {}),
            "summary": deepcopy(payload.get("summary") or {}),
        }
        summary = self._summary_for_record(record)
        with self.lock:
            self._record_path(run_id).write_text(
                json.dumps(_json_safe(record), ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
            entries = [
                entry
                for entry in self._load_index_unlocked()
                if str(entry.get("run_id") or "") != run_id
            ]
            entries.insert(0, summary)
            self._save_index_unlocked(entries[:100])
        return summary

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.lock:
            record_path = self._record_path(run_id)
            if not record_path.exists():
                return None
            try:
                payload = json.loads(record_path.read_text(encoding="utf-8"))
                return deepcopy(payload) if isinstance(payload, dict) else None
            except Exception:
                return None

    def list(self, user_email: str = "", include_all: bool = False) -> list[dict[str, Any]]:
        with self.lock:
            entries = self._load_index_unlocked()
            if include_all:
                return deepcopy(entries)
            normalized_user_email = _normalize_email(user_email)
            return [
                deepcopy(entry)
                for entry in entries
                if _normalize_email(entry.get("owner_email")) == normalized_user_email
            ]


JOB_STORE = JobStore(JOBS_DIR)
FLEET_PLANNER_HISTORY_STORE = SideToolHistoryStore(SIDE_TOOLS_DIR, "fleet_planner")
JOB_GATE = JobConcurrencyGate(MAX_CONCURRENT_JOBS, JOB_CONCURRENCY_DIR)
_SCHEDULER_LOCK = threading.Lock()
_SCHEDULER_STARTED = False


def _process_is_alive(pid: int | None) -> bool:
    return _pid_is_alive(pid)


def _spawn_job_worker(job_id: str) -> dict[str, Any] | None:
    job_record = JOB_STORE.get_job(job_id)
    if not job_record:
        return None
    if str(job_record.get("status", "")).strip().lower() != "queued":
        return None
    slot_path = JOB_GATE.acquire(job_id)
    if JOB_GATE.enabled and slot_path is None:
        return None
    env = os.environ.copy()
    if slot_path:
        env["BRP_JOB_CONCURRENCY_SLOT"] = str(slot_path)
        env["BRP_JOB_CONCURRENCY_ROOT"] = str(JOB_CONCURRENCY_DIR)
    try:
        process = subprocess.Popen(
            [sys.executable, str(JOB_RUNNER_PATH), str(JOBS_DIR / f"{job_id}.json")],
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except Exception:
        JOB_GATE.release(slot_path)
        raise
    JOB_GATE.attach_worker(slot_path, int(process.pid))
    latest_record = JOB_STORE.get_job(job_id)
    if not latest_record:
        return None
    if str(latest_record.get("status", "")).strip().lower() not in {"queued", "running"}:
        return latest_record
    return JOB_STORE.update_job(job_id, worker_pid=int(process.pid), job_slot_path=str(slot_path) if slot_path else None)


def _schedule_queued_jobs() -> None:
    if not _SCHEDULER_LOCK.acquire(blocking=False):
        return
    try:
        JOB_GATE.cleanup_stale_slots()
        for job_record in JOB_STORE.list_queued_jobs():
            spawned = _spawn_job_worker(str(job_record.get("job_id", "")))
            if spawned is None and JOB_GATE.enabled:
                break
    finally:
        _SCHEDULER_LOCK.release()


def _job_scheduler_loop() -> None:
    while True:
        try:
            _schedule_queued_jobs()
        except Exception:
            traceback.print_exc()
        time.sleep(JOB_QUEUE_POLL_SECONDS)


def _start_job_scheduler() -> None:
    global _SCHEDULER_STARTED
    if _SCHEDULER_STARTED:
        return
    _SCHEDULER_STARTED = True
    threading.Thread(target=_job_scheduler_loop, name="brp-job-scheduler", daemon=True).start()


def _cancel_job(job_id: str) -> dict[str, Any] | None:
    job_record = JOB_STORE.get_job(job_id)
    if not job_record:
        return None
    status = str(job_record.get("status", "")).strip().lower()
    pid = int(job_record.get("worker_pid", 0) or 0)
    if status in {"succeeded", "failed", "canceled"}:
        return job_record
    if _process_is_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    JOB_GATE.release(job_record.get("job_slot_path"))
    JOB_GATE.cleanup_stale_slots()
    updated = JOB_STORE.update_job(
        job_id,
        status="canceled",
        finished_at=utc_now_iso(),
        error="Job was canceled by the user.",
        traceback=None,
        worker_pid=None,
        job_slot_path=None,
        result=None,
    )
    _schedule_queued_jobs()
    return updated


def _can_access_job(job_record: dict[str, Any], user_email: str, include_all: bool = False) -> bool:
    if include_all:
        return True
    return _normalize_email(job_record.get("owner_email")) == _normalize_email(user_email)


def _strip_api_prefix(path: str) -> str:
    if path == "/api":
        return "/"
    if path.startswith("/api/"):
        return path[4:]
    return path


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolve_job_map_artifact(job_record: dict[str, Any], artifact_key: str) -> tuple[Path | None, str | None]:
    scenario_key = MAP_ARTIFACT_KEYS.get(artifact_key.strip().lower())
    if not scenario_key:
        return None, f"Unknown artifact: {artifact_key}"

    result = dict(job_record.get("result") or {})
    structured = dict(result.get("structured_results") or {})
    scenario = dict(structured.get(scenario_key) or {})
    output_paths = dict(structured.get("output_paths") or {})
    candidate_paths = [
        scenario.get("output_html"),
        output_paths.get(scenario_key),
        result.get(MAP_ARTIFACT_TOP_LEVEL_KEYS.get(scenario_key, "")),
    ]
    outputs_root = (BASE_DIR / "outputs").resolve()

    for raw_path in candidate_paths:
        if not raw_path:
            continue
        artifact_path = Path(str(raw_path)).expanduser().resolve()
        if artifact_path.suffix.lower() != ".html":
            return None, "Map artifact is not an HTML file."
        if not _path_is_relative_to(artifact_path, outputs_root):
            return None, "Map artifact is outside the backend outputs directory."
        if not artifact_path.exists():
            return None, f"Map artifact file is missing: {artifact_path.name}"
        return artifact_path, None

    return None, f"Map artifact is not available: {artifact_key}"


def _infer_output_directory_name(result: dict[str, Any]) -> str:
    structured = dict(result.get("structured_results") or {})
    outputs_root = (BASE_DIR / "outputs").resolve()
    for scenario_key in MAP_ARTIFACT_TOP_LEVEL_KEYS:
        scenario = dict(structured.get(scenario_key) or {})
        raw_path = scenario.get("output_html") or result.get(MAP_ARTIFACT_TOP_LEVEL_KEYS.get(scenario_key, ""))
        if not raw_path:
            continue
        artifact_path = Path(str(raw_path)).expanduser().resolve()
        if _path_is_relative_to(artifact_path, outputs_root):
            return artifact_path.parent.name
    return ""


def _build_rerender_config_for_job(job_record: dict[str, Any]) -> PlannerConfig:
    metadata = dict(job_record.get("metadata") or {})
    config_payload = dict(job_record.get("config") or metadata.get("planner_config") or {})
    config = _build_planner_config(config_payload)
    if not config.output_directory_name:
        result = dict(job_record.get("result") or {})
        config.output_directory_name = _infer_output_directory_name(result) or str(job_record.get("job_id") or uuid4().hex)
    return config


def _rerender_job_map_artifacts(job_id: str, job_record: dict[str, Any]) -> dict[str, Any]:
    result = dict(job_record.get("result") or {})
    structured = dict(result.get("structured_results") or {})
    if not structured:
        return job_record
    hydrated = rerender_html_from_structured_results(structured, _build_rerender_config_for_job(job_record))
    updated_result = dict(result)
    updated_result["structured_results"] = hydrated
    output_paths = dict(hydrated.get("output_paths") or {})
    for scenario_key, top_level_key in MAP_ARTIFACT_TOP_LEVEL_KEYS.items():
        if output_paths.get(scenario_key):
            updated_result[top_level_key] = output_paths[scenario_key]
    updated = JOB_STORE.update_job(job_id, result=updated_result)
    if updated:
        return updated
    rerendered = dict(job_record)
    rerendered["result"] = updated_result
    return rerendered


def _build_free_baseline_template_export(job_record: dict[str, Any]) -> tuple[bytes | None, str | None]:
    result = dict(job_record.get("result") or {})
    structured = dict(result.get("structured_results") or {})
    scenario = (
        dict(result.get("free_optimization_baseline") or {})
        or dict(structured.get("free_optimization_baseline") or {})
        or dict(structured.get("original") or {})
    )
    if not list(scenario.get("routes") or []) or not list(scenario.get("points") or []):
        return None, "Free optimization baseline has no route table to export."
    planner_config = dict(result.get("planner_config") or job_record.get("config") or {})
    service_direction = str(result.get("service_direction") or planner_config.get("service_direction") or "From School")
    try:
        return build_baseline_template_workbook_bytes(scenario, service_direction=service_direction), None
    except Exception as exc:
        return None, str(exc)


class BackendHandler(BaseHTTPRequestHandler):
    server_version = "BusingRoutingBackend/1.0"

    def _send_json(self, status_code: int, payload: dict[str, Any] | list[Any]) -> bool:
        body = json.dumps(_json_safe(payload), ensure_ascii=False, allow_nan=False).encode("utf-8")
        try:
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True
        except (BrokenPipeError, ConnectionResetError):
            print(f"[WARN] Client disconnected before response was fully sent: {self.path}")
            return False

    def _send_bytes(
        self,
        status_code: int,
        body: bytes,
        *,
        content_type: str,
        filename: str | None = None,
        inline: bool = True,
    ) -> bool:
        try:
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            if filename:
                disposition = "inline" if inline else "attachment"
                self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(filename)}")
            self.end_headers()
            self.wfile.write(body)
            return True
        except (BrokenPipeError, ConnectionResetError):
            print(f"[WARN] Client disconnected before response was fully sent: {self.path}")
            return False

    def _send_redirect(self, location: str, status_code: int = 302) -> bool:
        try:
            self.send_response(status_code)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return True
        except (BrokenPipeError, ConnectionResetError):
            print(f"[WARN] Client disconnected before redirect was sent: {self.path}")
            return False

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        raw_body = self.rfile.read(content_length)
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        return payload if isinstance(payload, dict) else {}

    def _current_user_email(self) -> str:
        if AUTH_PROVIDER == "local":
            return _normalize_email(self.headers.get("X-BRP-User-Email")) or DEV_USER_EMAIL
        return (
            _normalize_email(self.headers.get("X-BRP-User-Email"))
            or _normalize_email(self.headers.get("Cf-Access-Authenticated-User-Email"))
            or DEV_USER_EMAIL
        )

    def _is_authorized_request(self) -> bool:
        if not SERVICE_TOKEN:
            return True
        authorization = str(self.headers.get("Authorization", "") or "").strip()
        expected = f"Bearer {SERVICE_TOKEN}"
        return authorization == expected

    def _require_authorized_request(self) -> bool:
        if self._is_authorized_request():
            return True
        self._send_json(401, {"error": "Unauthorized backend request."})
        return False

    def _handle_sync_compute(self, payload: dict[str, Any]) -> None:
        config_payload = payload.get("config") or {}
        prepared_payload = payload.get("prepared_payload") or {}
        config = _build_planner_config(config_payload)
        result = run_backend_planner_with_prepared_data(prepared_payload, config=config)
        self._send_json(200, result)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = _strip_api_prefix(parsed.path)
        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if not self._require_authorized_request():
            return
        if path == "/auth/config":
            self._send_json(200, _auth_config_payload())
            return
        if path == "/auth/login":
            if AUTH_PROVIDER == "microsoft_sso_pending":
                self._send_json(
                    501,
                    {
                        "error": "Microsoft SSO is not configured yet.",
                        "auth": _auth_config_payload(),
                    },
                )
                return
            self._send_redirect(_auth_login_url())
            return
        if path == "/auth/logout":
            self._send_redirect(_auth_logout_url())
            return
        user_email = self._current_user_email()
        include_all = _is_admin_email(user_email)
        if path == "/me":
            self._send_json(
                200,
                {
                    "email": user_email,
                    "is_admin": include_all,
                    "auth_mode": AUTH_PROVIDER,
                    "auth": _auth_config_payload(),
                },
            )
            return
        if path == "/google-geocode-usage":
            self._send_json(200, _google_geocode_usage_payload())
            return
        if path == "/workbooks/template":
            self._send_bytes(
                200,
                build_excel_template_bytes(),
                content_type=WORKBOOK_CONTENT_TYPE,
                filename="brp_planning_template.xlsx",
                inline=False,
            )
            return
        if path == "/fleet-planner/demand-template":
            demand_input = _client_module("demand_input")
            self._send_bytes(
                200,
                demand_input.build_demand_template_workbook_bytes(),
                content_type=WORKBOOK_CONTENT_TYPE,
                filename="brp_demand_template.xlsx",
                inline=False,
            )
            return
        if path == "/fleet-planner/history":
            self._send_json(200, {"jobs": FLEET_PLANNER_HISTORY_STORE.list(user_email=user_email, include_all=include_all)})
            return
        if path.startswith("/fleet-planner/history/"):
            run_id = unquote(path.rsplit("/", 1)[-1]).strip()
            record = FLEET_PLANNER_HISTORY_STORE.get(run_id)
            if not record:
                self._send_json(404, {"error": f"Fleet Planner history run not found: {run_id}"})
                return
            if not _can_access_job(record, user_email, include_all=include_all):
                self._send_json(403, {"error": f"Fleet Planner history run is not available for user: {user_email}"})
                return
            self._send_json(200, record)
            return
        if path == "/workbooks/demos":
            self._send_json(200, {"demos": _list_demo_workbooks()})
            return
        if path.startswith("/workbooks/demos/"):
            demo_name = unquote(path.rsplit("/", 1)[-1])
            demo_path = _resolve_demo_workbook_path(demo_name)
            if not demo_path:
                self._send_json(404, {"error": f"Demo workbook not found: {demo_name}"})
                return
            self._send_bytes(
                200,
                demo_path.read_bytes(),
                content_type=WORKBOOK_CONTENT_TYPE,
                filename=demo_path.name,
                inline=False,
            )
            return
        if path == "/jobs":
            self._send_json(200, {"jobs": JOB_STORE.list_jobs(user_email=user_email, include_all=include_all)})
            return
        if path.startswith("/jobs/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) == 4 and parts[2] == "exports":
                job_id = parts[1].strip()
                export_key = unquote(parts[3]).strip().lower()
                job_record = JOB_STORE.get_job(job_id)
                if not job_record:
                    self._send_json(404, {"error": f"Job not found: {job_id}"})
                    return
                if not _can_access_job(job_record, user_email, include_all=include_all):
                    self._send_json(403, {"error": f"Job is not available for user: {user_email}"})
                    return
                if export_key != "free-optimization-template":
                    self._send_json(404, {"error": f"Unknown export: {export_key}"})
                    return
                workbook_bytes, export_error = _build_free_baseline_template_export(job_record)
                if export_error or not workbook_bytes:
                    self._send_json(404, {"error": export_error or "Export is not available."})
                    return
                self._send_bytes(
                    200,
                    workbook_bytes,
                    content_type=WORKBOOK_CONTENT_TYPE,
                    filename=f"free_optimization_baseline_{job_id}.xlsx",
                    inline=False,
                )
                return
            if len(parts) == 4 and parts[2] == "artifacts":
                job_id = parts[1].strip()
                artifact_key = unquote(parts[3]).strip()
                job_record = JOB_STORE.get_job(job_id)
                if not job_record:
                    self._send_json(404, {"error": f"Job not found: {job_id}"})
                    return
                if not _can_access_job(job_record, user_email, include_all=include_all):
                    self._send_json(403, {"error": f"Job is not available for user: {user_email}"})
                    return
                query_params = dict(parse_qsl(parsed.query))
                if query_params.get("refresh") in {"1", "true", "yes"}:
                    job_record = _rerender_job_map_artifacts(job_id, job_record)
                artifact_path, artifact_error = _resolve_job_map_artifact(job_record, artifact_key)
                if artifact_error and "file is missing" in artifact_error:
                    job_record = _rerender_job_map_artifacts(job_id, job_record)
                    artifact_path, artifact_error = _resolve_job_map_artifact(job_record, artifact_key)
                if artifact_error or not artifact_path:
                    self._send_json(404, {"error": artifact_error or f"Artifact not found: {artifact_key}"})
                    return
                inline = query_params.get("download") not in {"1", "true", "yes"}
                self._send_bytes(
                    200,
                    artifact_path.read_bytes(),
                    content_type="text/html; charset=utf-8",
                    filename=artifact_path.name,
                    inline=inline,
                )
                return
            if len(parts) != 2:
                self._send_json(404, {"error": f"Unknown path: {path}"})
                return
            job_id = parts[1].strip()
            job_record = JOB_STORE.get_job(job_id)
            if not job_record:
                self._send_json(404, {"error": f"Job not found: {job_id}"})
                return
            if not _can_access_job(job_record, user_email, include_all=include_all):
                self._send_json(403, {"error": f"Job is not available for user: {user_email}"})
                return
            self._send_json(200, job_record)
            return
        self._send_json(404, {"error": f"Unknown path: {path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = _strip_api_prefix(parsed.path)
        try:
            if not self._require_authorized_request():
                return
            user_email = self._current_user_email()
            include_all = _is_admin_email(user_email)
            payload = self._read_json_body()
            if path == "/compute":
                self._handle_sync_compute(payload)
                return
            if path == "/workbooks/preview":
                self._send_json(200, _handle_workbook_preview(payload))
                return
            if path == "/workbooks/submit":
                self._send_json(202, _handle_workbook_submit(payload, user_email=user_email))
                return
            if path == "/distance-checker/workbook-preview":
                self._send_json(200, _handle_distance_workbook_preview(payload))
                return
            if path == "/distance-checker/reference":
                self._send_json(200, _handle_reference_distance_check(payload))
                return
            if path == "/distance-checker/route-cost":
                self._send_json(200, _handle_current_plan_route_cost(payload))
                return
            if path == "/fleet-planner/preview":
                self._send_json(200, _handle_fleet_planner_preview(payload))
                return
            if path == "/fleet-planner/geocode":
                self._send_json(200, _handle_fleet_planner_geocode(payload))
                return
            if path == "/fleet-planner/clusters":
                self._send_json(200, _handle_fleet_planner_clusters(payload))
                return
            if path == "/fleet-planner/route-preview":
                self._send_json(200, _handle_fleet_planner_route_preview(payload))
                return
            if path == "/fleet-planner/global-plan":
                self._send_json(200, _handle_fleet_planner_global_plan(payload))
                return
            if path == "/fleet-planner/history":
                self._send_json(201, _handle_fleet_planner_history_create(payload, user_email=user_email))
                return
            if path == "/jobs":
                config_payload = payload.get("config") or {}
                prepared_payload = payload.get("prepared_payload") or {}
                metadata = payload.get("metadata") or {}
                summary = JOB_STORE.create_job(
                    config_payload,
                    prepared_payload,
                    metadata=metadata,
                    owner_email=user_email,
                )
                spawned = _spawn_job_worker(str(summary["job_id"]))
                if spawned:
                    summary["worker_pid"] = spawned.get("worker_pid")
                self._send_json(202, summary)
                return
            if path.startswith("/jobs/") and path.endswith("/cancel"):
                parts = [part for part in path.split("/") if part]
                if len(parts) != 3:
                    self._send_json(404, {"error": f"Unknown path: {path}"})
                    return
                job_id = parts[1].strip()
                job_record = JOB_STORE.get_job(job_id)
                if not job_record:
                    self._send_json(404, {"error": f"Job not found: {job_id}"})
                    return
                if not _can_access_job(job_record, user_email, include_all=include_all):
                    self._send_json(403, {"error": f"Job is not available for user: {user_email}"})
                    return
                updated = _cancel_job(job_id)
                if not updated:
                    self._send_json(404, {"error": f"Job not found: {job_id}"})
                    return
                self._send_json(200, updated)
                return
            if path.startswith("/jobs/") and path.endswith("/ai-audit"):
                parts = [part for part in path.split("/") if part]
                if len(parts) != 3:
                    self._send_json(404, {"error": f"Unknown path: {path}"})
                    return
                job_id = parts[1].strip()
                job_record = JOB_STORE.get_job(job_id)
                if not job_record:
                    self._send_json(404, {"error": f"Job not found: {job_id}"})
                    return
                if not _can_access_job(job_record, user_email, include_all=include_all):
                    self._send_json(403, {"error": f"Job is not available for user: {user_email}"})
                    return
                force_ai_audit = bool(payload.get("force"))
                audit_state, audit_record = JOB_STORE.begin_ai_audit(job_id, force=force_ai_audit)
                if audit_state == "missing" or not audit_record:
                    self._send_json(404, {"error": f"Job not found: {job_id}"})
                    return
                if audit_state == "cached":
                    self._send_json(
                        200,
                        {
                            "job_id": job_id,
                            "ai_audit_status": "succeeded",
                            "ai_audit_report": dict(audit_record.get("ai_audit_report") or {}),
                            "cached": True,
                        },
                    )
                    return
                if audit_state == "running":
                    self._send_json(
                        202,
                        {
                            "job_id": job_id,
                            "ai_audit_status": "running",
                            "ai_audit_report": dict(audit_record.get("ai_audit_report") or {}),
                            "message": "AI audit generation is already running for this job.",
                        },
                    )
                    return
                try:
                    ai_report = generate_ai_audit_report(
                        audit_record,
                        force=force_ai_audit,
                        language=str(payload.get("language") or "").strip() or None,
                    )
                except Exception as exc:
                    JOB_STORE.update_job(
                        job_id,
                        ai_audit_status="failed",
                        ai_audit_finished_at=utc_now_iso(),
                        ai_audit_error=str(exc),
                    )
                    raise
                updated = JOB_STORE.update_job(
                    job_id,
                    ai_audit_report=ai_report,
                    ai_audit_status="succeeded",
                    ai_audit_finished_at=utc_now_iso(),
                    ai_audit_error=None,
                )
                if updated:
                    updated["config"] = None
                    updated["prepared_payload"] = None
                    self._send_json(200, {"job_id": job_id, "ai_audit_status": "succeeded", "ai_audit_report": ai_report})
                    return
                self._send_json(404, {"error": f"Job not found: {job_id}"})
                return
            self._send_json(404, {"error": f"Unknown path: {path}"})
        except Exception as exc:
            if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                print(f"[WARN] Client disconnected during request handling: {self.path}")
                return
            self._send_json(
                500,
                {
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = _strip_api_prefix(parsed.path)
        try:
            if not self._require_authorized_request():
                return
            user_email = self._current_user_email()
            include_all = _is_admin_email(user_email)
            if not path.startswith("/jobs/"):
                self._send_json(404, {"error": f"Unknown path: {path}"})
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) != 2:
                self._send_json(404, {"error": f"Unknown path: {path}"})
                return
            job_id = parts[1].strip()
            job_record = JOB_STORE.get_job(job_id)
            if not job_record:
                self._send_json(404, {"error": f"Job not found: {job_id}"})
                return
            if not _can_access_job(job_record, user_email, include_all=include_all):
                self._send_json(403, {"error": f"Job is not available for user: {user_email}"})
                return
            _cancel_job(job_id)
            JOB_STORE.delete_job(job_id)
            self._send_json(200, {"deleted": True, "job_id": job_id})
        except Exception as exc:
            self._send_json(
                500,
                {
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )


def main(host: str = "127.0.0.1", port: int = 8001) -> None:
    _start_job_scheduler()
    server = ThreadingHTTPServer((host, port), BackendHandler)
    print(f"Backend listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    host = os.environ.get("BRP_BACKEND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    raw_port = os.environ.get("BRP_BACKEND_PORT", "8001").strip() or "8001"
    main(host=host, port=int(raw_port))
