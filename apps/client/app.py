from __future__ import annotations

from datetime import datetime
from html import escape as html_escape
import http.server
import io
from functools import partial
import json
import os
from pathlib import Path
import tempfile
import time
import textwrap
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from client_core import (
    PlannerConfig,
    SERVICE_DIRECTION_OPTIONS,
    TRAFFIC_PROFILE_MULTIPLIERS,
    TRAFFIC_PROFILE_OPTIONS,
    build_excel_template_bytes,
    cancel_backend_job,
    delete_backend_job,
    friendly_error_message,
    generate_backend_ai_audit,
    get_backend_job,
    get_excel_sheet_names,
    list_backend_jobs,
    prepare_client_payload,
    read_current_plan_from_excel,
    rerender_html_from_structured_results,
    submit_job_to_backend,
    summarize_current_plan_assessment,
)
from client_runtime import is_likely_english_korean_address
from distance_checker_page import render_distance_checker_page
from fleet_planner_page import render_fleet_planner_page


st.set_page_config(page_title="BRP Audit & Planning Client", layout="wide")
tool_name = str(st.query_params.get("tool", "")).strip().lower()
if tool_name == "distance-checker":
    render_distance_checker_page()
    st.stop()
if tool_name == "fleet-planner-preview":
    render_fleet_planner_page()
    st.stop()

st.title("BRP Audit & Planning Client")
st.caption(
    "Upload a current-plan workbook to assess the existing scheme, generate like-for-like and free-optimization baselines, "
    "and surface route-to-route improvement opportunities."
)
st.markdown(
    """
    <style>
    div[data-testid="stFileUploaderDropzone"] {
        min-height: 3.5rem !important;
        height: 3.5rem !important;
        padding-top: 0.2rem !important;
        padding-bottom: 0.2rem !important;
        display: flex !important;
        align-items: center !important;
    }
    div[data-testid="stFileUploaderDropzone"] > div {
        width: 100%;
    }
    div[data-testid="stFileUploaderDropzoneInstructions"] {
        padding: 0 !important;
    }
    div[data-testid="stFileUploaderDropzoneInstructions"] > div {
        gap: 0.65rem !important;
    }
    div[data-testid="stFileUploader"] small {
        margin-top: 0 !important;
    }
    div[data-testid="column"] button[kind="secondary"] {
        min-height: 3.5rem !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


APP_BASE_DIR = Path(__file__).resolve().parent
DEMO_DATA_DIR = APP_BASE_DIR / "demodata"
BACKEND_BASE_URL = os.environ.get("BRP_BACKEND_BASE_URL", "http://127.0.0.1:8001")
BACKEND_TIMEOUT_SECONDS = int(os.environ.get("BRP_BACKEND_TIMEOUT_SECONDS", "1800"))
DEV_USER_EMAIL = os.environ.get("BRP_DEV_USER_EMAIL", "local@brp.dev").strip().lower()
ROUTE_DURATION_GRACE_MINUTES = 10


def get_current_user_email() -> str:
    context = getattr(st, "context", None)
    headers = getattr(context, "headers", {}) if context is not None else {}
    cloudflare_email = ""
    if headers:
        try:
            cloudflare_email = str(
                headers.get("Cf-Access-Authenticated-User-Email", "")
                or headers.get("cf-access-authenticated-user-email", "")
                or ""
            ).strip()
        except AttributeError:
            cloudflare_email = ""
    return (cloudflare_email or DEV_USER_EMAIL or "local@brp.dev").strip().lower()


def get_access_logout_url() -> str:
    context = getattr(st, "context", None)
    headers = getattr(context, "headers", {}) if context is not None else {}
    host = ""
    proto = "https"
    if headers:
        try:
            host = str(headers.get("Host", "") or headers.get("host", "") or "").strip()
            proto = str(
                headers.get("X-Forwarded-Proto", "")
                or headers.get("x-forwarded-proto", "")
                or proto
            ).strip()
        except AttributeError:
            host = ""
    if host:
        return f"{proto}://{host}/cdn-cgi/access/logout"
    return "/cdn-cgi/access/logout"


CURRENT_USER_EMAIL = get_current_user_email()
ACCESS_LOGOUT_URL = get_access_logout_url()


def render_sign_out_link(logout_url: str) -> None:
    safe_url = html_escape(str(logout_url), quote=True)
    st.markdown(
        f"""
        <a class="brp-sign-out-link" href="{safe_url}" target="_self">Sign out</a>
        <style>
        .brp-sign-out-link {{
            display: block;
            width: 100%;
            box-sizing: border-box;
            padding: 0.45rem 0.75rem;
            margin: 0.25rem 0 1rem 0;
            border: 1px solid rgba(49, 51, 63, 0.2);
            border-radius: 0.5rem;
            color: rgb(49, 51, 63);
            text-align: center;
            text-decoration: none;
            font-size: 0.9rem;
            background: white;
        }}
        .brp-sign-out-link:hover {{
            border-color: rgba(49, 51, 63, 0.4);
            color: rgb(49, 51, 63);
            text-decoration: none;
            background: rgba(250, 250, 250, 1);
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def effective_route_duration_limit_minutes(route_duration_limit_minutes: int) -> int:
    return max(1, int(route_duration_limit_minutes) + ROUTE_DURATION_GRACE_MINUTES)


def configured_bus_slot_names() -> tuple[str, str, str]:
    return (
        str(st.session_state.get("planner_large_bus_name", "Large Bus")).strip() or "Large Bus",
        str(st.session_state.get("planner_mid_bus_name", "Mid Bus")).strip() or "Mid Bus",
        str(st.session_state.get("planner_small_bus_name", "Small Bus")).strip() or "Small Bus",
    )


def format_bus_mix(bus_mix: dict[str, int]) -> str:
    if not bus_mix:
        return "No buses used"
    ordered_parts = []
    preferred_names = configured_bus_slot_names()
    for name in preferred_names:
        count = int(bus_mix.get(name, 0))
        if count > 0:
            ordered_parts.append(f"{name}: {count}")
    for name, count in bus_mix.items():
        if name not in preferred_names and int(count) > 0:
            ordered_parts.append(f"{name}: {int(count)}")
    return " | ".join(ordered_parts) if ordered_parts else "No buses used"


def format_distance_km(distance_km: float) -> str:
    return f"{float(distance_km):,.1f} km"


def find_subway_aggregation_block_reason(records: list[dict[str, object]]) -> str | None:
    for item in records:
        country = str(item.get("country", "")).strip()
        address = str(item.get("address", "")).strip()
        if is_likely_english_korean_address(country, address):
            return (
                "Subway aggregation is unavailable for South Korea rows that use English-only addresses, "
                "because those stops require Google geocoding."
            )
    return None


def format_duration_minutes(duration_minutes: float) -> str:
    return f"{float(duration_minutes):,.1f} min"


def format_bus_type_max_counts(max_counts: dict[str, object]) -> str:
    if not max_counts:
        return "No configured fleet limit"
    ordered_parts = []
    preferred_names = configured_bus_slot_names()
    for name in preferred_names:
        count = int(max_counts.get(name, 0) or 0)
        if count > 0:
            ordered_parts.append(f"{name}: {count}")
    for name, value in max_counts.items():
        if name not in preferred_names:
            count = int(value or 0)
            if count > 0:
                ordered_parts.append(f"{name}: {count}")
    return " | ".join(ordered_parts) if ordered_parts else "No configured fleet limit"


def format_vehicle_ratio(ratio_map: dict[str, object]) -> str:
    if not ratio_map:
        return "No ratio"
    ordered_parts = []
    preferred_names = configured_bus_slot_names()
    for name in preferred_names:
        raw_value = float(ratio_map.get(name, 0.0) or 0.0)
        if raw_value > 0:
            display_value = int(raw_value) if abs(raw_value - int(raw_value)) < 1e-9 else round(raw_value, 2)
            ordered_parts.append(f"{name}: {display_value}")
    for name, raw_value in ratio_map.items():
        if name not in preferred_names:
            numeric_value = float(raw_value or 0.0)
            if numeric_value > 0:
                display_value = int(numeric_value) if abs(numeric_value - int(numeric_value)) < 1e-9 else round(numeric_value, 2)
                ordered_parts.append(f"{name}: {display_value}")
    return " | ".join(ordered_parts) if ordered_parts else "No ratio"


def maybe_autofill_planner_settings_from_current_plan(
    current_plan_preview: dict[str, object] | None,
    source_label: str,
) -> None:
    if not current_plan_preview:
        return
    fleet_items = list(current_plan_preview.get("fleet") or [])
    signature = (
        source_label,
        tuple(
            sorted(
                (
                    str(item.get("bus_type", "")).strip(),
                    int(item.get("seat_count", 0) or 0),
                    int(item.get("vehicle_count", 0) or 0),
                )
                for item in fleet_items
            )
        ),
    )
    if st.session_state.get("current_plan_autofill_signature") == signature:
        return

    normalized_fleet = []
    for item in fleet_items:
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
        st.session_state[f"planner_{slot_key}_bus_name"] = slot_name
        st.session_state[f"planner_{slot_key}_bus_capacity"] = seat_count
        st.session_state[f"planner_{slot_key}_bus_max_count"] = vehicle_count
        st.session_state[f"planner_free_baseline_{slot_key}_bus_ratio"] = float(vehicle_count)

    st.session_state["current_plan_autofill_signature"] = signature
    st.rerun()


def compute_route_load_factor_pct(route: dict[str, object]) -> float:
    capacity = float(route.get("bus_capacity", 0) or 0)
    load = float(route.get("load", 0) or 0)
    if capacity <= 0:
        return 0.0
    return load / capacity * 100.0


def keyed_number_input(label: str, key: str, default_value: int | float, **kwargs):
    if key in st.session_state:
        return st.number_input(label, key=key, **kwargs)
    return st.number_input(label, value=default_value, key=key, **kwargs)


def read_google_geocode_usage_display() -> str:
    usage_path = Path(__file__).resolve().parent / "cache" / "google_geocode_usage.json"
    month_key = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m")
    try:
        payload = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.exists() else {}
        used = max(0, int(payload.get(month_key, 0) or 0))
    except Exception:
        used = 0
    return f"Google geocode usage this month: {used:,} / 10,000"


def build_display_result_from_backend_payload(
    backend_result: dict[str, object],
    planner_config_snapshot: dict[str, object],
    client_prep: dict[str, object] | None = None,
) -> dict[str, object]:
    legacy_include_aggregation = bool(planner_config_snapshot.get("include_aggregation_scenarios", True))
    planner_config = PlannerConfig(
        large_bus_name=str(planner_config_snapshot.get("large_bus_name", "Large Bus")),
        mid_bus_name=str(planner_config_snapshot.get("mid_bus_name", "Mid Bus")),
        small_bus_name=str(planner_config_snapshot.get("small_bus_name", "Small Bus")),
        large_bus_capacity=int(planner_config_snapshot.get("large_bus_capacity", 42)),
        large_bus_max_count=int(planner_config_snapshot.get("large_bus_max_count", 20)),
        mid_bus_capacity=int(planner_config_snapshot.get("mid_bus_capacity", 35)),
        mid_bus_max_count=int(planner_config_snapshot.get("mid_bus_max_count", 15)),
        small_bus_capacity=int(planner_config_snapshot.get("small_bus_capacity", 19)),
        small_bus_max_count=int(planner_config_snapshot.get("small_bus_max_count", 10)),
        free_baseline_large_bus_ratio=float(planner_config_snapshot.get("free_baseline_large_bus_ratio", planner_config_snapshot.get("large_bus_max_count", 20))),
        free_baseline_mid_bus_ratio=float(planner_config_snapshot.get("free_baseline_mid_bus_ratio", planner_config_snapshot.get("mid_bus_max_count", 15))),
        free_baseline_small_bus_ratio=float(planner_config_snapshot.get("free_baseline_small_bus_ratio", planner_config_snapshot.get("small_bus_max_count", 10))),
        express_threshold_km=float(planner_config_snapshot.get("express_threshold_km", 15.0)),
        reserved_express_buses=int(planner_config_snapshot.get("reserved_express_buses", 4)),
        express_skip_inner_km=float(planner_config_snapshot.get("express_skip_inner_km", 8.0)),
        max_route_duration_minutes=int(planner_config_snapshot.get("max_route_duration_minutes", 60)),
        stop_service_minutes=int(planner_config_snapshot.get("stop_service_minutes", 1)),
        traffic_profile_name=str(planner_config_snapshot.get("traffic_profile_name", "Off-Peak")),
        service_direction=str(planner_config_snapshot.get("service_direction", "From School")),
        subway_search_radius_m=int(planner_config_snapshot.get("subway_search_radius_m", 1500)),
        max_subway_walk_distance_m=int(planner_config_snapshot.get("max_subway_walk_distance_m", 800)),
        nearby_cluster_radius_m=int(planner_config_snapshot.get("nearby_cluster_radius_m", 500)),
        include_subway_aggregation_scenario=bool(
            planner_config_snapshot.get("include_subway_aggregation_scenario", legacy_include_aggregation)
        ),
        include_nearby_aggregation_scenario=bool(
            planner_config_snapshot.get("include_nearby_aggregation_scenario", legacy_include_aggregation)
        ),
        operating_cost_per_km=0.0,
        revenue_rules=[{"min_km": 0.0, "max_km": None, "fee_per_person": 0.0}],
    )
    structured_results = rerender_html_from_structured_results(
        dict(backend_result.get("structured_results") or {}),
        planner_config,
    )
    client_prep = dict(client_prep or {})
    combined_logs = "\n\n".join(
        part.strip()
        for part in (str(client_prep.get("logs", "") or ""), str(backend_result.get("logs", "") or ""))
        if part.strip()
    )
    output_paths = structured_results.get("output_paths", {})
    return {
        "original_html": output_paths.get("original", ""),
        "current_plan_html": output_paths.get("current_plan", ""),
        "subway_html": output_paths.get("subway", ""),
        "nearby_html": output_paths.get("nearby", ""),
        "further_most_html": output_paths.get("further_most", ""),
        "further_most_nearby_html": output_paths.get("further_most_nearby", ""),
        "logs": combined_logs,
        "summary": dict(backend_result.get("summary") or {}),
        "excluded_stops": list(client_prep.get("excluded_stops") or []),
        "geocode_warnings": list(client_prep.get("geocode_warnings") or []),
        "elapsed_seconds": float(backend_result.get("elapsed_seconds", 0.0) or 0.0) + float(client_prep.get("elapsed_seconds", 0.0) or 0.0),
        "structured_results": structured_results,
        "cache_hit": False,
        "backend_elapsed_seconds": float(backend_result.get("elapsed_seconds", 0.0) or 0.0),
        "client_prep_elapsed_seconds": float(client_prep.get("elapsed_seconds", 0.0) or 0.0),
        "current_plan_assessment": backend_result.get("current_plan_assessment"),
        "current_plan_scenario": backend_result.get("current_plan_scenario"),
        "like_for_like_baseline": backend_result.get("like_for_like_baseline"),
        "current_plan_like_for_like_comparison": backend_result.get("current_plan_like_for_like_comparison"),
        "constrained_improvement_baseline": backend_result.get("constrained_improvement_baseline"),
        "current_plan_constrained_comparison": backend_result.get("current_plan_constrained_comparison"),
        "constrained_selected_moves": backend_result.get("constrained_selected_moves"),
        "constrained_package_summaries": backend_result.get("constrained_package_summaries"),
        "free_optimization_baseline": backend_result.get("free_optimization_baseline"),
        "current_plan_comparison": backend_result.get("current_plan_comparison"),
        "route_reallocation_analysis": backend_result.get("route_reallocation_analysis"),
        "nearby_private_access_analysis": backend_result.get("nearby_private_access_analysis"),
        "further_most_private_access_analysis": backend_result.get("further_most_private_access_analysis"),
        "traffic_profile_name": backend_result.get("traffic_profile_name"),
        "traffic_time_multiplier": backend_result.get("traffic_time_multiplier"),
        "traffic_profile_context": backend_result.get("traffic_profile_context"),
        "planner_config": dict(planner_config_snapshot),
        "job_id": str(backend_result.get("job_id", "") or ""),
    }


def format_job_option(job: dict[str, object]) -> str:
    job_id = str(job.get("job_id", "")).strip()
    status = str(job.get("status", "")).strip() or "unknown"
    owner_label = format_job_owner_label(job)
    metadata = dict(job.get("metadata") or {})
    job_name = str(metadata.get("job_name", "")).strip()
    source_label = str(metadata.get("source_label", "")).strip() or "Untitled job"
    return f"{job_id} | {status} | {owner_label} | {job_name or source_label}"


def format_job_owner_label(job: dict[str, object]) -> str:
    owner_email = str(job.get("owner_email", "") or "").strip()
    if not owner_email:
        return "legacy"
    return owner_email.split("@", 1)[0].strip() or owner_email


def build_job_display_name(source_label: str, custom_name: str = "") -> str:
    default_name = Path(str(source_label or "")).stem.strip() or "Untitled job"
    normalized_custom_name = " ".join(str(custom_name or "").strip().split())
    if not normalized_custom_name:
        return default_name
    return f"{default_name} - {normalized_custom_name}"


def render_localized_timestamp(label: str, utc_iso: str, *, key: str, height: int = 28) -> None:
    normalized_value = str(utc_iso or "").strip()
    if not normalized_value:
        st.caption(f"{label}: Unknown")
        return
    safe_label = label.replace("`", "").strip()
    safe_value = normalized_value.replace("`", "").strip()
    components.html(
        f"""
        <div style="font-size:0.875rem;color:rgb(49, 51, 63);font-family:var(--font, sans-serif);">
          <strong>{safe_label}:</strong>
          <span id="local-time-{key}">{safe_value}</span>
        </div>
        <script>
          (function() {{
            const raw = {safe_value!r};
            const target = document.getElementById("local-time-{key}");
            try {{
              const dt = new Date(raw);
              if (!Number.isNaN(dt.getTime())) {{
                const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "local";
                const formatted = dt.toLocaleString([], {{
                  year: "numeric",
                  month: "2-digit",
                  day: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                  hour12: false,
                  timeZoneName: "short"
                }});
                target.textContent = formatted + " (" + tz + ")";
              }}
            }} catch (err) {{
              target.textContent = raw;
            }}
          }})();
        </script>
        """,
        height=height,
    )


def build_scenario_snapshot(name: str, scenario: dict[str, object]) -> dict[str, object]:
    enabled = bool(scenario.get("enabled", True)) if scenario else False
    routes = list(scenario.get("routes") or [])
    avg_route_distance_km = float(scenario.get("avg_route_distance_m", 0.0) or 0.0) / 1000.0
    avg_route_duration_minutes = float(scenario.get("avg_route_duration_s", 0.0) or 0.0) / 60.0
    avg_load_factor_pct = (
        sum(compute_route_load_factor_pct(route) for route in routes) / len(routes)
        if routes
        else 0.0
    )
    return {
        "name": name,
        "enabled": enabled,
        "skipped_reason": str(scenario.get("skipped_reason", "") or ""),
        "route_count": int(scenario.get("bus_count", 0)),
        "stop_count": int(scenario.get("stop_count", 0)),
        "avg_route_distance_km": avg_route_distance_km,
        "avg_route_duration_minutes": avg_route_duration_minutes,
        "avg_load_factor_pct": avg_load_factor_pct,
        "bus_mix": dict(scenario.get("bus_mix", {})),
        "routes": routes,
    }


def build_route_table_rows(routes: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for route in routes:
        rows.append(
            {
                "route_id": route.get("route_id") or route.get("vehicle_id"),
                "bus_type": route.get("bus_type_name"),
                "stops": max(0, len(list(route.get("nodes") or [])) - 1),
                "passengers": int(route.get("load", 0) or 0),
                "capacity": int(route.get("bus_capacity", 0) or 0),
                "load_factor_pct": round(compute_route_load_factor_pct(route), 1),
                "distance_km": round(float(route.get("distance_m", 0.0)) / 1000.0, 2),
                "duration_min": round(float(route.get("time_s", 0.0)) / 60.0, 1),
            }
        )
    return rows


def build_baseline_template_workbook_bytes(
    scenario: dict[str, object],
    *,
    service_direction: str,
) -> bytes:
    points = [dict(item) for item in list(scenario.get("points") or [])]
    routes = [dict(item) for item in list(scenario.get("routes") or [])]
    point_by_node = {int(point.get("node_id", index)): point for index, point in enumerate(points)}
    assignment_rows: list[dict[str, object]] = []
    fleet_counts: dict[tuple[str, int], int] = {}

    for route_index, route in enumerate(routes, start=1):
        route_id = str(route.get("route_id") or route.get("vehicle_id") or route_index).strip()
        if not route_id.upper().startswith("R"):
            route_id = f"R{route_id}"
        bus_type = str(route.get("bus_type_name", "")).strip() or "Unknown"
        bus_capacity = int(route.get("bus_capacity", 0) or 0)
        fleet_counts[(bus_type, bus_capacity)] = fleet_counts.get((bus_type, bus_capacity), 0) + 1
        for stop_sequence, node_id in enumerate(list(route.get("nodes") or []), start=1):
            point = point_by_node.get(int(node_id), {})
            assignment_rows.append(
                {
                    "route_id": route_id,
                    "stop_sequence": stop_sequence,
                    "bus_type": bus_type,
                    "country": str(point.get("country", "")).strip(),
                    "city": str(point.get("city", "")).strip(),
                    "address": str(point.get("address", "")).strip(),
                    "passenger_count": int(point.get("passenger_count", 0) or 0),
                    "note": "Free optimization baseline export",
                }
            )

    fleet_rows = [
        {
            "bus_type": bus_type,
            "seat_count": seat_count,
            "vehicle_count": vehicle_count,
            "note": "Generated from free optimization baseline result",
        }
        for (bus_type, seat_count), vehicle_count in sorted(fleet_counts.items(), key=lambda item: (item[0][0], item[0][1]))
    ]
    notes_df = pd.DataFrame(
        {
            "section": ["Source", "Service Direction", "How to use"],
            "guidance": [
                "Generated from the Free Optimization Baseline result.",
                str(service_direction or "From School"),
                "This workbook uses the same sheet and column format as the current-plan input template and can be uploaded as a current plan for another audit run.",
            ],
        }
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(assignment_rows).to_excel(writer, index=False, sheet_name="current_plan_assignments")
        pd.DataFrame(fleet_rows).to_excel(writer, index=False, sheet_name="current_plan_fleet")
        notes_df.to_excel(writer, index=False, sheet_name="template_notes")
    return buffer.getvalue()


def render_free_baseline_template_download(
    free_optimization_baseline_result: dict[str, object],
    *,
    service_direction: str,
    key: str,
) -> None:
    if not list(free_optimization_baseline_result.get("routes") or []) or not list(free_optimization_baseline_result.get("points") or []):
        return
    st.download_button(
        "Download Free Optimization as Input Template (.xlsx)",
        data=build_baseline_template_workbook_bytes(
            free_optimization_baseline_result,
            service_direction=service_direction,
        ),
        file_name="free_optimization_baseline_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key=key,
    )


def build_current_route_table_rows(
    route_summaries: list[dict[str, object]],
    route_duration_limit_minutes: int,
) -> list[dict[str, object]]:
    effective_limit_minutes = effective_route_duration_limit_minutes(route_duration_limit_minutes)
    rows: list[dict[str, object]] = []
    for item in route_summaries:
        load_factor_pct = round(float(item.get("load_factor", 0.0)) * 100.0, 1)
        duration_min = round(float(item.get("duration_s", 0.0)) / 60.0, 1)
        health_flags: list[str] = []
        if load_factor_pct < 50:
            health_flags.append("Low load")
        if duration_min > effective_limit_minutes:
            health_flags.append("Overlong")
        if int(item.get("stop_count", 0) or 0) > 12:
            health_flags.append("Many stops")
        rows.append(
            {
                "route_id": item.get("route_id"),
                "bus_type": item.get("bus_type"),
                "stops": int(item.get("stop_count", 0) or 0),
                "passengers": int(item.get("passenger_count", 0) or 0),
                "load_factor_pct": load_factor_pct,
                "distance_km": round(float(item.get("distance_m", 0.0)) / 1000.0, 2),
                "duration_min": duration_min,
                "health_flags": ", ".join(health_flags) if health_flags else "Healthy",
            }
        )
    rows.sort(key=lambda row: (row["load_factor_pct"], -row["duration_min"]))
    return rows


def build_weak_route_table_rows(weak_routes: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in weak_routes:
        candidate_receiving_routes = list(item.get("candidate_receiving_routes") or [])
        receiving_summary = " | ".join(
            f"{candidate['route_id']} ({float(candidate.get('min_inter_route_distance_m', 0.0)) / 1000.0:.1f} km, {int(candidate.get('spare_seats', 0))} spare)"
            for candidate in candidate_receiving_routes[:3]
        )
        rows.append(
            {
                "route_id": item.get("route_id"),
                "bus_type": item.get("bus_type"),
                "stops": int(item.get("stop_count", 0) or 0),
                "passengers": int(item.get("passenger_count", 0) or 0),
                "load_factor_pct": round(float(item.get("load_factor_pct", 0.0) or 0.0), 1),
                "avg_route_distance_km": round(float(item.get("distance_km", 0.0) or 0.0), 2),
                "avg_route_duration_min": round(float(item.get("duration_min", 0.0) or 0.0), 1),
                "weakness_reasons": ", ".join(list(item.get("reasons") or [])),
                "candidate_receiving_routes": receiving_summary or "None",
            }
        )
    return rows


def build_reallocation_table_rows(recommendations: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in recommendations:
        decision_signal = str(item.get("route_action_label", "")).strip() or "Local improvement"
        rows.append(
            {
                "action": str(item.get("recommendation_type", "")).replace("_", " ").title(),
                "from_route": item.get("from_route_id"),
                "to_route": item.get("to_route_id"),
                "stops": ", ".join(list(item.get("addresses") or [])),
                "moved_passengers": int(item.get("moved_passenger_count", 0) or 0),
                "network_time_saving_min": round(float(item.get("network_total_duration_saving_s", 0.0) or 0.0) / 60.0, 1),
                "network_distance_saving_km": round(float(item.get("network_total_distance_saving_m", 0.0) or 0.0) / 1000.0, 2),
                "transfer_to_route_distance_km": round(float(item.get("transfer_to_route_min_distance_m", 0.0) or 0.0) / 1000.0, 2),
                "remaining_from_route_stops": int(item.get("remaining_from_route_stop_count", 0) or 0),
                "remaining_from_route_passengers": int(item.get("from_route_passenger_count_after", 0) or 0),
                "from_route_after_load_pct": round(float(item.get("from_load_factor_after", 0.0) or 0.0) * 100.0, 1),
                "to_route_after_load_pct": round(float(item.get("to_load_factor_after", 0.0) or 0.0) * 100.0, 1),
                "decision_signal": decision_signal,
                "explanation": item.get("explanation"),
            }
        )
    return rows


def build_route_opportunity_profile_rows(route_profiles: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in route_profiles:
        rows.append(
            {
                "route_id": item.get("route_id"),
                "decision_signal": item.get("route_action_label"),
                "best_receiving_route": item.get("best_to_route_id"),
                "supporting_moves": int(item.get("supporting_move_count", 0) or 0),
                "supporting_same_stage_moves": int(item.get("supporting_stage_move_count", 0) or 0),
                "best_time_saving_min": round(float(item.get("best_network_time_saving_s", 0.0) or 0.0) / 60.0, 1),
                "best_distance_saving_km": round(float(item.get("best_network_distance_saving_m", 0.0) or 0.0) / 1000.0, 2),
                "remaining_stops_if_best_move_applied": int(item.get("best_remaining_stop_count", 0) or 0),
                "remaining_passengers_if_best_move_applied": int(item.get("best_remaining_passenger_count", 0) or 0),
                "best_transfer_distance_km": round(float(item.get("best_transfer_to_route_distance_m", 0.0) or 0.0) / 1000.0, 2),
                "summary": item.get("best_explanation"),
            }
        )
    return rows


def build_private_access_rows(private_access_analysis: dict[str, object] | None) -> list[dict[str, object]]:
    analysis = dict(private_access_analysis or {})
    rows: list[dict[str, object]] = []
    for item in list(analysis.get("rows") or []):
        rows.append(
            {
                "stop_address": item.get("address"),
                "passengers": int(item.get("passenger_count", 0) or 0),
                "current_route": item.get("from_route_id"),
                "feasible_pickup_route": item.get("pickup_route_id"),
                "feasible_pickup_stop": item.get("pickup_address"),
                "private_drive_time_min": round(float(item.get("private_drive_time_s", 0.0) or 0.0) / 60.0, 1),
                "private_drive_distance_km": round(float(item.get("private_drive_distance_m", 0.0) or 0.0) / 1000.0, 2),
                "decision_signal": item.get("decision_signal"),
                "reason": item.get("reason"),
            }
        )
    return rows


def build_private_access_cluster_rows(private_access_analysis: dict[str, object] | None) -> list[dict[str, object]]:
    analysis = dict(private_access_analysis or {})
    rows: list[dict[str, object]] = []
    for item in list(analysis.get("clusters") or []):
        members = list(item.get("members") or [])
        member_preview = " | ".join(
            f"{str(member.get('address', '')).strip()} ({float(member.get('private_drive_time_s', 0.0) or 0.0) / 60.0:.1f} min, {float(member.get('private_drive_distance_m', 0.0) or 0.0) / 1000.0:.2f} km)"
            for member in members[:6]
        )
        rows.append(
            {
                "cluster_center_address": item.get("pickup_address"),
                "pickup_route": item.get("pickup_route_id"),
                "clustered_riders": int(item.get("clustered_rider_count", 0) or 0),
                "clustered_passengers": int(item.get("clustered_passenger_count", 0) or 0),
                "avg_private_drive_time_min": round(float(item.get("avg_private_drive_time_s", 0.0) or 0.0) / 60.0, 1),
                "max_private_drive_time_min": round(float(item.get("max_private_drive_time_s", 0.0) or 0.0) / 60.0, 1),
                "avg_private_drive_distance_km": round(float(item.get("avg_private_drive_distance_m", 0.0) or 0.0) / 1000.0, 2),
                "max_private_drive_distance_km": round(float(item.get("max_private_drive_distance_m", 0.0) or 0.0) / 1000.0, 2),
                "clustered_addresses": member_preview,
            }
        )
    return rows


def build_constrained_package_rows(
    selected_moves: list[dict[str, object]],
    package_summaries: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    package_summaries = list(package_summaries or [])
    if package_summaries:
        rows: list[dict[str, object]] = []
        for item in package_summaries:
            rows.append(
                {
                    "package_id": item.get("package_id"),
                    "from_route": item.get("from_route_id"),
                    "to_route": item.get("to_route_id"),
                    "moves_in_package": int(item.get("move_count", 0) or 0),
                    "stops_moved": int(item.get("moved_stop_count", 0) or 0),
                    "moved_passengers": int(item.get("moved_passenger_count", 0) or 0),
                    "package_time_saving_min": round(float(item.get("network_total_duration_saving_s", 0.0) or 0.0) / 60.0, 1),
                    "package_distance_saving_km": round(float(item.get("network_total_distance_saving_m", 0.0) or 0.0) / 1000.0, 2),
                    "package_signal": item.get("package_action_label"),
                    "strongest_move_signal": item.get("strongest_move_label"),
                    "remaining_from_route_stops": int(item.get("remaining_from_route_stop_count", 0) or 0),
                    "remaining_from_route_passengers": int(item.get("remaining_from_route_passenger_count", 0) or 0),
                    "projected_to_route_stops": int(item.get("projected_to_route_stop_count", 0) or 0),
                    "projected_to_route_passengers": int(item.get("projected_to_route_passenger_count", 0) or 0),
                    "projected_to_route_duration_min": round(float(item.get("projected_to_route_duration_s", 0.0) or 0.0) / 60.0, 1),
                    "projected_to_route_distance_km": round(float(item.get("projected_to_route_distance_m", 0.0) or 0.0) / 1000.0, 2),
                    "projected_to_route_load_pct": round(float(item.get("projected_to_route_load_factor", 0.0) or 0.0) * 100.0, 1),
                    "stops": ", ".join(str(addr).strip() for addr in list(item.get("addresses") or []) if str(addr).strip()),
                    "summary": item.get("package_summary"),
                }
            )
        return rows

    grouped: dict[str, list[dict[str, object]]] = {}
    for item in selected_moves:
        package_id = str(item.get("constrained_package_id", "")).strip() or "P?"
        grouped.setdefault(package_id, []).append(item)

    rows: list[dict[str, object]] = []
    for package_id, items in sorted(grouped.items()):
        first = items[0]
        all_addresses: list[str] = []
        total_passengers = 0
        total_time_saving_s = 0.0
        total_distance_saving_m = 0.0
        for item in items:
            all_addresses.extend(str(addr).strip() for addr in list(item.get("addresses") or []) if str(addr).strip())
            total_passengers += int(item.get("moved_passenger_count", 0) or 0)
            total_time_saving_s += float(item.get("network_total_duration_saving_s", 0.0) or 0.0)
            total_distance_saving_m += float(item.get("network_total_distance_saving_m", 0.0) or 0.0)
        rows.append(
            {
                "package_id": package_id,
                "from_route": first.get("from_route_id"),
                "to_route": first.get("to_route_id"),
                "moves_in_package": len(items),
                "stops_moved": len(all_addresses),
                "moved_passengers": total_passengers,
                "package_time_saving_min": round(total_time_saving_s / 60.0, 1),
                "package_distance_saving_km": round(total_distance_saving_m / 1000.0, 2),
                "decision_signal": first.get("route_action_label"),
                "stops": ", ".join(all_addresses),
            }
        )
    return rows


def build_constrained_package_outcome_rows(
    package_summaries: list[dict[str, object]] | None,
    route_duration_limit_minutes: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in list(package_summaries or []):
        projected_to_duration_min = round(float(item.get("projected_to_route_duration_s", 0.0) or 0.0) / 60.0, 1)
        projected_to_load_pct = round(float(item.get("projected_to_route_load_factor", 0.0) or 0.0) * 100.0, 1)
        package_signal = str(item.get("package_action_label", "")).strip() or "Local improvement"
        merge_readiness = classify_package_merge_readiness(item, route_duration_limit_minutes)
        rows.append(
            {
                "package_id": item.get("package_id"),
                "decision_signal": package_signal,
                "merge_readiness": merge_readiness,
                "from_route": item.get("from_route_id"),
                "from_stops_before": int(item.get("original_from_route_stop_count", 0) or 0),
                "from_stops_after": int(item.get("remaining_from_route_stop_count", 0) or 0),
                "from_passengers_before": int(item.get("original_from_route_passenger_count", 0) or 0),
                "from_passengers_after": int(item.get("remaining_from_route_passenger_count", 0) or 0),
                "from_duration_before_min": round(float(item.get("original_from_route_duration_s", 0.0) or 0.0) / 60.0, 1),
                "from_duration_after_min": round(float(item.get("projected_from_route_duration_s", 0.0) or 0.0) / 60.0, 1),
                "from_load_before_pct": round(float(item.get("original_from_route_load_factor", 0.0) or 0.0) * 100.0, 1),
                "from_load_after_pct": round(float(item.get("projected_from_route_load_factor", 0.0) or 0.0) * 100.0, 1),
                "to_route": item.get("to_route_id"),
                "to_stops_before": int(item.get("original_to_route_stop_count", 0) or 0),
                "to_stops_after": int(item.get("projected_to_route_stop_count", 0) or 0),
                "to_passengers_before": int(item.get("original_to_route_passenger_count", 0) or 0),
                "to_passengers_after": int(item.get("projected_to_route_passenger_count", 0) or 0),
                "to_duration_before_min": round(float(item.get("original_to_route_duration_s", 0.0) or 0.0) / 60.0, 1),
                "to_duration_after_min": projected_to_duration_min,
                "to_load_before_pct": round(float(item.get("original_to_route_load_factor", 0.0) or 0.0) * 100.0, 1),
                "to_load_after_pct": projected_to_load_pct,
                "summary": item.get("package_summary"),
            }
        )
    return rows


def classify_package_merge_readiness(
    package_summary: dict[str, object],
    route_duration_limit_minutes: int,
) -> str:
    projected_to_duration_min = float(package_summary.get("projected_to_route_duration_s", 0.0) or 0.0) / 60.0
    projected_to_load_pct = float(package_summary.get("projected_to_route_load_factor", 0.0) or 0.0) * 100.0
    effective_limit_minutes = effective_route_duration_limit_minutes(route_duration_limit_minutes)
    if projected_to_duration_min <= effective_limit_minutes and projected_to_load_pct <= 85.0:
        return "Safe merge candidate"
    if projected_to_duration_min <= effective_limit_minutes + 10 and projected_to_load_pct <= 92.0:
        return "Monitor receiving route"
    return "Receiving route stressed"


def summarize_package_merge_readiness(
    package_summaries: list[dict[str, object]] | None,
    route_duration_limit_minutes: int,
) -> dict[str, int]:
    summary = {
        "Safe merge candidate": 0,
        "Monitor receiving route": 0,
        "Receiving route stressed": 0,
    }
    for item in list(package_summaries or []):
        summary[classify_package_merge_readiness(item, route_duration_limit_minutes)] += 1
    return summary


def build_executive_findings(
    summary: dict[str, object],
    current_plan_assessment: dict[str, object],
    like_for_like_comparison: dict[str, object],
    constrained_comparison: dict[str, object],
    constrained_package_summaries: list[dict[str, object]],
    current_plan_comparison: dict[str, object],
    route_reallocation_analysis: dict[str, object],
    scenario_snapshots: list[dict[str, object]],
    geocode_warning_count: int,
    route_duration_limit_minutes: int,
) -> list[str]:
    findings: list[str] = []
    effective_limit_minutes = effective_route_duration_limit_minutes(route_duration_limit_minutes)
    if like_for_like_comparison:
        avg_distance_gap_pct = float(like_for_like_comparison.get("avg_distance_gap_pct", 0.0) or 0.0)
        if avg_distance_gap_pct > 5:
            findings.append(
                f"Without changing route count or bus mix, stop-order optimization alone can reduce average route distance by {avg_distance_gap_pct:.1f}%."
            )
        avg_duration_gap_pct = float(like_for_like_comparison.get("avg_duration_gap_pct", 0.0) or 0.0)
        if avg_duration_gap_pct > 5:
            findings.append(
                f"Without changing route count or bus mix, stop-order optimization alone can reduce average route time by {avg_duration_gap_pct:.1f}%."
            )
    if constrained_comparison:
        avg_distance_gap_pct = float(constrained_comparison.get("avg_distance_gap_pct", 0.0) or 0.0)
        if avg_distance_gap_pct > 5:
            findings.append(
                f"Limited route-to-route reallocations can reduce average route distance by {avg_distance_gap_pct:.1f}% without a full network redesign."
            )
        avg_duration_gap_pct = float(constrained_comparison.get("avg_duration_gap_pct", 0.0) or 0.0)
        if avg_duration_gap_pct > 5:
            findings.append(
                f"Limited route-to-route reallocations can reduce average route time by {avg_duration_gap_pct:.1f}% without a full network redesign."
            )
    constrained_package_summaries = list(constrained_package_summaries or [])
    if constrained_package_summaries:
        readiness_summary = summarize_package_merge_readiness(
            constrained_package_summaries,
            route_duration_limit_minutes,
        )
        removable_now = [
            item for item in constrained_package_summaries
            if bool(item.get("route_eliminated"))
        ]
        removal_paths = [
            item for item in constrained_package_summaries
            if not bool(item.get("route_eliminated")) and bool(item.get("route_removal_candidate"))
        ]
        consolidation_paths = [
            item for item in constrained_package_summaries
            if not bool(item.get("route_eliminated"))
            and not bool(item.get("route_removal_candidate"))
            and bool(item.get("route_consolidation_candidate"))
        ]
        if removable_now:
            findings.append(
                f"{len(removable_now)} constrained transfer package(s) now empty a route completely, creating immediate route-removal candidates."
            )
        elif removal_paths:
            findings.append(
                f"{len(removal_paths)} constrained transfer package(s) now leave a route with very limited residual demand, creating a strong removal path."
            )
        elif consolidation_paths:
            findings.append(
                f"{len(consolidation_paths)} constrained transfer package(s) now move a route materially closer to consolidation."
            )
        top_package = constrained_package_summaries[0]
        findings.append(
            f"The top constrained package moves {int(top_package.get('moved_stop_count', 0) or 0)} stop(s) from "
            f"{str(top_package.get('from_route_id', '')).strip()} to {str(top_package.get('to_route_id', '')).strip()} "
            f"and is judged as {str(top_package.get('package_action_label', '')).strip().lower()} after the full package is applied."
        )
        safe_merge_count = int(readiness_summary.get("Safe merge candidate", 0) or 0)
        monitor_count = int(readiness_summary.get("Monitor receiving route", 0) or 0)
        stressed_count = int(readiness_summary.get("Receiving route stressed", 0) or 0)
        if safe_merge_count > 0:
            findings.append(
                f"{safe_merge_count} constrained package(s) currently look like clean merge candidates because the receiving route stays within the target duration plus the 10-minute operating buffer and a comfortable load range."
            )
        elif monitor_count > 0:
            findings.append(
                f"{monitor_count} constrained package(s) are feasible but still need monitoring because the receiving route becomes tight after absorption."
            )
        elif stressed_count > 0:
            findings.append(
                f"Current constrained packages still push the receiving route too hard, so the merge ideas need another iteration before they are clean operating moves."
            )
        projected_to_duration_min = float(top_package.get("projected_to_route_duration_s", 0.0) or 0.0) / 60.0
        projected_to_load_pct = float(top_package.get("projected_to_route_load_factor", 0.0) or 0.0) * 100.0
        if projected_to_duration_min <= effective_limit_minutes and projected_to_load_pct <= 85.0:
            findings.append(
                f"The receiving route in the top package still stays within a comfortable operating range at about {projected_to_duration_min:.1f} minutes and {projected_to_load_pct:.1f}% load."
            )
        elif projected_to_duration_min <= effective_limit_minutes + 10 and projected_to_load_pct <= 92.0:
            findings.append(
                f"The receiving route in the top package remains feasible but should be watched closely because it rises to about {projected_to_duration_min:.1f} minutes and {projected_to_load_pct:.1f}% load."
            )
        else:
            findings.append(
                f"The receiving route in the top package becomes stressed at about {projected_to_duration_min:.1f} minutes and {projected_to_load_pct:.1f}% load, so this is not yet a clean merge."
            )
    if current_plan_comparison:
        route_gap = int(current_plan_comparison.get("route_gap", 0) or 0)
        if route_gap > 0:
            findings.append(
                f"The imported current plan uses {route_gap} more routes than the system baseline."
            )
        avg_distance_gap_pct = float(current_plan_comparison.get("avg_distance_gap_pct", 0.0) or 0.0)
        if avg_distance_gap_pct > 5:
            findings.append(
                f"Current-plan average route distance is {avg_distance_gap_pct:.1f}% above the system baseline."
            )
        avg_duration_gap_pct = float(current_plan_comparison.get("avg_duration_gap_pct", 0.0) or 0.0)
        if avg_duration_gap_pct > 5:
            findings.append(
                f"Current-plan average route time is {avg_duration_gap_pct:.1f}% above the system baseline."
            )
    subway_stop_reduction = int(summary.get("stop_reduction", 0) or 0)
    nearby_stop_reduction = int(summary.get("nearby_stop_reduction", 0) or 0)
    if subway_stop_reduction > 0:
        findings.append(
            f"Subway aggregation reduces stop count by {subway_stop_reduction} compared with the original stop list."
        )
    if nearby_stop_reduction > 0:
        findings.append(
            f"Nearby-address clustering reduces stop count by {nearby_stop_reduction} compared with the original stop list."
        )
    if current_plan_assessment:
        low_load_routes = int(current_plan_assessment.get("low_load_route_count", 0) or 0)
        if low_load_routes > 0:
            findings.append(
                f"The current plan contains {low_load_routes} route(s) below 50% load, which suggests oversized vehicles or over-splitting."
            )
    if route_reallocation_analysis:
        reallocation_summary = dict(route_reallocation_analysis.get("summary") or {})
        route_removable_now_count = int(reallocation_summary.get("route_removable_now_count", 0) or 0)
        route_removal_candidate_count = int(reallocation_summary.get("route_removal_candidate_count", 0) or 0)
        route_consolidation_candidate_count = int(reallocation_summary.get("route_consolidation_candidate_count", 0) or 0)
        actionable_weak_route_count = int(reallocation_summary.get("actionable_weak_route_count", 0) or 0)
        if route_removable_now_count > 0:
            findings.append(
                f"{route_removable_now_count} current route(s) can be emptied by a feasible local transfer and are now candidates for immediate removal."
            )
        elif route_removal_candidate_count > 0:
            findings.append(
                f"{route_removal_candidate_count} current route(s) now have a credible path toward removal through local route-to-route transfers."
            )
        elif route_consolidation_candidate_count > 0:
            findings.append(
                f"{route_consolidation_candidate_count} current route(s) can be pushed closer to consolidation through local stop transfers."
            )
        elif actionable_weak_route_count > 0:
            findings.append(
                f"{actionable_weak_route_count} weak route(s) have at least one actionable local reallocation idea."
            )
        top_recommendation = next(iter(list(reallocation_summary.get("priority_recommendations") or [])), None)
        if top_recommendation is None:
            top_recommendation = next(iter(list(route_reallocation_analysis.get("recommendations") or [])), None)
        if top_recommendation:
            findings.append(
                f"Top reallocation idea: move {int(top_recommendation.get('stop_count', 0) or 0)} stop(s) from "
                f"{top_recommendation.get('from_route_id')} to {top_recommendation.get('to_route_id')} "
                f"to save about {float(top_recommendation.get('network_total_duration_saving_s', 0.0) or 0.0) / 60.0:.1f} minutes of network operating time."
            )
    if geocode_warning_count > 0:
        findings.append(
            f"{geocode_warning_count} address(es) still need cleanup because they could not be geocoded."
        )
    if not findings and scenario_snapshots:
        best = sorted(
            scenario_snapshots,
            key=lambda item: (int(item["route_count"]), float(item["distance_km"]), float(item["duration_minutes"])),
        )[0]
        findings.append(
            f"{best['name']} is currently the leanest baseline scenario by route count and network effort."
        )
    return findings


def render_ai_audit_report_panel(
    *,
    job_detail: dict[str, object] | None,
    selected_job_id: str,
    current_user_email: str,
    executive_findings: list[str],
    current_plan_assessment: dict[str, object],
    current_plan_comparison: dict[str, object],
    route_reallocation_analysis: dict[str, object],
    scenario_snapshots: list[dict[str, object]],
) -> None:
    st.subheader("AI Audit Report")
    st.caption(
        "AI uses the deterministic audit outputs only: route metrics, baseline comparisons, and recommendation summaries. "
        "Full address lists are excluded from the prompt."
    )
    st.markdown("**Audit Briefing Board**")
    reallocation_summary = dict(route_reallocation_analysis.get("summary") or {})
    current_route_count = int(
        (current_plan_assessment or {}).get("route_count")
        or current_plan_comparison.get("current_route_count")
        or 0
    )
    current_avg_duration = float((current_plan_assessment or {}).get("avg_route_duration_minutes", 0.0) or 0.0)
    current_avg_load = float((current_plan_assessment or {}).get("avg_load_factor_pct", 0.0) or 0.0)
    route_gap = int(current_plan_comparison.get("route_gap", 0) or 0)
    action_count = int(reallocation_summary.get("actionable_weak_route_count", 0) or 0)
    briefing_cols = st.columns(4)
    briefing_cols[0].metric("Current Routes", current_route_count or "N/A", delta=f"{route_gap:+d} vs baseline" if route_gap else None)
    briefing_cols[1].metric("Average Load", f"{current_avg_load:.1f}%" if current_avg_load else "N/A")
    briefing_cols[2].metric("Average Route Time", format_duration_minutes(current_avg_duration) if current_avg_duration else "N/A")
    briefing_cols[3].metric("Action Signals", action_count)

    scenario_rows = []
    for snapshot in scenario_snapshots:
        if not bool(snapshot.get("enabled")):
            continue
        scenario_rows.append(
            {
                "Scenario": str(snapshot.get("name", "")),
                "Routes": int(snapshot.get("route_count", 0) or 0),
                "Avg Time (min)": round(float(snapshot.get("avg_route_duration_minutes", 0.0) or 0.0), 1),
                "Avg Distance (km)": round(float(snapshot.get("avg_route_distance_km", 0.0) or 0.0), 1),
                "Avg Load (%)": round(float(snapshot.get("avg_load_factor_pct", 0.0) or 0.0), 1),
            }
        )
    route_rows = []
    for route in list((current_plan_assessment or {}).get("route_summaries") or []):
        route = dict(route)
        duration_min = round(float(route.get("duration_s", 0.0) or 0.0) / 60.0, 1)
        load_pct = round(float(route.get("load_factor", 0.0) or 0.0) * 100.0, 1)
        risk_reasons = []
        if duration_min >= 70:
            risk_reasons.append("long ride")
        elif duration_min >= 60:
            risk_reasons.append("near duration limit")
        if load_pct < 50:
            risk_reasons.append("low utilization")
        elif load_pct >= 90:
            risk_reasons.append("very full")
        if not risk_reasons:
            risk_reasons.append("balanced")
        route_rows.append(
            {
                "Route": str(route.get("route_id", "") or "Unknown"),
                "Duration": f"{duration_min:.1f} min",
                "Load": f"{load_pct:.1f}%",
                "Passengers": int(route.get("passenger_count", 0) or 0),
                "What to notice": ", ".join(risk_reasons),
                "_score": (3 if "long ride" in risk_reasons else 0)
                + (2 if "low utilization" in risk_reasons else 0)
                + (2 if "very full" in risk_reasons else 0)
                + (1 if "near duration limit" in risk_reasons else 0),
            }
        )
    action_rows = []
    priority_actions = list(
        reallocation_summary.get("priority_recommendations")
        or route_reallocation_analysis.get("recommendations")
        or []
    )
    for index, item in enumerate(priority_actions[:8], start=1):
        item = dict(item)
        from_route = str(item.get("from_route_id", "") or "N/A")
        to_route = str(item.get("to_route_id", "") or "N/A")
        stop_count = int(item.get("stop_count", 0) or 0)
        action_text = f"Move {stop_count} stop(s) from {from_route} to {to_route}"
        time_saving_min = round(float(item.get("network_total_duration_saving_s", 0.0) or 0.0) / 60.0, 1)
        distance_saving_km = round(float(item.get("network_total_distance_saving_m", 0.0) or 0.0) / 1000.0, 1)
        action_rows.append(
            {
                "Suggested action": action_text,
                "Why it matters": f"Save about {time_saving_min:.1f} min and {distance_saving_km:.1f} km",
                "Operational meaning": str(item.get("route_action_label") or item.get("recommendation_type") or "Local improvement").replace("_", " ").title(),
            }
        )
    visual_col_1, visual_col_2 = st.columns([1.1, 1.0])
    with visual_col_1:
        if route_rows:
            st.markdown("**Routes to Review First**")
            st.caption(
                "Start here to see which current routes look inefficient or operationally tight."
            )
            risk_df = pd.DataFrame(sorted(route_rows, key=lambda item: int(item["_score"]), reverse=True))
            st.dataframe(
                risk_df.drop(columns=["_score"]).head(8),
                width="stretch",
                hide_index=True,
            )
            st.caption("Rows are sorted by review priority: long rides, low utilization, or unusually full routes rise to the top.")
        elif scenario_rows:
            st.markdown("**Scenario Comparison**")
            st.caption("Compare the current plan against the generated baselines.")
            st.dataframe(pd.DataFrame(scenario_rows), width="stretch", hide_index=True)
    with visual_col_2:
        if action_rows:
            st.markdown("**Top Suggested Actions**")
            st.caption(
                "These are small route-to-route changes the system thinks are worth checking before any larger redesign."
            )
            st.dataframe(pd.DataFrame(action_rows).head(5), width="stretch", hide_index=True)
        elif scenario_rows:
            st.markdown("**Scenario Comparison**")
            st.caption("Compare the current plan against the generated baselines.")
            st.dataframe(pd.DataFrame(scenario_rows), width="stretch", hide_index=True)
    if scenario_rows and route_rows and action_rows:
        with st.expander("Scenario Comparison Table", expanded=False):
            st.dataframe(pd.DataFrame(scenario_rows), width="stretch", hide_index=True)

    signal_items = []
    if int((current_plan_assessment or {}).get("low_load_route_count", 0) or 0) > 0:
        signal_items.append(("Low-load routes", str((current_plan_assessment or {}).get("low_load_route_count"))))
    if int((current_plan_assessment or {}).get("overlong_route_count", 0) or 0) > 0:
        signal_items.append(("Overlong routes", str((current_plan_assessment or {}).get("overlong_route_count"))))
    if int(reallocation_summary.get("route_removal_candidate_count", 0) or 0) > 0:
        signal_items.append(("Removal paths", str(reallocation_summary.get("route_removal_candidate_count"))))
    if int(reallocation_summary.get("route_consolidation_candidate_count", 0) or 0) > 0:
        signal_items.append(("Consolidation paths", str(reallocation_summary.get("route_consolidation_candidate_count"))))
    if signal_items:
        chip_html = " ".join(
            f"<span style='display:inline-block;margin:0 8px 8px 0;padding:6px 10px;border:1px solid #ccd6e0;border-radius:999px;background:#f7f9fb;font-size:0.88rem;'><strong>{html_escape(label)}</strong>: {html_escape(value)}</span>"
            for label, value in signal_items
        )
        st.markdown(chip_html, unsafe_allow_html=True)

    st.divider()
    report = dict((job_detail or {}).get("ai_audit_report") or {})
    ai_audit_status = str((job_detail or {}).get("ai_audit_status", "") or "").strip().lower()
    ai_audit_running = ai_audit_status == "running"
    has_ai_report = bool(report.get("report_markdown"))
    button_col_1, button_col_2 = st.columns([1.4, 1])
    with button_col_1:
        generate_clicked = st.button(
            "Generate AI Audit Report",
            type="primary",
            width="stretch",
            disabled=not bool(selected_job_id) or ai_audit_running or has_ai_report,
        )
    with button_col_2:
        regenerate_clicked = st.button(
            "Regenerate",
            width="stretch",
            disabled=not bool(selected_job_id) or ai_audit_running,
        )
    if ai_audit_running:
        st.info("AI audit generation is already running for this job. Refresh Job History in a moment before trying again.")
    elif has_ai_report:
        st.caption("An AI audit report already exists. Use `Regenerate` only when you intentionally want a fresh API call.")
    if generate_clicked or regenerate_clicked:
        try:
            with st.spinner("Generating AI audit report..."):
                report = generate_backend_ai_audit(
                    BACKEND_BASE_URL,
                    selected_job_id,
                    user_email=current_user_email,
                    force=bool(regenerate_clicked),
                    language="English",
                )
            st.success("AI audit report generated.")
            st.rerun()
        except Exception as exc:
            st.error(f"AI audit generation failed: {friendly_error_message(exc)}")

    if report.get("report_markdown"):
        st.markdown(str(report.get("report_markdown") or ""))
        st.caption(
            f"Generated: {report.get('generated_at', 'unknown')} | "
            f"Model: {report.get('model', 'unknown')} | "
            f"Input policy: {report.get('input_policy', 'aggregated facts only')}"
        )
        st.download_button(
            "Download Printable AI Report (.html)",
            data=build_ai_audit_report_html(
                report,
                job_id=selected_job_id,
                current_plan_assessment=current_plan_assessment,
                current_plan_comparison=current_plan_comparison,
                route_reallocation_analysis=route_reallocation_analysis,
                scenario_snapshots=scenario_snapshots,
            ),
            file_name=f"ai_audit_report_{selected_job_id}.html",
            mime="text/html",
            use_container_width=True,
        )
    else:
        st.info("No AI audit report has been generated for this job yet.")
        if executive_findings:
            st.markdown("**Deterministic findings available for AI generation**")
            st.markdown("\n".join(f"- {item}" for item in executive_findings[:6]))

    evidence_1, evidence_2, evidence_3, evidence_4 = st.columns(4)
    if current_plan_assessment:
        evidence_1.metric("Current Routes", current_plan_assessment["route_count"])
        evidence_2.metric("Average Load", f"{current_plan_assessment['avg_load_factor_pct']:.1f}%")
        evidence_3.metric("Low-Load Routes", current_plan_assessment["low_load_route_count"])
        evidence_4.metric("Overlong Routes", current_plan_assessment["overlong_route_count"])
    elif current_plan_comparison:
        evidence_1.metric("Current Routes", int(current_plan_comparison.get("current_route_count", 0) or 0))
        evidence_2.metric("Route Gap", int(current_plan_comparison.get("route_gap", 0) or 0))
    if route_reallocation_analysis:
        reallocation_summary = dict(route_reallocation_analysis.get("summary") or {})
        st.caption(
            "Top local action signal: "
            f"{int(reallocation_summary.get('actionable_weak_route_count', 0) or 0)} actionable weak route(s), "
            f"{int(reallocation_summary.get('route_removal_candidate_count', 0) or 0)} removal path(s), "
            f"{int(reallocation_summary.get('route_consolidation_candidate_count', 0) or 0)} consolidation path(s)."
        )


PLANNER_STEP_PATTERNS = [
    ("[CLIENT] Preparing client-side data", "Preparing client-side data"),
    ("[CLIENT] Sending prepared data to backend", "Submitting prepared data to backend"),
    ("[CLIENT] Rendering returned route maps", "Rendering returned route maps"),
    ("Valid stops:", "Geocoding completed"),
    ("Subway aggregation reduced stops", "Aggregating nearby subway stops"),
    ("Nearby-address aggregation reduced stops", "Aggregating nearby addresses"),
]


def infer_planner_step(log_line: str) -> str | None:
    for pattern, label in PLANNER_STEP_PATTERNS:
        if pattern in log_line:
            return label
    return None


def render_planner_status(
    container,
    current_step: str,
    total_elapsed: float,
    step_history: list[dict[str, float | str]],
    recent_logs: list[str],
) -> None:
    with container.container():
        st.markdown("**Planner Status**")
        st.info(f"Current step: {current_step} | Total elapsed: {total_elapsed:.1f}s")
        if step_history:
            lines = []
            for item in step_history[-8:]:
                lines.append(
                    f"- {item['step']}: +{float(item['step_elapsed']):.1f}s, total {float(item['total_elapsed']):.1f}s"
                )
            st.markdown("\n".join(lines))
        if recent_logs:
            st.caption("Recent log lines")
            st.code("\n".join(recent_logs[-8:]), language="text")


def render_html_preview(path: Path, height: int = 720) -> None:
    components.html(path.read_text(encoding="utf-8"), height=height, scrolling=True)


def build_audit_report_bytes(audit_sheets: list[tuple[str, pd.DataFrame]]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_name, df in audit_sheets:
            if df.empty:
                continue
            safe_name = sheet_name[:31]
            df.to_excel(writer, index=False, sheet_name=safe_name)
    buffer.seek(0)
    return buffer.getvalue()


def _pdf_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_pdf_line(text: str, width: int = 92) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return [""]
    return textwrap.wrap(raw, width=width, break_long_words=False, break_on_hyphens=False) or [raw]


def build_current_plan_audit_outline(result: dict[str, object]) -> dict[str, object]:
    current_plan_assessment = summarize_current_plan_assessment(result.get("current_plan_assessment"))
    if not current_plan_assessment:
        return {"title": "Current Plan Audit", "sections": []}

    planner_config_used = dict(result.get("planner_config") or {})
    route_duration_limit_minutes = int(planner_config_used.get("max_route_duration_minutes", 60))
    effective_limit_minutes = effective_route_duration_limit_minutes(route_duration_limit_minutes)
    route_reallocation_analysis = dict(result.get("route_reallocation_analysis") or {})
    reallocation_summary = dict(route_reallocation_analysis.get("summary") or {})
    priority_recommendations = list(
        reallocation_summary.get("priority_recommendations")
        or route_reallocation_analysis.get("recommendations")
        or []
    )
    like_for_like_comparison = dict(result.get("current_plan_like_for_like_comparison") or {})
    constrained_comparison = dict(result.get("current_plan_constrained_comparison") or {})
    current_plan_comparison = dict(result.get("current_plan_comparison") or {})
    constrained_package_summaries = list(result.get("constrained_package_summaries") or [])

    overview: list[str] = [
        f"The current network runs {int(current_plan_assessment.get('route_count', 0) or 0)} routes covering "
        f"{int(current_plan_assessment.get('stop_count', 0) or 0)} service stops, with an average route length of "
        f"{format_distance_km(float(current_plan_assessment.get('avg_route_distance_km', 0.0) or 0.0))} and an average route time of "
        f"{format_duration_minutes(float(current_plan_assessment.get('avg_route_duration_minutes', 0.0) or 0.0))}.",
        f"Average load is {float(current_plan_assessment.get('avg_load_factor_pct', 0.0) or 0.0):.1f}%. "
        f"{int(current_plan_assessment.get('low_load_route_count', 0) or 0)} routes are below the low-load threshold, and "
        f"{int(current_plan_assessment.get('overlong_route_count', 0) or 0)} routes exceed the effective route-duration threshold of "
        f"{effective_limit_minutes} minutes.",
    ]
    if current_plan_assessment.get("recommendations"):
        overview.extend(str(item).strip() for item in list(current_plan_assessment.get("recommendations") or [])[:3] if str(item).strip())

    improvement_paths: list[str] = []
    if like_for_like_comparison:
        avg_duration_gap_pct = float(like_for_like_comparison.get("avg_duration_gap_pct", 0.0) or 0.0)
        avg_distance_gap_pct = float(like_for_like_comparison.get("avg_distance_gap_pct", 0.0) or 0.0)
        improvement_paths.append(
            f"Like-for-like optimization reduces average route distance by {avg_distance_gap_pct:.1f}% and average route time by {avg_duration_gap_pct:.1f}% without changing route count or bus mix."
        )
    if constrained_comparison:
        avg_duration_gap_pct = float(constrained_comparison.get("avg_duration_gap_pct", 0.0) or 0.0)
        avg_distance_gap_pct = float(constrained_comparison.get("avg_distance_gap_pct", 0.0) or 0.0)
        improvement_paths.append(
            f"Constrained improvement reduces average route distance by {avg_distance_gap_pct:.1f}% and average route time by {avg_duration_gap_pct:.1f}% through a small set of plausible route-to-route transfers."
        )
    if current_plan_comparison:
        avg_duration_gap_pct = float(current_plan_comparison.get("avg_duration_gap_pct", 0.0) or 0.0)
        avg_distance_gap_pct = float(current_plan_comparison.get("avg_distance_gap_pct", 0.0) or 0.0)
        improvement_paths.append(
            f"Free optimization remains the theoretical upper bound, with a {avg_distance_gap_pct:.1f}% average-distance gap and a {avg_duration_gap_pct:.1f}% average-time gap versus the current plan."
        )

    priority_actions: list[str] = []
    for item in priority_recommendations[:3]:
        explanation = str(item.get("explanation", "")).strip()
        label = str(item.get("route_action_label", "")).strip() or "Local improvement"
        time_saving = float(item.get("network_total_duration_saving_s", 0.0) or 0.0) / 60.0
        distance_saving = float(item.get("network_total_distance_saving_m", 0.0) or 0.0) / 1000.0
        if explanation:
            priority_actions.append(
                f"[{label}] {explanation} Estimated saving: {time_saving:.1f} min and {distance_saving:.1f} km."
            )
    for item in constrained_package_summaries[:2]:
        summary = str(item.get("package_summary", "")).strip()
        if summary:
            priority_actions.append(summary)

    technical_snapshot: list[str] = []
    if route_reallocation_analysis:
        technical_snapshot.append(
            f"Route reallocation review found {int(reallocation_summary.get('actionable_weak_route_count', 0) or 0)} actionable weak routes, "
            f"{int(reallocation_summary.get('route_removal_candidate_count', 0) or 0)} removal paths, and "
            f"{int(reallocation_summary.get('route_consolidation_candidate_count', 0) or 0)} consolidation paths."
        )
    if constrained_package_summaries:
        merge_summary = summarize_package_merge_readiness(constrained_package_summaries, route_duration_limit_minutes)
        technical_snapshot.append(
            f"Selected constrained packages: {len(constrained_package_summaries)} total, including "
            f"{int(merge_summary.get('Safe merge candidate', 0) or 0)} safe merge candidates, "
            f"{int(merge_summary.get('Monitor receiving route', 0) or 0)} monitor cases, and "
            f"{int(merge_summary.get('Receiving route stressed', 0) or 0)} stressed receiving routes."
        )

    return {
        "title": "Current Plan Audit Report",
        "sections": [
            {"heading": "Overview", "items": overview},
            {"heading": "Improvement Paths", "items": improvement_paths or ["No baseline comparison findings were generated for this job."]},
            {"heading": "Priority Actions", "items": priority_actions or ["No route-to-route actions met the current filters."]},
            {"heading": "Technical Snapshot", "items": technical_snapshot or ["No additional technical findings were generated."]},
        ],
    }


def build_current_plan_audit_pdf_bytes(result: dict[str, object]) -> bytes:
    outline = build_current_plan_audit_outline(result)
    title = str(outline.get("title") or "Current Plan Audit Report")
    sections = list(outline.get("sections") or [])

    lines: list[str] = [title, ""]
    for section in sections:
        heading = str(section.get("heading") or "").strip()
        items = list(section.get("items") or [])
        if heading:
            lines.append(heading.upper())
        for item in items:
            wrapped = _wrap_pdf_line(f"- {str(item).strip()}", width=88)
            lines.extend(wrapped)
        lines.append("")

    max_lines_per_page = 46
    paged_lines = [lines[index:index + max_lines_per_page] for index in range(0, len(lines), max_lines_per_page)] or [["Current Plan Audit Report"]]

    font_object_number = 3
    objects: list[bytes] = []

    page_object_numbers: list[int] = []
    content_object_numbers: list[int] = []

    next_object_number = 4
    for _page in paged_lines:
        page_object_numbers.append(next_object_number)
        next_object_number += 1
        content_object_numbers.append(next_object_number)
        next_object_number += 1

    kids_refs = " ".join(f"{number} 0 R" for number in page_object_numbers)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Count {len(page_object_numbers)} /Kids [{kids_refs}] >>".encode("latin-1"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for page_index, page_lines in enumerate(paged_lines):
        content_lines = ["BT", "/F1 11 Tf", "50 790 Td", "14 TL"]
        for line in page_lines:
            safe_line = _pdf_escape(line)
            content_lines.append(f"({safe_line}) Tj")
            content_lines.append("T*")
        content_lines.append("ET")
        content_stream = "\n".join(content_lines).encode("latin-1", errors="replace")
        page_object = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_object_number} 0 R >> >> "
            f"/Contents {content_object_numbers[page_index]} 0 R >>"
        ).encode("latin-1")
        content_object = (
            f"<< /Length {len(content_stream)} >>\nstream\n".encode("latin-1")
            + content_stream
            + b"\nendstream"
        )
        objects.append(page_object)
        objects.append(content_object)

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_index, object_bytes in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{object_index} 0 obj\n".encode("latin-1"))
        buffer.write(object_bytes)
        buffer.write(b"\nendobj\n")
    xref_start = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    buffer.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF"
        ).encode("latin-1")
    )
    return buffer.getvalue()


def build_simple_pdf_bytes(title: str, lines: list[str], *, width: int = 92) -> bytes:
    normalized_lines: list[str] = [str(title or "Report").strip() or "Report", ""]
    for line in lines:
        normalized_lines.extend(_wrap_pdf_line(str(line or ""), width=width))
    max_lines_per_page = 46
    paged_lines = [normalized_lines[index:index + max_lines_per_page] for index in range(0, len(normalized_lines), max_lines_per_page)] or [[title]]

    font_object_number = 3
    objects: list[bytes] = []
    page_object_numbers: list[int] = []
    content_object_numbers: list[int] = []
    next_object_number = 4
    for _page in paged_lines:
        page_object_numbers.append(next_object_number)
        next_object_number += 1
        content_object_numbers.append(next_object_number)
        next_object_number += 1

    kids_refs = " ".join(f"{number} 0 R" for number in page_object_numbers)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Count {len(page_object_numbers)} /Kids [{kids_refs}] >>".encode("latin-1"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for page_index, page_lines in enumerate(paged_lines):
        content_lines = ["BT", "/F1 11 Tf", "50 790 Td", "14 TL"]
        for line in page_lines:
            content_lines.append(f"({_pdf_escape(line)}) Tj")
            content_lines.append("T*")
        content_lines.append("ET")
        content_stream = "\n".join(content_lines).encode("latin-1", errors="replace")
        page_object = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_object_number} 0 R >> >> "
            f"/Contents {content_object_numbers[page_index]} 0 R >>"
        ).encode("latin-1")
        content_object = (
            f"<< /Length {len(content_stream)} >>\nstream\n".encode("latin-1")
            + content_stream
            + b"\nendstream"
        )
        objects.append(page_object)
        objects.append(content_object)

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_index, object_bytes in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{object_index} 0 obj\n".encode("latin-1"))
        buffer.write(object_bytes)
        buffer.write(b"\nendobj\n")
    xref_start = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    buffer.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF"
        ).encode("latin-1")
    )
    return buffer.getvalue()


def markdown_to_pdf_lines(markdown_text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in str(markdown_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        if line.startswith("###"):
            lines.extend(["", line.lstrip("# ").upper()])
        elif line.startswith("##"):
            lines.extend(["", line.lstrip("# ").upper()])
        elif line.startswith("#"):
            lines.extend(["", line.lstrip("# ").upper()])
        elif line.startswith(("- ", "* ")):
            lines.append("- " + line[2:].strip())
        else:
            lines.append(line.replace("**", "").replace("__", "").replace("`", ""))
    return lines


def build_ai_audit_pdf_bytes(report_markdown: str, *, job_id: str) -> bytes:
    lines = markdown_to_pdf_lines(report_markdown)
    return build_simple_pdf_bytes(f"AI Audit Report - {job_id}", lines, width=88)


def markdown_to_report_html(markdown_text: str) -> str:
    html_parts: list[str] = []
    in_list = False
    for raw_line in str(markdown_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            continue
        if line.startswith("#"):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            level = min(max(len(line) - len(line.lstrip("#")), 2), 3)
            text = html_escape(line.lstrip("# ").replace("**", "").replace("__", "").replace("`", ""))
            html_parts.append(f"<h{level}>{text}</h{level}>")
            continue
        if line.startswith(("- ", "* ")):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            text = html_escape(line[2:].replace("**", "").replace("__", "").replace("`", ""))
            html_parts.append(f"<li>{text}</li>")
            continue
        if in_list:
            html_parts.append("</ul>")
            in_list = False
        html_parts.append(f"<p>{html_escape(line.replace('**', '').replace('__', '').replace('`', ''))}</p>")
    if in_list:
        html_parts.append("</ul>")
    return "\n".join(html_parts)


def build_ai_audit_report_html(
    report: dict[str, object],
    *,
    job_id: str,
    current_plan_assessment: dict[str, object],
    current_plan_comparison: dict[str, object],
    route_reallocation_analysis: dict[str, object],
    scenario_snapshots: list[dict[str, object]],
) -> bytes:
    report_markdown = str(report.get("report_markdown") or "")
    generated_at = html_escape(str(report.get("generated_at") or ""))
    model = html_escape(str(report.get("model") or ""))
    reallocation_summary = dict(route_reallocation_analysis.get("summary") or {})
    current_route_count = int(
        current_plan_assessment.get("route_count")
        or current_plan_comparison.get("current_route_count")
        or 0
    )
    current_avg_load = float(current_plan_assessment.get("avg_load_factor_pct", 0.0) or 0.0)
    current_avg_duration = float(current_plan_assessment.get("avg_route_duration_minutes", 0.0) or 0.0)
    action_count = int(reallocation_summary.get("actionable_weak_route_count", 0) or 0)
    scenario_rows = []
    for snapshot in scenario_snapshots:
        if not bool(snapshot.get("enabled")):
            continue
        scenario_rows.append(
            "<tr>"
            f"<td>{html_escape(str(snapshot.get('name', '')))}</td>"
            f"<td>{int(snapshot.get('route_count', 0) or 0)}</td>"
            f"<td>{float(snapshot.get('avg_route_duration_minutes', 0.0) or 0.0):.1f}</td>"
            f"<td>{float(snapshot.get('avg_route_distance_km', 0.0) or 0.0):.1f}</td>"
            f"<td>{float(snapshot.get('avg_load_factor_pct', 0.0) or 0.0):.1f}%</td>"
            "</tr>"
        )
    body = markdown_to_report_html(report_markdown)
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AI Audit Report - {html_escape(job_id)}</title>
  <style>
    @page {{ margin: 18mm; }}
    body {{ font-family: Arial, Helvetica, sans-serif; color: #172033; margin: 0; background: #f3f6fa; }}
    .page {{ max-width: 980px; margin: 0 auto; background: #fff; padding: 36px 42px; }}
    .eyebrow {{ color: #607086; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
    h1 {{ margin: 8px 0 4px; font-size: 30px; }}
    h2 {{ margin-top: 28px; padding-top: 14px; border-top: 1px solid #d9e0e8; font-size: 20px; }}
    h3 {{ margin-top: 18px; font-size: 16px; }}
    p, li {{ font-size: 14px; line-height: 1.55; }}
    ul {{ margin-top: 8px; padding-left: 22px; }}
    .meta {{ color: #607086; font-size: 12px; margin-bottom: 24px; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 24px 0; }}
    .card {{ border: 1px solid #d9e0e8; border-radius: 8px; padding: 14px; background: #f8fafc; }}
    .card .label {{ color: #607086; font-size: 12px; }}
    .card .value {{ font-size: 22px; font-weight: 700; margin-top: 6px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 18px 0 26px; font-size: 13px; }}
    th {{ background: #eef3f8; text-align: left; }}
    th, td {{ border: 1px solid #d9e0e8; padding: 9px 10px; }}
    .report {{ border-top: 3px solid #1e6bff; padding-top: 10px; }}
    .footer {{ margin-top: 32px; color: #607086; font-size: 11px; }}
    @media print {{
      body {{ background: #fff; }}
      .page {{ padding: 0; max-width: none; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <div class="eyebrow">BRP AI Audit Report</div>
    <h1>Current Scheme Assessment</h1>
    <div class="meta">Job {html_escape(job_id)} | Generated {generated_at or "N/A"} | Model {model or "N/A"}</div>
    <section class="cards">
      <div class="card"><div class="label">Current Routes</div><div class="value">{current_route_count or "N/A"}</div></div>
      <div class="card"><div class="label">Average Load</div><div class="value">{current_avg_load:.1f}%</div></div>
      <div class="card"><div class="label">Average Route Time</div><div class="value">{current_avg_duration:.1f} min</div></div>
      <div class="card"><div class="label">Action Signals</div><div class="value">{action_count}</div></div>
    </section>
    <h2>Scenario Snapshot</h2>
    <table>
      <thead><tr><th>Scenario</th><th>Routes</th><th>Avg Time (min)</th><th>Avg Distance (km)</th><th>Avg Load</th></tr></thead>
      <tbody>{''.join(scenario_rows) or '<tr><td colspan="5">No scenario data available.</td></tr>'}</tbody>
    </table>
    <section class="report">{body}</section>
    <div class="footer">Generated from deterministic BRP audit outputs. Full address lists are excluded from the AI prompt.</div>
  </main>
</body>
</html>"""
    return html.encode("utf-8")


def build_audit_report_from_result(result: dict[str, object]) -> list[tuple[str, pd.DataFrame]]:
    audit_sheets: list[tuple[str, pd.DataFrame]] = []
    current_plan_assessment = summarize_current_plan_assessment(result.get("current_plan_assessment"))
    if not current_plan_assessment:
        return audit_sheets

    route_reallocation_analysis = result.get("route_reallocation_analysis") or {}
    nearby_private_access_analysis = result.get("nearby_private_access_analysis") or {}
    like_for_like_baseline = summarize_current_plan_assessment(result.get("like_for_like_baseline"))
    constrained_improvement_baseline = summarize_current_plan_assessment(result.get("constrained_improvement_baseline"))
    current_plan_like_for_like_comparison = result.get("current_plan_like_for_like_comparison") or {}
    current_plan_constrained_comparison = result.get("current_plan_constrained_comparison") or {}
    constrained_package_summaries = list(result.get("constrained_package_summaries") or [])
    free_optimization_baseline_result = (
        result.get("free_optimization_baseline")
        or (result.get("structured_results") or {}).get("free_optimization_baseline", {})
        or (result.get("structured_results") or {}).get("original", {})
    )
    current_plan_comparison = result.get("current_plan_comparison") or {}
    planner_config_used = dict(result.get("planner_config") or {})
    route_duration_limit_minutes = int(planner_config_used.get("max_route_duration_minutes", 60))

    audit_sheets.append(
        ("Audit Summary", pd.DataFrame([{
            "current_routes": current_plan_assessment["route_count"],
            "current_service_stops": current_plan_assessment["stop_count"],
            "avg_route_distance_km": current_plan_assessment["avg_route_distance_km"],
            "avg_route_duration_min": current_plan_assessment["avg_route_duration_minutes"],
            "avg_load_factor_pct": current_plan_assessment["avg_load_factor_pct"],
            "low_load_routes": current_plan_assessment["low_load_route_count"],
            "overlong_routes": current_plan_assessment["overlong_route_count"],
            "bus_mix": format_bus_mix(current_plan_assessment["bus_mix"]),
        }]))
    )
    if current_plan_assessment["recommendations"]:
        audit_sheets.append(
            ("Audit Recommendations", pd.DataFrame({"recommendation": current_plan_assessment["recommendations"]}))
        )

    route_table_rows = build_current_route_table_rows(
        list(current_plan_assessment.get("route_summaries", [])),
        route_duration_limit_minutes,
    )
    if route_table_rows:
        audit_sheets.append(("Route Diagnostics", pd.DataFrame(route_table_rows)))

    if route_reallocation_analysis:
        weak_routes = list(route_reallocation_analysis.get("weak_routes") or [])
        reallocation_recommendations = list(route_reallocation_analysis.get("recommendations") or [])
        route_opportunity_profiles = list(route_reallocation_analysis.get("route_opportunity_profiles") or [])
        reallocation_summary = dict(route_reallocation_analysis.get("summary") or {})
        audit_sheets.append(
            ("Reallocation Summary", pd.DataFrame([{
                "weak_routes": len(weak_routes),
                "actionable_weak_routes": int(reallocation_summary.get("actionable_weak_route_count", 0) or 0),
                "removal_paths": int(reallocation_summary.get("route_removal_candidate_count", 0) or 0),
                "consolidation_paths": int(reallocation_summary.get("route_consolidation_candidate_count", 0) or 0),
                "removable_now": int(reallocation_summary.get("route_removable_now_count", 0) or 0),
                "candidate_route_pairs": int(route_reallocation_analysis.get("candidate_route_pair_count", 0) or 0),
                "best_time_saving_min": float(reallocation_summary.get("best_network_time_saving_s", 0.0) or 0.0) / 60.0,
                "best_distance_saving_km": float(reallocation_summary.get("best_network_distance_saving_m", 0.0) or 0.0) / 1000.0,
            }]))
        )
        if weak_routes:
            audit_sheets.append(("Weak Routes", pd.DataFrame(build_weak_route_table_rows(weak_routes))))
        if route_opportunity_profiles:
            audit_sheets.append(
                ("Route Action Signals", pd.DataFrame(build_route_opportunity_profile_rows(route_opportunity_profiles)))
            )
        priority_recommendations = list(reallocation_summary.get("priority_recommendations") or reallocation_recommendations[:3])
        if priority_recommendations:
            priority_df = pd.DataFrame([
                {
                    "route_action_label": str(item.get("route_action_label", "")).strip() or "Local improvement",
                    "explanation": str(item.get("explanation", "")).strip(),
                    "time_saving_min": float(item.get("network_total_duration_saving_s", 0.0) or 0.0) / 60.0,
                    "distance_saving_km": float(item.get("network_total_distance_saving_m", 0.0) or 0.0) / 1000.0,
                }
                for item in priority_recommendations
            ])
            audit_sheets.append(("Priority Actions", priority_df))
        if reallocation_recommendations:
            audit_sheets.append(
                ("Detailed Moves", pd.DataFrame(build_reallocation_table_rows(reallocation_recommendations)))
            )
    if like_for_like_baseline:
        audit_sheets.append(
            ("Like-for-Like Baseline", pd.DataFrame([{
                "baseline_routes": like_for_like_baseline["route_count"],
                "avg_route_distance_km": like_for_like_baseline["avg_route_distance_km"],
                "avg_route_duration_min": like_for_like_baseline["avg_route_duration_minutes"],
                "avg_load_factor_pct": like_for_like_baseline["avg_load_factor_pct"],
                "bus_mix": format_bus_mix(like_for_like_baseline["bus_mix"]),
            }]))
        )
        if current_plan_like_for_like_comparison:
            like_for_like_recommendations = list(current_plan_like_for_like_comparison.get("recommendations") or [])
            if like_for_like_recommendations:
                audit_sheets.append(
                    ("Like-for-Like Findings", pd.DataFrame({"finding": like_for_like_recommendations}))
                )
            audit_sheets.append(
                ("Like-for-Like Comparison", pd.DataFrame([{
                    "current_route_count": int(current_plan_like_for_like_comparison.get("current_route_count", 0)),
                    "route_gap": int(current_plan_like_for_like_comparison.get("route_gap", 0)),
                    "current_avg_distance_km": float(current_plan_like_for_like_comparison.get("current_avg_route_distance_m", 0.0)) / 1000.0,
                    "avg_distance_gap_pct": float(current_plan_like_for_like_comparison.get("avg_distance_gap_pct", 0.0)),
                    "current_avg_duration_min": float(current_plan_like_for_like_comparison.get("current_avg_route_duration_s", 0.0)) / 60.0,
                    "avg_duration_gap_pct": float(current_plan_like_for_like_comparison.get("avg_duration_gap_pct", 0.0)),
                    "baseline_avg_load_pct": float(current_plan_like_for_like_comparison.get("baseline_avg_load_factor", 0.0)) * 100.0,
                }]))
            )

    if constrained_improvement_baseline:
        audit_sheets.append(
            ("Constrained Baseline", pd.DataFrame([{
                "baseline_routes": constrained_improvement_baseline["route_count"],
                "avg_route_distance_km": constrained_improvement_baseline["avg_route_distance_km"],
                "avg_route_duration_min": constrained_improvement_baseline["avg_route_duration_minutes"],
                "avg_load_factor_pct": constrained_improvement_baseline["avg_load_factor_pct"],
                "bus_mix": format_bus_mix(constrained_improvement_baseline["bus_mix"]),
            }]))
        )
        if current_plan_constrained_comparison:
            constrained_recommendations = list(current_plan_constrained_comparison.get("recommendations") or [])
            if constrained_recommendations:
                audit_sheets.append(
                    ("Constrained Findings", pd.DataFrame({"finding": constrained_recommendations}))
                )
            audit_sheets.append(
                ("Constrained Comparison", pd.DataFrame([{
                    "current_route_count": int(current_plan_constrained_comparison.get("current_route_count", 0)),
                    "route_gap": int(current_plan_constrained_comparison.get("route_gap", 0)),
                    "current_avg_distance_km": float(current_plan_constrained_comparison.get("current_avg_route_distance_m", 0.0)) / 1000.0,
                    "avg_distance_gap_pct": float(current_plan_constrained_comparison.get("avg_distance_gap_pct", 0.0)),
                    "current_avg_duration_min": float(current_plan_constrained_comparison.get("current_avg_route_duration_s", 0.0)) / 60.0,
                    "avg_duration_gap_pct": float(current_plan_constrained_comparison.get("avg_duration_gap_pct", 0.0)),
                    "baseline_avg_load_pct": float(current_plan_constrained_comparison.get("baseline_avg_load_factor", 0.0)) * 100.0,
                }]))
            )
        constrained_selected_moves = list(result.get("constrained_selected_moves") or [])
        if constrained_selected_moves:
            constrained_package_rows = build_constrained_package_rows(
                constrained_selected_moves,
                constrained_package_summaries,
            )
            if constrained_package_rows:
                audit_sheets.append(("Constrained Packages", pd.DataFrame(constrained_package_rows)))
                constrained_package_outcome_rows = build_constrained_package_outcome_rows(
                    constrained_package_summaries,
                    route_duration_limit_minutes,
                )
                if constrained_package_outcome_rows:
                    audit_sheets.append(("Package Outcomes", pd.DataFrame(constrained_package_outcome_rows)))

    if free_optimization_baseline:
        free_opt = {
            "baseline_routes": int(free_optimization_baseline.get("route_count", 0)),
            "avg_route_distance_km": float(free_optimization_baseline.get("avg_route_distance_km", 0.0)),
            "avg_route_duration_min": float(free_optimization_baseline.get("avg_route_duration_minutes", 0.0)),
            "avg_load_factor_pct": float(free_optimization_baseline.get("avg_load_factor_pct", 0.0)),
            "bus_mix": format_bus_mix(dict(free_optimization_baseline.get("bus_mix", {}))),
        }
        audit_sheets.append(("Free Optimization Baseline", pd.DataFrame([free_opt])))
        free_routes = build_route_table_rows(list(free_optimization_baseline.get("routes") or []))
        if free_routes:
            audit_sheets.append(("Free Opt Route Table", pd.DataFrame(free_routes)))

    nearby_private_access_cluster_rows = build_private_access_cluster_rows(nearby_private_access_analysis)
    if nearby_private_access_cluster_rows:
        audit_sheets.append(("Nearby Private Access Clusters", pd.DataFrame(nearby_private_access_cluster_rows)))
    nearby_private_access_rows = build_private_access_rows(nearby_private_access_analysis)
    if nearby_private_access_rows:
        audit_sheets.append(("Nearby Private Access Riders", pd.DataFrame(nearby_private_access_rows)))

    if current_plan_comparison:
        comparison_recommendations = list(current_plan_comparison.get("recommendations") or [])
        if comparison_recommendations:
            audit_sheets.append(
                ("Comparison Findings", pd.DataFrame({"finding": comparison_recommendations}))
            )
        audit_sheets.append(
            ("Current vs Free Baseline", pd.DataFrame([{
                "current_route_count": int(current_plan_comparison.get("current_route_count", 0)),
                "route_gap": int(current_plan_comparison.get("route_gap", 0)),
                "current_avg_distance_km": float(current_plan_comparison.get("current_avg_route_distance_m", 0.0)) / 1000.0,
                "avg_distance_gap_pct": float(current_plan_comparison.get("avg_distance_gap_pct", 0.0)),
                "current_avg_duration_min": float(current_plan_comparison.get("current_avg_route_duration_s", 0.0)) / 60.0,
                "avg_duration_gap_pct": float(current_plan_comparison.get("avg_duration_gap_pct", 0.0)),
                "current_avg_load_pct": float(current_plan_comparison.get("current_avg_load_factor", 0.0)) * 100.0,
                "current_bus_mix": format_bus_mix(dict(current_plan_comparison.get("current_bus_mix", {}))),
                "baseline_bus_mix": format_bus_mix(dict(current_plan_comparison.get("baseline_bus_mix", {}))),
            }]))
        )

    return audit_sheets


st.header("📥 Data Input")
source_mode = st.radio(
    "Input Source",
    options=["Upload Workbook", "Demo Workbook"],
    horizontal=True,
)

source_excel_path: str | None = None
source_label = ""
template_bytes = build_excel_template_bytes()

if source_mode == "Upload Workbook":
    upload_col, template_col = st.columns([2.4, 1], vertical_alignment="bottom")
    with upload_col:
        uploaded_file = st.file_uploader("Upload Workbook", type=["xlsx", "xlsm"])
    with template_col:
        st.download_button(
            label="Download Template",
            data=template_bytes,
            file_name="brp_planning_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    if uploaded_file is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded_file.name).suffix) as temp_file:
            temp_file.write(uploaded_file.getvalue())
            source_excel_path = temp_file.name
        source_label = uploaded_file.name
else:
    demo_files = sorted(path for path in DEMO_DATA_DIR.glob("*.xlsx"))
    if not demo_files:
        st.warning("No demo workbook was found in `apps/client/demodata`.")
    else:
        selected_demo_name = str(st.session_state.get("demo_workbook_name", demo_files[0].name))
        selected_demo_path = next((path for path in demo_files if path.name == selected_demo_name), demo_files[0])
        demo_select_col, demo_download_col = st.columns([2.4, 1], vertical_alignment="bottom")
        with demo_select_col:
            selected_demo_path = st.selectbox(
                "Demo Workbook",
                options=demo_files,
                format_func=lambda path: path.name,
            )
        with demo_download_col:
            st.download_button(
                label="Download Demo",
                data=selected_demo_path.read_bytes(),
                file_name=selected_demo_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        st.session_state["demo_workbook_name"] = selected_demo_path.name
        source_excel_path = str(selected_demo_path)
        source_label = selected_demo_path.name
        st.caption(
            f"{len(demo_files)} demo workbook{'s' if len(demo_files) != 1 else ''} available. "
            f"Currently selected: `{selected_demo_path.name}`."
        )
        st.caption(
            "This demo uses the same workbook parsing and planning flow as a real user upload. "
            "The only difference is that the file comes from `apps/client/demodata`."
        )

job_name_signature = (source_mode, source_label)
if st.session_state.get("planner_job_name_signature") != job_name_signature:
    st.session_state["planner_job_custom_name"] = ""
    st.session_state["planner_job_name_signature"] = job_name_signature

direction_col, traffic_col, duration_col = st.columns(3)
with direction_col:
    service_direction = st.selectbox(
        "Service Direction",
        options=SERVICE_DIRECTION_OPTIONS,
        index=SERVICE_DIRECTION_OPTIONS.index("From School"),
    )
    st.caption("Choose whether routes are planned outward from school or inward to school.")
with traffic_col:
    traffic_profile_name = st.selectbox(
        "Traffic Assumptions",
        options=TRAFFIC_PROFILE_OPTIONS,
        index=TRAFFIC_PROFILE_OPTIONS.index("Off-Peak"),
    )
    st.caption(
        "Adjusts route time only, uses city-aware defaults when supported, and does not change distance."
    )
with duration_col:
    max_route_duration_minutes = keyed_number_input(
        "Target Route Duration (min)",
        "planner_max_route_duration_minutes",
        60,
        min_value=10,
        max_value=300,
        step=5,
    )
    st.caption(
        "This target now directly influences baseline solving as well as audit thresholds, with a 10-minute acceptable operating buffer."
    )

terminal_row_label = "last" if service_direction == "To School" else "first"
terminal_row_description = "shared school row" if service_direction == "To School" else "shared depot / school row"
direction_explanation = (
    "Routes are interpreted as pickup sequences that end at school."
    if service_direction == "To School"
    else "Routes are interpreted as outbound runs that start from school."
)

with st.expander("Usage Steps and Notes (expand to learn how to use)", expanded=False):
    st.markdown(
        f"""
        **Workflow**

        1. Choose `Upload Workbook` for a real run, or `Demo Workbook` to test with sample data.
        2. Download the template if needed. The required sheets are `current_plan_assignments` and `current_plan_fleet`.
        3. In `current_plan_assignments`, enter one row per stop using:
           `route_id`, `stop_sequence`, `bus_type`, `country`, `city`, `address`, `passenger_count`.
        4. In `current_plan_fleet`, enter the actual fleet facts using:
           `bus_type`, `seat_count`, `vehicle_count`.
        5. Set `Service Direction`, `Traffic Assumptions`, and `Target Route Duration` before submitting.
        6. Optional: fill `Custom Job Name`. If blank, the job uses the workbook name. If filled, Job History shows `workbook name - custom name`.
        7. Click `Submit Job`. The backend runs geocoding, current-plan assessment, OSRM routing, OR-Tools baseline solving, route reallocation analysis, map rendering, and stores the job in history.
        8. Reopen completed runs from `Job History`. Succeeded historical jobs can be loaded again without rerunning the planner.
        9. Review the result tabs:
           - `AI Audit Report`: generate or regenerate a bounded AI narrative from deterministic audit facts, then download the printable HTML report.
           - `Audit Evidence`: inspect current-plan metrics, route diagnostics, reallocation signals, and benchmark comparisons.
           - `Baseline Scenarios`: compare scenario route tables and download the Free Optimization Baseline as a new input-template workbook.
           - `Maps`: view and download rendered HTML route maps.
           - `Diagnostics`: review geocode warnings, excluded stops, and raw technical details.
        10. Use `Distance & Cost` in the sidebar for quick operational checks:
           - `Reference Distance Check`: calculate distance from one reference stop to uploaded addresses.
           - `Current Plan Route Cost`: sum each route's stop-to-stop OSRM distance and estimate one-way diesel cost.
           - Distance & Cost results are cached locally and can be reloaded from its job cache.

        **Workbook rules**

        - Current direction: `{service_direction}`. {direction_explanation}
        - `From School`: the first row of each route must be the shared school / depot row.
        - `To School`: the last row of each route must be the shared school row.
        - The **{terminal_row_label} row** of every route should be the **{terminal_row_description}** and must have `passenger_count = 0`.
        - All routes should use the same school / depot address so the baselines have one consistent anchor.
        - `stop_sequence` controls the imported current-plan order. Keep it unique within each `route_id`.
        - `bus_type` must match a row in `current_plan_fleet`.
        - `passenger_count` must be a non-negative whole number.

        **What the system computes**

        - Current Plan: evaluates the imported route order and actual fleet seat counts.
        - Like-for-Like Baseline: keeps route allocation and bus mix fixed, then improves stop order.
        - Constrained Improvement Baseline: applies a small set of high-confidence route-to-route transfers.
        - Free Optimization Baseline: lets the solver regroup planning stops more freely under the configured fleet limits.
        - Reallocation Signals: identifies weak routes, possible route removals, and targeted stop-transfer opportunities.
        - AI Audit Report: summarizes the deterministic outputs only; full address lists are excluded from the AI prompt.

        **Geocoding, routing, and cost notes**

        - China addresses use AMap. South Korea uses Kakao by default, while English-only Korean address text can fall back to Google Geocoding.
        - Korean addresses are not auto-normalized or auto-corrected. Unresolved stops appear in Diagnostics for manual cleanup.
        - Routing uses OSRM road data for supported country/city datasets.
        - Traffic assumptions adjust route time only. Distance stays unchanged.
        - Distance & Cost route-cost defaults can be selected by deployment through `BRP_ROUTE_COST_MARKET`.
        - E-bus, electric, EV, and new-energy bus types keep route distance results but skip diesel-cost estimation.
        - Large files or uncached addresses may take longer because geocoding, OSRM calls, OR-Tools solving, and analysis are real computations.
        """
    )

selected_sheet = "current_plan_assignments"
current_plan_preview: dict[str, object] | None = None
current_plan_preview_error: str | None = None

if source_excel_path is not None:
    sheet_names = get_excel_sheet_names(source_excel_path)
    has_current_plan_sheets = {"current_plan_assignments", "current_plan_fleet"} <= set(sheet_names)
    if not has_current_plan_sheets:
        missing_sheet_names = sorted({"current_plan_assignments", "current_plan_fleet"} - set(sheet_names))
        current_plan_preview_error = (
            "This version expects a workbook with sheets `current_plan_assignments` and `current_plan_fleet`. "
            f"Missing: {', '.join(missing_sheet_names)}."
        )
    else:
        try:
            current_plan_preview = read_current_plan_from_excel(source_excel_path, service_direction=service_direction)
        except Exception as exc:
            current_plan_preview_error = str(exc)
else:
    st.info("Upload a workbook or choose a demo workbook to begin.")

if current_plan_preview:
    maybe_autofill_planner_settings_from_current_plan(current_plan_preview, source_label)


job_summaries: list[dict[str, object]] = []
job_list_error: str | None = None
try:
    job_summaries = list_backend_jobs(BACKEND_BASE_URL, user_email=CURRENT_USER_EMAIL)
except Exception as exc:
    job_list_error = friendly_error_message(exc)


with st.sidebar:
    st.caption(f"Signed in as `{CURRENT_USER_EMAIL}`")
    render_sign_out_link(ACCESS_LOGOUT_URL)
    st.header("Optional Baseline Settings")
    st.caption(
        "Most users can leave these unchanged. When a current plan workbook is loaded, these baseline settings are auto-filled from the imported fleet facts; they still only affect the system-generated baselines and do not overwrite the imported current plan."
    )
    with st.expander("Fleet Assumptions", expanded=False):
        large_bus_name = st.text_input("Large Slot Label", key="planner_large_bus_name", value="Large Bus")
        large_bus_capacity = keyed_number_input(f"{large_bus_name or 'Large Slot'} Seats", "planner_large_bus_capacity", 42, min_value=0, max_value=200, step=1)
        large_bus_max_count = keyed_number_input(f"{large_bus_name or 'Large Slot'} Max Count", "planner_large_bus_max_count", 20, min_value=0, max_value=200, step=1)
        mid_bus_name = st.text_input("Mid Slot Label", key="planner_mid_bus_name", value="Mid Bus")
        mid_bus_capacity = keyed_number_input(f"{mid_bus_name or 'Mid Slot'} Seats", "planner_mid_bus_capacity", 35, min_value=0, max_value=200, step=1)
        mid_bus_max_count = keyed_number_input(f"{mid_bus_name or 'Mid Slot'} Max Count", "planner_mid_bus_max_count", 15, min_value=0, max_value=200, step=1)
        small_bus_name = st.text_input("Small Slot Label", key="planner_small_bus_name", value="Small Bus")
        small_bus_capacity = keyed_number_input(f"{small_bus_name or 'Small Slot'} Seats", "planner_small_bus_capacity", 19, min_value=0, max_value=200, step=1)
        small_bus_max_count = keyed_number_input(f"{small_bus_name or 'Small Slot'} Max Count", "planner_small_bus_max_count", 10, min_value=0, max_value=200, step=1)

    with st.expander("Free Baseline Vehicle Ratio", expanded=False):
        st.caption(
            "These weights only affect the free-optimization baseline. The system converts them into "
            "the configured slot counts over the total baseline fleet budget."
        )
        free_baseline_large_bus_ratio = keyed_number_input(f"Free Baseline {large_bus_name or 'Large Slot'} Weight", "planner_free_baseline_large_bus_ratio", float(large_bus_max_count), min_value=0.0, max_value=500.0, step=1.0)
        free_baseline_mid_bus_ratio = keyed_number_input(f"Free Baseline {mid_bus_name or 'Mid Slot'} Weight", "planner_free_baseline_mid_bus_ratio", float(mid_bus_max_count), min_value=0.0, max_value=500.0, step=1.0)
        free_baseline_small_bus_ratio = keyed_number_input(f"Free Baseline {small_bus_name or 'Small Slot'} Weight", "planner_free_baseline_small_bus_ratio", float(small_bus_max_count), min_value=0.0, max_value=500.0, step=1.0)
        st.caption(
            f"Current ratio input: {format_vehicle_ratio({(large_bus_name or 'Large Bus'): free_baseline_large_bus_ratio, (mid_bus_name or 'Mid Bus'): free_baseline_mid_bus_ratio, (small_bus_name or 'Small Bus'): free_baseline_small_bus_ratio})}"
        )

    with st.expander("Route Policy Assumptions", expanded=False):
        stop_service_minutes = st.number_input("Stop Dwell Minutes", min_value=0, max_value=20, value=1, step=1)
        express_threshold_km = st.number_input("Remote Stop Threshold (km)", min_value=1.0, max_value=100.0, value=15.0, step=1.0)
        reserved_express_buses = st.number_input("Reserved Express Buses", min_value=0, max_value=100, value=4, step=1)
        express_skip_inner_km = st.number_input("Express Skip Inner Radius (km)", min_value=0.0, max_value=100.0, value=8.0, step=1.0)

    with st.expander("Advanced Aggregation Settings", expanded=False):
        st.caption(
            "These only affect the optional subway-aggregated and nearby-aggregated comparison scenarios."
        )
        subway_search_radius_m = st.number_input("Subway Search Radius (m)", min_value=100, max_value=5000, value=1500, step=100)
        max_subway_walk_distance_m = st.number_input("Max Subway Walk Distance (m)", min_value=50, max_value=3000, value=800, step=50)
        nearby_cluster_radius_m = st.number_input("Nearby Cluster Radius (m)", min_value=50, max_value=3000, value=500, step=50)
    st.caption(read_google_geocode_usage_display())
    st.caption("Other tools")
    if st.button("Distance & Cost", key="open_distance_checker"):
        st.query_params["tool"] = "distance-checker"
        st.rerun()
    if st.button("Fleet Planner Preview", key="open_fleet_planner_preview"):
        st.query_params["tool"] = "fleet-planner-preview"
        st.rerun()

current_input_records: list[dict[str, object]] = []
input_parse_error: str | None = None
run_planner_clicked = False
include_subway_aggregation_scenario = True
include_nearby_aggregation_scenario = True
subway_aggregation_block_reason: str | None = None
if source_excel_path is not None:
    if current_plan_preview:
        current_input_records = [dict(item) for item in list(current_plan_preview.get("input_records") or [])]
        current_plan_summary = current_plan_preview["summary"]
        subway_aggregation_block_reason = find_subway_aggregation_block_reason(current_input_records)
        st.caption(
            f"Workbook detected as current-plan audit input from `{source_label}`. "
            f"Prepared {current_plan_summary.get('planning_stop_count', len(current_input_records))} planning stops from `current_plan_assignments`."
        )
        st.info(
            "Current plan workbook detected. "
            f"`current_plan_assignments` is being used route-by-route for this audit run. "
            f"Direction: {current_plan_summary.get('service_direction', service_direction)} | "
            f"Routes: {current_plan_summary['route_count']} | "
            f"Service rows: {current_plan_summary['assignment_count']} | "
            f"Planning stops: {current_plan_summary.get('planning_stop_count', len(current_input_records))} | "
            f"Bus types: {', '.join(current_plan_summary['bus_types']) or 'None'} | "
            f"Fleet: {'; '.join(current_plan_summary.get('fleet_summary', [])) or 'None'}"
        )
    elif current_plan_preview_error:
        input_parse_error = current_plan_preview_error
        st.error(f"Workbook parsing failed: {current_plan_preview_error}")
        st.caption(
            "Please use the planning template and keep only the two required sheets: "
            "`current_plan_assignments` and `current_plan_fleet`."
        )
    include_subway_aggregation_scenario = st.checkbox(
        "Run subway alternative baseline",
        value=False if current_plan_preview or subway_aggregation_block_reason else True,
        help=(
            "When turned on, the app will search for nearby subway stations and build the subway-aggregated baseline. "
            "Turning it off skips subway-station queries entirely."
        ),
        disabled=bool(subway_aggregation_block_reason),
    )
    if subway_aggregation_block_reason:
        st.markdown(
            f"<p style='color:#d32f2f; margin:0.2rem 0 0.6rem 0;'>{subway_aggregation_block_reason}</p>",
            unsafe_allow_html=True,
        )
    include_nearby_aggregation_scenario = st.checkbox(
        "Run nearby alternative baseline",
        value=False if current_plan_preview else True,
        help=(
            "When turned on, the app will build the nearby-aggregated baseline by clustering close addresses. "
            "This does not trigger subway-station queries."
        ),
    )
    st.text_input(
        "Custom Job Name (optional)",
        key="planner_job_custom_name",
        placeholder="Example: May audit before parent review",
        help="Leave blank to use the default workbook-based name. If filled, Job History displays: default name - custom name.",
    )
    preview_job_name = build_job_display_name(source_label, str(st.session_state.get("planner_job_custom_name", "")))
    st.caption(f"Job History name preview: `{preview_job_name}`")
    run_planner_clicked = st.button(
        "Submit Job",
        type="primary",
        width="stretch",
        disabled=bool(input_parse_error),
    )


try:
    config = {
        "uploaded_rows": tuple(
            (
                item["country"],
                item["city"],
                item["address"],
                int(item["passenger_count"]),
            )
            for item in current_input_records
        ),
        "large_bus_capacity": large_bus_capacity,
        "large_bus_max_count": large_bus_max_count,
        "large_bus_name": large_bus_name,
        "mid_bus_capacity": mid_bus_capacity,
        "mid_bus_max_count": mid_bus_max_count,
        "mid_bus_name": mid_bus_name,
        "small_bus_capacity": small_bus_capacity,
        "small_bus_max_count": small_bus_max_count,
        "small_bus_name": small_bus_name,
        "free_baseline_large_bus_ratio": free_baseline_large_bus_ratio,
        "free_baseline_mid_bus_ratio": free_baseline_mid_bus_ratio,
        "free_baseline_small_bus_ratio": free_baseline_small_bus_ratio,
        "express_threshold_km": express_threshold_km,
        "reserved_express_buses": reserved_express_buses,
        "express_skip_inner_km": express_skip_inner_km,
        "max_route_duration_minutes": max_route_duration_minutes,
        "stop_service_minutes": stop_service_minutes,
        "traffic_profile_name": traffic_profile_name,
        "service_direction": service_direction,
        "subway_search_radius_m": subway_search_radius_m,
        "max_subway_walk_distance_m": max_subway_walk_distance_m,
        "nearby_cluster_radius_m": nearby_cluster_radius_m,
        "include_subway_aggregation_scenario": include_subway_aggregation_scenario,
        "include_nearby_aggregation_scenario": include_nearby_aggregation_scenario,
    }
    base_run_key = str(config)
except Exception:
    current_input_records = []
    base_run_key = ""

if run_planner_clicked:
    try:
        submission_snapshot = {
            "input_records": [dict(item) for item in current_input_records],
            "input_parse_error": input_parse_error,
            "selected_sheet": selected_sheet,
            "base_run_key": base_run_key,
            "source_label": source_label,
            "job_default_name": build_job_display_name(source_label),
            "job_custom_name": str(st.session_state.get("planner_job_custom_name", "")).strip(),
            "job_name": build_job_display_name(source_label, str(st.session_state.get("planner_job_custom_name", ""))),
            "planner_config": {
                "large_bus_capacity": int(large_bus_capacity),
                "large_bus_max_count": int(large_bus_max_count),
                "large_bus_name": str(large_bus_name),
                "mid_bus_capacity": int(mid_bus_capacity),
                "mid_bus_max_count": int(mid_bus_max_count),
                "mid_bus_name": str(mid_bus_name),
                "small_bus_capacity": int(small_bus_capacity),
                "small_bus_max_count": int(small_bus_max_count),
                "small_bus_name": str(small_bus_name),
                "free_baseline_large_bus_ratio": float(free_baseline_large_bus_ratio),
                "free_baseline_mid_bus_ratio": float(free_baseline_mid_bus_ratio),
                "free_baseline_small_bus_ratio": float(free_baseline_small_bus_ratio),
                "express_threshold_km": float(express_threshold_km),
                "reserved_express_buses": int(reserved_express_buses),
                "express_skip_inner_km": float(express_skip_inner_km),
                "max_route_duration_minutes": int(max_route_duration_minutes),
                "stop_service_minutes": int(stop_service_minutes),
                "traffic_profile_name": str(traffic_profile_name),
                "service_direction": str(service_direction),
                "subway_search_radius_m": int(subway_search_radius_m),
                "max_subway_walk_distance_m": int(max_subway_walk_distance_m),
                "nearby_cluster_radius_m": int(nearby_cluster_radius_m),
                "include_subway_aggregation_scenario": bool(include_subway_aggregation_scenario),
                "include_nearby_aggregation_scenario": bool(include_nearby_aggregation_scenario),
            },
        }
        st.session_state["planner_submission"] = submission_snapshot

        input_records = submission_snapshot["input_records"]
        if submission_snapshot["input_parse_error"]:
            raise ValueError(str(submission_snapshot["input_parse_error"]))
        st.write(
            f"Loaded {len(input_records)} input rows for `{submission_snapshot['job_name']}` using `{submission_snapshot['source_label']}`."
        )
        status_placeholder = st.empty()
        planner_started_at = time.perf_counter()
        status_state = {"step_started_at": planner_started_at, "current_step_label": "Starting planner"}
        step_history: list[dict[str, float | str]] = []
        recent_logs: list[str] = []

        def handle_progress_line(line: str) -> None:
            stripped = line.strip()
            if not stripped:
                return
            recent_logs.append(stripped)
            recent_logs[:] = recent_logs[-20:]
            next_step = infer_planner_step(stripped)
            now = time.perf_counter()
            if next_step and next_step != status_state["current_step_label"]:
                if status_state["current_step_label"]:
                    step_history.append(
                        {
                            "step": status_state["current_step_label"],
                            "step_elapsed": now - float(status_state["step_started_at"]),
                            "total_elapsed": now - planner_started_at,
                        }
                    )
                status_state["current_step_label"] = next_step
                status_state["step_started_at"] = now
            render_planner_status(status_placeholder, str(status_state["current_step_label"]), now - planner_started_at, step_history, recent_logs)

        render_planner_status(status_placeholder, str(status_state["current_step_label"]), 0.0, step_history, recent_logs)

        planner_config = PlannerConfig(
            large_bus_name=submission_snapshot["planner_config"].get("large_bus_name", "Large Bus"),
            mid_bus_name=submission_snapshot["planner_config"].get("mid_bus_name", "Mid Bus"),
            small_bus_name=submission_snapshot["planner_config"].get("small_bus_name", "Small Bus"),
            large_bus_capacity=submission_snapshot["planner_config"]["large_bus_capacity"],
            large_bus_max_count=submission_snapshot["planner_config"]["large_bus_max_count"],
            mid_bus_capacity=submission_snapshot["planner_config"]["mid_bus_capacity"],
            mid_bus_max_count=submission_snapshot["planner_config"]["mid_bus_max_count"],
            small_bus_capacity=submission_snapshot["planner_config"]["small_bus_capacity"],
            small_bus_max_count=submission_snapshot["planner_config"]["small_bus_max_count"],
            free_baseline_large_bus_ratio=submission_snapshot["planner_config"]["free_baseline_large_bus_ratio"],
            free_baseline_mid_bus_ratio=submission_snapshot["planner_config"]["free_baseline_mid_bus_ratio"],
            free_baseline_small_bus_ratio=submission_snapshot["planner_config"]["free_baseline_small_bus_ratio"],
            express_threshold_km=submission_snapshot["planner_config"]["express_threshold_km"],
            reserved_express_buses=submission_snapshot["planner_config"]["reserved_express_buses"],
            express_skip_inner_km=submission_snapshot["planner_config"]["express_skip_inner_km"],
            max_route_duration_minutes=submission_snapshot["planner_config"]["max_route_duration_minutes"],
            stop_service_minutes=submission_snapshot["planner_config"]["stop_service_minutes"],
            traffic_profile_name=submission_snapshot["planner_config"]["traffic_profile_name"],
            service_direction=submission_snapshot["planner_config"].get("service_direction", "From School"),
            subway_search_radius_m=submission_snapshot["planner_config"]["subway_search_radius_m"],
            max_subway_walk_distance_m=submission_snapshot["planner_config"]["max_subway_walk_distance_m"],
            nearby_cluster_radius_m=submission_snapshot["planner_config"]["nearby_cluster_radius_m"],
            include_subway_aggregation_scenario=bool(
                submission_snapshot["planner_config"].get(
                    "include_subway_aggregation_scenario",
                    submission_snapshot["planner_config"].get("include_aggregation_scenarios", True),
                )
            ),
            include_nearby_aggregation_scenario=bool(
                submission_snapshot["planner_config"].get(
                    "include_nearby_aggregation_scenario",
                    submission_snapshot["planner_config"].get("include_aggregation_scenarios", True),
                )
            ),
            operating_cost_per_km=0.0,
            revenue_rules=[{"min_km": 0.0, "max_km": None, "fee_per_person": 0.0}],
        )

        with st.spinner("Preparing and submitting job..."):
            handle_progress_line("[CLIENT] Preparing client-side data before backend submission.")
            client_prep = prepare_client_payload(
                input_records,
                current_plan_data=current_plan_preview,
                config=planner_config,
                progress_callback=handle_progress_line,
            )
            handle_progress_line("[CLIENT] Sending prepared data to backend.")
            submitted_job = submit_job_to_backend(
                client_prep["prepared_payload"],
                config=planner_config,
                backend_base_url=BACKEND_BASE_URL,
                user_email=CURRENT_USER_EMAIL,
                metadata={
                    "job_name": submission_snapshot["job_name"],
                    "job_default_name": submission_snapshot["job_default_name"],
                    "job_custom_name": submission_snapshot["job_custom_name"],
                    "source_label": submission_snapshot["source_label"],
                    "selected_sheet": submission_snapshot["selected_sheet"],
                    "planner_config": dict(submission_snapshot["planner_config"]),
                    "client_prep": {
                        "geocode_warnings": list(client_prep.get("geocode_warnings") or []),
                        "excluded_stops": list(client_prep.get("excluded_stops") or []),
                        "elapsed_seconds": float(client_prep.get("elapsed_seconds", 0.0) or 0.0),
                        "logs": str(client_prep.get("logs", "") or ""),
                    },
                },
                timeout_seconds=60,
            )
            handle_progress_line("[CLIENT] Job submitted to backend queue.")

        finished_at = time.perf_counter()
        step_history.append(
            {
                "step": str(status_state["current_step_label"]),
                "step_elapsed": finished_at - float(status_state["step_started_at"]),
                "total_elapsed": finished_at - planner_started_at,
            }
        )
        render_planner_status(status_placeholder, "Job submitted", finished_at - planner_started_at, step_history, recent_logs)
        submitted_job_id = str(submitted_job.get("job_id", "")).strip()
        st.session_state["active_job_id"] = submitted_job_id
        st.session_state["selected_job_id"] = submitted_job_id
        st.success(
            f"Job submitted successfully: `{submission_snapshot['job_name']}` (`{submitted_job_id}`). "
            "You can come back later and reopen it from Job History."
        )
    except Exception as exc:
        st.error(f"Planner failed: {friendly_error_message(exc)}")
        with st.expander("Technical Details"):
            st.code(str(exc), language="text")

st.divider()
st.header("📋 Job Selection & Output")
with st.expander("Job History", expanded=bool(job_summaries)):
    history_header_col, history_refresh_col = st.columns([6, 1])
    with history_header_col:
        st.caption("Review saved jobs, reload their results, or manage running jobs.")
    with history_refresh_col:
        if st.button("Refresh", key="job_history_refresh", width="stretch"):
            st.rerun()
    if job_list_error:
        st.warning(f"Job history could not be loaded: {job_list_error}")
    elif not job_summaries:
        st.caption("No submitted jobs yet.")
    else:
        default_job_id = str(st.session_state.get("selected_job_id", "")).strip()
        job_ids = [str(item.get("job_id", "")).strip() for item in job_summaries if str(item.get("job_id", "")).strip()]
        if not default_job_id or default_job_id not in job_ids:
            default_job_id = job_ids[0]
        selected_job_id = st.selectbox(
            "Saved Jobs",
            options=job_ids,
            index=job_ids.index(default_job_id),
            format_func=lambda job_id: format_job_option(next(item for item in job_summaries if str(item.get("job_id", "")).strip() == job_id)),
        )
        st.session_state["selected_job_id"] = selected_job_id
        selected_job_summary = next(
            (item for item in job_summaries if str(item.get("job_id", "")).strip() == selected_job_id),
            {},
        )
        selected_job_metadata = dict(selected_job_summary.get("metadata") or {})
        selected_job_payload_summary = dict(selected_job_summary.get("prepared_payload_summary") or {})
        selected_job_owner = format_job_owner_label(selected_job_summary)
        selected_job_name = (
            str(selected_job_metadata.get("job_name", "")).strip()
            or str(selected_job_metadata.get("source_label", "")).strip()
            or "Unknown"
        )
        st.caption(
            f"Job: {selected_job_name} | "
            f"Owner: {selected_job_owner} | "
            f"Status: {selected_job_summary.get('status', 'unknown')} | "
            f"Rows: {int(selected_job_payload_summary.get('input_record_count', 0) or 0)} | "
            f"Current-plan routes: {int(selected_job_payload_summary.get('current_plan_route_count', 0) or 0)} | "
            f"Source: {selected_job_metadata.get('source_label', 'Unknown')}"
        )
        render_localized_timestamp(
            "Created",
            str(selected_job_summary.get("created_at", "") or ""),
            key=f"history-{selected_job_id}",
        )
        auto_refresh_enabled = st.checkbox(
            "Auto-refresh selected job while running",
            value=True,
            key="job_history_auto_refresh",
        )
        action_col_1, action_col_2 = st.columns(2)
        if action_col_1.button("Terminate Selected Job", width="stretch", disabled=str(selected_job_summary.get("status", "")).strip() not in {"queued", "running"}):
            try:
                cancel_backend_job(BACKEND_BASE_URL, selected_job_id, user_email=CURRENT_USER_EMAIL)
                st.session_state["active_job_id"] = ""
                st.rerun()
            except Exception as exc:
                st.error(f"Terminate failed: {friendly_error_message(exc)}")
        if action_col_2.button("Delete Selected Job", width="stretch"):
            try:
                delete_backend_job(BACKEND_BASE_URL, selected_job_id, user_email=CURRENT_USER_EMAIL)
                if st.session_state.get("selected_job_id") == selected_job_id:
                    st.session_state["selected_job_id"] = ""
                if st.session_state.get("loaded_job_id") == selected_job_id:
                    st.session_state["loaded_job_id"] = ""
                    st.session_state["display_result"] = None
                if st.session_state.get("active_job_id") == selected_job_id:
                    st.session_state["active_job_id"] = ""
                st.rerun()
            except Exception as exc:
                st.error(f"Delete failed: {friendly_error_message(exc)}")


selected_job_id = str(st.session_state.get("selected_job_id", "")).strip()
active_job_id = str(st.session_state.get("active_job_id", "")).strip()
job_detail: dict[str, object] | None = None
job_detail_error: str | None = None
if selected_job_id:
    try:
        job_detail = get_backend_job(BACKEND_BASE_URL, selected_job_id, user_email=CURRENT_USER_EMAIL)
    except Exception as exc:
        job_detail_error = friendly_error_message(exc)

if job_detail is not None:
    job_status = str(job_detail.get("status", "")).strip() or "unknown"
    job_metadata = dict(job_detail.get("metadata") or {})
    job_client_prep = dict(job_metadata.get("client_prep") or {})
    detail_job_name = (
        str(job_metadata.get("job_name", "")).strip()
        or str(job_metadata.get("source_label", "")).strip()
        or selected_job_id
    )
    st.caption(
        f"Selected job: `{detail_job_name}` (`{selected_job_id}`) | Status: {job_status}"
    )
    render_localized_timestamp(
        "Created",
        str(job_detail.get("created_at", "") or ""),
        key=f"detail-{selected_job_id}",
    )
    if job_status in {"queued", "running"}:
        st.info("This job is still running in the background. Refresh the page or reopen Job History later to view the completed result.")
        if bool(st.session_state.get("job_history_auto_refresh", True)):
            st.markdown(
                """
                <script>
                window.setTimeout(function () {
                    window.location.reload();
                }, 5000);
                </script>
                """,
                unsafe_allow_html=True,
            )
    elif job_status == "failed":
        st.error(f"Job failed: {job_detail.get('error', 'Unknown backend error')}")
        backend_traceback = str(job_detail.get("traceback", "") or "").strip()
        if backend_traceback:
            with st.expander("Technical Details"):
                st.code(backend_traceback, language="text")
    elif job_status == "succeeded" and job_detail.get("result"):
        backend_result = dict(job_detail.get("result") or {})
        backend_result["job_id"] = selected_job_id
        display_result = build_display_result_from_backend_payload(
            backend_result,
            dict(job_metadata.get("planner_config") or {}),
            client_prep=job_client_prep,
        )
        st.session_state["display_result"] = dict(display_result)
        st.session_state["loaded_job_id"] = selected_job_id
        if active_job_id == selected_job_id:
            st.session_state["active_job_id"] = ""

if job_detail_error:
    st.warning(f"Job detail could not be loaded: {job_detail_error}")


loaded_job_id = str(st.session_state.get("loaded_job_id", "")).strip()
result = st.session_state.get("display_result") if (not selected_job_id or loaded_job_id == selected_job_id) else None
if result is not None:
    summary = result["summary"]
    structured_results = result["structured_results"]
    free_optimization_baseline_result = (
        result.get("free_optimization_baseline")
        or structured_results.get("free_optimization_baseline", {})
        or structured_results.get("original", {})
    )
    scenario_snapshots = [
        build_scenario_snapshot("Current Plan", structured_results.get("current_plan", {})),
        build_scenario_snapshot("Free Optimization Baseline", free_optimization_baseline_result),
        build_scenario_snapshot("Further Most", structured_results.get("further_most", {})),
        build_scenario_snapshot("Nearby Aggregated", structured_results.get("nearby", {})),
        build_scenario_snapshot("Further Most + Nearby Aggregate", structured_results.get("further_most_nearby", {})),
    ]
    free_optimization_baseline = scenario_snapshots[1]
    current_plan_assessment = summarize_current_plan_assessment(result.get("current_plan_assessment"))
    like_for_like_baseline = summarize_current_plan_assessment(result.get("like_for_like_baseline"))
    constrained_improvement_baseline = summarize_current_plan_assessment(result.get("constrained_improvement_baseline"))
    current_plan_like_for_like_comparison = result.get("current_plan_like_for_like_comparison") or {}
    current_plan_constrained_comparison = result.get("current_plan_constrained_comparison") or {}
    constrained_package_summaries = list(result.get("constrained_package_summaries") or [])
    current_plan_comparison = result.get("current_plan_comparison") or {}
    route_reallocation_analysis = result.get("route_reallocation_analysis") or {}
    nearby_private_access_analysis = result.get("nearby_private_access_analysis") or {}
    further_most_private_access_analysis = result.get("further_most_private_access_analysis") or {}
    further_most_nearby_private_access_analysis = result.get("further_most_nearby_private_access_analysis") or {}
    planner_config_used = dict(result.get("planner_config") or {})
    executive_findings = build_executive_findings(
        summary,
        current_plan_assessment,
        current_plan_like_for_like_comparison,
        current_plan_constrained_comparison,
        constrained_package_summaries,
        current_plan_comparison,
        route_reallocation_analysis,
        scenario_snapshots,
        len(result["geocode_warnings"]),
        int(planner_config_used.get("max_route_duration_minutes", 60)),
    )
    tabs = st.tabs(
        [
            "AI Audit Report",
            "Audit Evidence",
            "Baseline Scenarios",
            "Maps",
            "Diagnostics",
        ]
    )

    with tabs[0]:
        render_ai_audit_report_panel(
            job_detail=job_detail,
            selected_job_id=selected_job_id,
            current_user_email=CURRENT_USER_EMAIL,
            executive_findings=executive_findings,
            current_plan_assessment=current_plan_assessment,
            current_plan_comparison=current_plan_comparison,
            route_reallocation_analysis=route_reallocation_analysis,
            scenario_snapshots=scenario_snapshots,
        )
        st.divider()
        st.subheader("Deterministic Snapshot")
        top_1, top_2, top_3, top_4 = st.columns(4)
        top_1.metric("Uploaded Addresses", summary["uploaded_address_count"])
        top_2.metric("Original Stops", summary["original_valid_stops"])
        top_3.metric("Baseline Runtime", f"{result['elapsed_seconds']:.1f}s")
        if current_plan_comparison:
            top_4.metric(
                "Route Gap vs Baseline",
                int(current_plan_comparison.get("current_route_count", 0)),
                delta=f"{int(current_plan_comparison.get('route_gap', 0)):+d}",
            )
        else:
            best_snapshot = sorted(
                scenario_snapshots,
                key=lambda item: (
                    int(item["route_count"]),
                    float(item["avg_route_distance_km"]),
                    float(item["avg_route_duration_minutes"]),
                ),
            )[0]
            top_4.metric("Leanest Scenario", best_snapshot["name"], delta=f"{best_snapshot['route_count']} routes")

        st.caption(
            f"Client preprocessing: {float(result.get('client_prep_elapsed_seconds', 0.0)):.1f}s | "
            f"Backend compute: {float(result.get('backend_elapsed_seconds', 0.0)):.1f}s"
        )
        traffic_profile_used = str(result.get("traffic_profile_name") or planner_config_used.get("traffic_profile_name", "Off-Peak") or "Off-Peak")
        traffic_multiplier_used = float(result.get("traffic_time_multiplier") or TRAFFIC_PROFILE_MULTIPLIERS.get(traffic_profile_used, 1.0))
        traffic_profile_context = str(result.get("traffic_profile_context") or "Global default")
        st.caption(
            f"Traffic assumption: {traffic_profile_used} | "
            f"Travel-time multiplier: {traffic_multiplier_used:.2f}x ({traffic_profile_context}) | "
            "Distance unchanged"
        )
        st.caption(f"Service direction: {str(result.get('service_direction') or planner_config_used.get('service_direction', 'From School'))}")

        st.markdown("**Baseline Scenario Snapshot**")
        for column, snapshot in zip(st.columns(len(scenario_snapshots)), scenario_snapshots):
            with column:
                st.markdown(f"**{snapshot['name']}**")
                if not bool(snapshot["enabled"]):
                    st.info(str(snapshot["skipped_reason"]) or "Skipped")
                else:
                    st.metric("Routes", int(snapshot["route_count"]))
                    st.metric("Stops", int(snapshot["stop_count"]))
                    st.metric("Avg Route Distance", format_distance_km(float(snapshot["avg_route_distance_km"])))
                    st.metric("Avg Route Duration", format_duration_minutes(float(snapshot["avg_route_duration_minutes"])))
                    st.caption(
                        f"Load factor: {float(snapshot['avg_load_factor_pct']):.1f}% | "
                        f"Mix: {format_bus_mix(dict(snapshot['bus_mix']))}"
                    )

        st.markdown("**Key Findings**")
        st.markdown("\n".join(f"- {item}" for item in executive_findings))

    with tabs[1]:
        st.subheader("Audit Evidence")
        if not current_plan_assessment:
            st.info("This result does not include current-plan audit data.")
        else:
            audit_1, audit_2, audit_3, audit_4 = st.columns(4)
            audit_1.metric("Current Routes", current_plan_assessment["route_count"])
            audit_2.metric("Current Service Stops", current_plan_assessment["stop_count"])
            audit_3.metric("Avg Route Distance", format_distance_km(current_plan_assessment["avg_route_distance_km"]))
            audit_4.metric("Avg Route Duration", format_duration_minutes(current_plan_assessment["avg_route_duration_minutes"]))

            audit_5, audit_6, audit_7 = st.columns(3)
            audit_5.metric("Average Load", f"{current_plan_assessment['avg_load_factor_pct']:.1f}%")
            audit_6.metric("Low-Load Routes", current_plan_assessment["low_load_route_count"])
            audit_7.metric("Overlong Routes", current_plan_assessment["overlong_route_count"])

            st.caption(
                f"Service direction: {str(current_plan_assessment.get('service_direction') or planner_config_used.get('service_direction', 'From School'))} | "
                f"Current vehicle mix: {format_bus_mix(current_plan_assessment['bus_mix'])}"
            )

            audit_outline = build_current_plan_audit_outline(result)
            overview_section = next((section for section in audit_outline.get("sections", []) if section.get("heading") == "Overview"), {})
            improvement_section = next((section for section in audit_outline.get("sections", []) if section.get("heading") == "Improvement Paths"), {})
            actions_section = next((section for section in audit_outline.get("sections", []) if section.get("heading") == "Priority Actions"), {})

            st.markdown("**Audit Story**")
            story_col_1, story_col_2 = st.columns([1.2, 1.0], vertical_alignment="top")
            with story_col_1:
                overview_items = list(overview_section.get("items") or [])
                if overview_items:
                    st.markdown("**What stands out now**")
                    st.markdown("\n".join(f"- {item}" for item in overview_items))
            with story_col_2:
                improvement_items = list(improvement_section.get("items") or [])
                action_items = list(actions_section.get("items") or [])
                if improvement_items:
                    st.markdown("**Best improvement paths**")
                    st.markdown("\n".join(f"- {item}" for item in improvement_items[:3]))
                if action_items:
                    st.markdown("**Recommended next actions**")
                    st.markdown("\n".join(f"- {item}" for item in action_items[:3]))

            route_table_rows = build_current_route_table_rows(
                list(current_plan_assessment.get("route_summaries", [])),
                int(planner_config_used.get("max_route_duration_minutes", 60)),
            )
            if route_table_rows:
                with st.expander("Route-Level Diagnostics", expanded=False):
                    st.dataframe(pd.DataFrame(route_table_rows), width="stretch")

            if route_reallocation_analysis:
                weak_routes = list(route_reallocation_analysis.get("weak_routes") or [])
                reallocation_recommendations = list(route_reallocation_analysis.get("recommendations") or [])
                route_opportunity_profiles = list(route_reallocation_analysis.get("route_opportunity_profiles") or [])
                reallocation_summary = dict(route_reallocation_analysis.get("summary") or {})
                priority_recommendations = list(reallocation_summary.get("priority_recommendations") or reallocation_recommendations[:3])
                st.markdown("**Network Adjustment Signals**")
                st.caption(
                    "This section summarizes small, explainable route-to-route adjustments that could improve the network without a full redesign."
                )
                realloc_1, realloc_2, realloc_3, realloc_4 = st.columns(4)
                realloc_1.metric("Weak Routes", len(weak_routes))
                realloc_2.metric(
                    "Actionable Weak Routes",
                    int(reallocation_summary.get("actionable_weak_route_count", 0) or 0),
                )
                realloc_3.metric(
                    "Removal Paths",
                    int(reallocation_summary.get("route_removal_candidate_count", 0) or 0),
                )
                realloc_4.metric(
                    "Consolidation Paths",
                    int(reallocation_summary.get("route_consolidation_candidate_count", 0) or 0),
                )

                realloc_5, realloc_6, realloc_7, realloc_8 = st.columns(4)
                realloc_5.metric(
                    "Removable Now",
                    int(reallocation_summary.get("route_removable_now_count", 0) or 0),
                )
                realloc_6.metric(
                    "Candidate Route Pairs",
                    int(route_reallocation_analysis.get("candidate_route_pair_count", 0) or 0),
                )
                realloc_7.metric(
                    "Best Time Saving",
                    format_duration_minutes(float(reallocation_summary.get("best_network_time_saving_s", 0.0) or 0.0) / 60.0),
                )
                realloc_8.metric(
                    "Best Distance Saving",
                    format_distance_km(float(reallocation_summary.get("best_network_distance_saving_m", 0.0) or 0.0) / 1000.0),
                )

                if weak_routes:
                    with st.expander("Weak Route Review", expanded=False):
                        st.dataframe(pd.DataFrame(build_weak_route_table_rows(weak_routes)), width="stretch")

                if route_opportunity_profiles:
                    with st.expander("Route-Level Action Signals", expanded=False):
                        st.caption(
                            "This table stabilizes the move-level analysis into route-level signals. "
                            "A route only gets promoted toward consolidation or removal when multiple compatible local moves point in the same direction."
                        )
                        st.dataframe(pd.DataFrame(build_route_opportunity_profile_rows(route_opportunity_profiles)), width="stretch")

                if priority_recommendations:
                    st.markdown("**Priority Route-to-Route Actions**")
                    st.markdown(
                        "\n".join(
                            f"- [{str(item.get('route_action_label', '')).strip() or 'Local improvement'}] "
                            f"{str(item.get('explanation', '')).strip()} "
                            f"Estimated time saving: {float(item.get('network_total_duration_saving_s', 0.0) or 0.0) / 60.0:.1f} min; "
                            f"estimated distance saving: {float(item.get('network_total_distance_saving_m', 0.0) or 0.0) / 1000.0:.1f} km."
                            for item in priority_recommendations
                        )
                    )

                if reallocation_recommendations:
                    with st.expander("Detailed Route-to-Route Move Review", expanded=False):
                        st.dataframe(pd.DataFrame(build_reallocation_table_rows(reallocation_recommendations)), width="stretch")
                else:
                    st.caption(
                        "No route-to-route transfer opportunities met the current filters. "
                        "This usually means the current weak routes cannot be improved by small local moves alone."
                    )

            st.markdown("**Benchmark Readout**")
            benchmark_cols = st.columns(3 if free_optimization_baseline else 2)
            if like_for_like_baseline:
                with benchmark_cols[0]:
                    st.markdown("**Like-for-Like**")
                    st.metric("Avg Route Distance", format_distance_km(like_for_like_baseline["avg_route_distance_km"]))
                    st.metric("Avg Route Duration", format_duration_minutes(like_for_like_baseline["avg_route_duration_minutes"]))
                    st.caption(f"Load: {like_for_like_baseline['avg_load_factor_pct']:.1f}%")
            if constrained_improvement_baseline:
                with benchmark_cols[1 if like_for_like_baseline else 0]:
                    st.markdown("**Constrained**")
                    st.metric("Avg Route Distance", format_distance_km(constrained_improvement_baseline["avg_route_distance_km"]))
                    st.metric("Avg Route Duration", format_duration_minutes(constrained_improvement_baseline["avg_route_duration_minutes"]))
                    st.caption(f"Load: {constrained_improvement_baseline['avg_load_factor_pct']:.1f}%")
            if free_optimization_baseline:
                target_index = 2 if like_for_like_baseline and constrained_improvement_baseline else (1 if (like_for_like_baseline or constrained_improvement_baseline) else 0)
                with benchmark_cols[target_index]:
                    st.markdown("**Free Optimization**")
                    st.metric("Avg Route Distance", format_distance_km(float(free_optimization_baseline.get("avg_route_distance_km", 0.0))))
                    st.metric("Avg Route Duration", format_duration_minutes(float(free_optimization_baseline.get("avg_route_duration_minutes", 0.0))))
                    st.caption(f"Load: {float(free_optimization_baseline.get('avg_load_factor_pct', 0.0)):.1f}%")

            if like_for_like_baseline:
                st.caption(
                    "Like-for-like keeps route count, stop allocation, and bus mix fixed, and only improves stop order inside each route."
                )
                like_1, like_2, like_3, like_4 = st.columns(4)
                like_1.metric("Baseline Routes", like_for_like_baseline["route_count"])
                like_2.metric("Avg Route Distance", format_distance_km(like_for_like_baseline["avg_route_distance_km"]))
                like_3.metric("Avg Route Duration", format_duration_minutes(like_for_like_baseline["avg_route_duration_minutes"]))
                like_4.metric("Baseline Avg Load", f"{like_for_like_baseline['avg_load_factor_pct']:.1f}%")
                st.caption(f"Like-for-like vehicle mix: {format_bus_mix(like_for_like_baseline['bus_mix'])}")
                if current_plan_like_for_like_comparison:
                    cmp_1, cmp_2, cmp_3, cmp_4 = st.columns(4)
                    cmp_1.metric(
                        "Route Count Gap",
                        int(current_plan_like_for_like_comparison.get("current_route_count", 0)),
                        delta=f"{int(current_plan_like_for_like_comparison.get('route_gap', 0)):+d} vs like-for-like",
                    )
                    cmp_2.metric(
                        "Current Avg Distance",
                        format_distance_km(float(current_plan_like_for_like_comparison.get("current_avg_route_distance_m", 0.0)) / 1000.0),
                        delta=f"{float(current_plan_like_for_like_comparison.get('avg_distance_gap_pct', 0.0)):+.1f}%",
                    )
                    cmp_3.metric(
                        "Current Avg Duration",
                        format_duration_minutes(float(current_plan_like_for_like_comparison.get("current_avg_route_duration_s", 0.0)) / 60.0),
                        delta=f"{float(current_plan_like_for_like_comparison.get('avg_duration_gap_pct', 0.0)):+.1f}%",
                    )
                    cmp_4.metric(
                        "Baseline Avg Load",
                        f"{float(current_plan_like_for_like_comparison.get('baseline_avg_load_factor', 0.0)) * 100.0:.1f}%",
                    )
                    like_for_like_recommendations = list(current_plan_like_for_like_comparison.get("recommendations") or [])
                    if like_for_like_recommendations:
                        st.markdown("**Like-for-Like Findings**")
                        st.markdown("\n".join(f"- {item}" for item in like_for_like_recommendations))

            if constrained_improvement_baseline:
                st.caption(
                    "Constrained improvement applies a small set of high-confidence route-to-route transfers instead of redesigning the whole network."
                )
                constrained_1, constrained_2, constrained_3, constrained_4 = st.columns(4)
                constrained_1.metric("Baseline Routes", constrained_improvement_baseline["route_count"])
                constrained_2.metric("Avg Route Distance", format_distance_km(constrained_improvement_baseline["avg_route_distance_km"]))
                constrained_3.metric("Avg Route Duration", format_duration_minutes(constrained_improvement_baseline["avg_route_duration_minutes"]))
                constrained_4.metric("Baseline Avg Load", f"{constrained_improvement_baseline['avg_load_factor_pct']:.1f}%")
                st.caption(f"Constrained-improvement vehicle mix: {format_bus_mix(constrained_improvement_baseline['bus_mix'])}")
                if current_plan_constrained_comparison:
                    ccmp_1, ccmp_2, ccmp_3, ccmp_4 = st.columns(4)
                    ccmp_1.metric(
                        "Route Count Gap",
                        int(current_plan_constrained_comparison.get("current_route_count", 0)),
                        delta=f"{int(current_plan_constrained_comparison.get('route_gap', 0)):+d} vs constrained",
                    )
                    ccmp_2.metric(
                        "Current Avg Distance",
                        format_distance_km(float(current_plan_constrained_comparison.get("current_avg_route_distance_m", 0.0)) / 1000.0),
                        delta=f"{float(current_plan_constrained_comparison.get('avg_distance_gap_pct', 0.0)):+.1f}%",
                    )
                    ccmp_3.metric(
                        "Current Avg Duration",
                        format_duration_minutes(float(current_plan_constrained_comparison.get("current_avg_route_duration_s", 0.0)) / 60.0),
                        delta=f"{float(current_plan_constrained_comparison.get('avg_duration_gap_pct', 0.0)):+.1f}%",
                    )
                    ccmp_4.metric(
                        "Baseline Avg Load",
                        f"{float(current_plan_constrained_comparison.get('baseline_avg_load_factor', 0.0)) * 100.0:.1f}%",
                    )
                    constrained_recommendations = list(current_plan_constrained_comparison.get("recommendations") or [])
                    if constrained_recommendations:
                        st.markdown("**Constrained Improvement Findings**")
                        st.markdown("\n".join(f"- {item}" for item in constrained_recommendations))
                        if constrained_package_summaries:
                            top_package = constrained_package_summaries[0]
                            target_minutes = int(planner_config_used.get("max_route_duration_minutes", 60))
                            effective_target_minutes = effective_route_duration_limit_minutes(target_minutes)
                            projected_to_duration_min = float(top_package.get("projected_to_route_duration_s", 0.0) or 0.0) / 60.0
                            projected_to_load_pct = float(top_package.get("projected_to_route_load_factor", 0.0) or 0.0) * 100.0
                            if projected_to_duration_min <= effective_target_minutes and projected_to_load_pct <= 85.0:
                                st.markdown(
                                    f"- The leading package is a practical merge candidate because `{top_package.get('to_route_id')}` still lands near "
                                    f"{projected_to_duration_min:.1f} min and {projected_to_load_pct:.1f}% load after absorbing the extra stops."
                                )
                            elif projected_to_duration_min <= effective_target_minutes + 10 and projected_to_load_pct <= 92.0:
                                st.markdown(
                                    f"- The leading package is feasible but tight because `{top_package.get('to_route_id')}` would rise to "
                                    f"{projected_to_duration_min:.1f} min and {projected_to_load_pct:.1f}% load."
                                )
                            else:
                                st.markdown(
                                    f"- The leading package is not yet a clean merge because `{top_package.get('to_route_id')}` would be pushed to "
                                    f"{projected_to_duration_min:.1f} min and {projected_to_load_pct:.1f}% load."
                                )
                    elif constrained_package_summaries:
                        st.markdown("**Constrained Improvement Findings**")
                        fallback_findings = [
                            str(constrained_package_summaries[0].get("package_summary", "")).strip()
                        ]
                        if len(constrained_package_summaries) > 1:
                            fallback_findings.append(
                                f"{len(constrained_package_summaries)} constrained transfer package(s) were selected in total."
                            )
                        st.markdown("\n".join(f"- {item}" for item in fallback_findings if item))

                constrained_selected_moves = list(result.get("constrained_selected_moves") or [])
                if constrained_selected_moves:
                    constrained_package_rows = build_constrained_package_rows(
                        constrained_selected_moves,
                        constrained_package_summaries,
                    )
                    if constrained_package_rows:
                        with st.expander("Selected Constrained Transfer Packages", expanded=False):
                            st.caption(
                                "These are the grouped transfer actions the system actually applied to build the constrained-improvement baseline."
                            )
                            st.dataframe(pd.DataFrame(constrained_package_rows), use_container_width=True)
                            constrained_package_outcome_rows = build_constrained_package_outcome_rows(
                                constrained_package_summaries,
                                int(planner_config_used.get("max_route_duration_minutes", 60)),
                            )
                            if constrained_package_outcome_rows:
                                merge_readiness_summary = summarize_package_merge_readiness(
                                    constrained_package_summaries,
                                    int(planner_config_used.get("max_route_duration_minutes", 60)),
                                )
                                readiness_col_1, readiness_col_2, readiness_col_3 = st.columns(3)
                                readiness_col_1.metric(
                                    "Safe Merge Candidates",
                                    int(merge_readiness_summary.get("Safe merge candidate", 0) or 0),
                                )
                                readiness_col_2.metric(
                                    "Monitor Receiving Route",
                                    int(merge_readiness_summary.get("Monitor receiving route", 0) or 0),
                                )
                                readiness_col_3.metric(
                                    "Receiving Route Stressed",
                                    int(merge_readiness_summary.get("Receiving route stressed", 0) or 0),
                                )
                                st.markdown("**Post-Package Route Outcomes**")
                                st.caption(
                                    "This compares the sending route and receiving route before and after each selected package."
                                )
                                st.dataframe(pd.DataFrame(constrained_package_outcome_rows), use_container_width=True)

            if free_optimization_baseline:
                st.caption(
                    "Free optimization is the upper-bound benchmark: the system can regroup planning stops more freely while respecting the chosen service direction."
                )
                free_1, free_2, free_3, free_4 = st.columns(4)
                free_1.metric("Baseline Routes", int(free_optimization_baseline.get("route_count", 0)))
                free_2.metric("Avg Route Distance", format_distance_km(float(free_optimization_baseline.get("avg_route_distance_km", 0.0))))
                free_3.metric("Avg Route Duration", format_duration_minutes(float(free_optimization_baseline.get("avg_route_duration_minutes", 0.0))))
                free_4.metric("Baseline Avg Load", f"{float(free_optimization_baseline.get('avg_load_factor_pct', 0.0)):.1f}%")
                st.caption(
                    f"Actual vehicle mix: {format_bus_mix(dict(free_optimization_baseline.get('bus_mix', {})))}"
                )
                st.caption(
                    "Configured free-baseline fleet limit: "
                    f"{format_bus_type_max_counts(dict(free_optimization_baseline_result.get('configured_bus_type_max_counts', {})))}"
                )
                st.caption(
                    "Input free-baseline vehicle ratio: "
                    f"{format_vehicle_ratio(dict(free_optimization_baseline_result.get('configured_vehicle_ratio', {})))}"
                )
                free_routes = build_route_table_rows(list(free_optimization_baseline.get("routes") or []))
                if free_routes:
                    with st.expander("Free Optimization Route Table", expanded=False):
                        st.dataframe(pd.DataFrame(free_routes), width="stretch")
                render_free_baseline_template_download(
                    free_optimization_baseline_result,
                    service_direction=str(result.get("service_direction") or planner_config_used.get("service_direction", "From School")),
                    key=f"free-template-audit-{loaded_job_id or selected_job_id}",
                )

            if current_plan_comparison:
                st.markdown("**Current Plan vs Free Optimization Baseline**")
                compare_1, compare_2, compare_3, compare_4 = st.columns(4)
                compare_1.metric(
                    "Current Route Count",
                    int(current_plan_comparison.get("current_route_count", 0)),
                    delta=f"{int(current_plan_comparison.get('route_gap', 0)):+d} vs free baseline",
                )
                compare_2.metric(
                    "Current Avg Distance",
                    format_distance_km(float(current_plan_comparison.get("current_avg_route_distance_m", 0.0)) / 1000.0),
                    delta=f"{float(current_plan_comparison.get('avg_distance_gap_pct', 0.0)):+.1f}%",
                )
                compare_3.metric(
                    "Current Avg Duration",
                    format_duration_minutes(float(current_plan_comparison.get("current_avg_route_duration_s", 0.0)) / 60.0),
                    delta=f"{float(current_plan_comparison.get('avg_duration_gap_pct', 0.0)):+.1f}%",
                )
                compare_4.metric(
                    "Current Avg Load",
                    f"{float(current_plan_comparison.get('current_avg_load_factor', 0.0)) * 100.0:.1f}%",
                )
                st.caption(
                    "Current vs baseline bus mix: "
                    f"Current [{format_bus_mix(dict(current_plan_comparison.get('current_bus_mix', {})))}] | "
                    f"Baseline [{format_bus_mix(dict(current_plan_comparison.get('baseline_bus_mix', {})))}]"
                )
                comparison_recommendations = list(current_plan_comparison.get("recommendations") or [])
                if comparison_recommendations:
                    st.markdown("**Comparison Findings**")
                    st.markdown("\n".join(f"- {item}" for item in comparison_recommendations))

            st.divider()
            st.download_button(
                label="Download Current Plan Audit Report (.pdf)",
                data=build_current_plan_audit_pdf_bytes(result),
                file_name="current_plan_audit_report.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

    with tabs[2]:
        st.subheader("Baseline Scenarios")
        render_free_baseline_template_download(
            free_optimization_baseline_result,
            service_direction=str(result.get("service_direction") or planner_config_used.get("service_direction", "From School")),
            key=f"free-template-scenarios-top-{loaded_job_id or selected_job_id}",
        )
        for snapshot in scenario_snapshots:
            with st.container():
                st.markdown(f"**{snapshot['name']}**")
                if not bool(snapshot["enabled"]):
                    st.info(str(snapshot["skipped_reason"]) or "Skipped")
                else:
                    row_1, row_2, row_3, row_4, row_5 = st.columns(5)
                    row_1.metric("Routes", int(snapshot["route_count"]))
                    row_2.metric("Stops", int(snapshot["stop_count"]))
                    row_3.metric("Avg Route Distance", format_distance_km(float(snapshot["avg_route_distance_km"])))
                    row_4.metric("Avg Route Duration", format_duration_minutes(float(snapshot["avg_route_duration_minutes"])))
                    row_5.metric("Avg Load", f"{float(snapshot['avg_load_factor_pct']):.1f}%")
                    st.caption(f"Vehicle mix: {format_bus_mix(dict(snapshot['bus_mix']))}")
                    route_rows = build_route_table_rows(list(snapshot["routes"]))
                    if route_rows:
                        with st.expander(f"{snapshot['name']} Route Table", expanded=False):
                            st.dataframe(pd.DataFrame(route_rows), width="stretch")
                    if snapshot["name"] == "Free Optimization Baseline":
                        render_free_baseline_template_download(
                            free_optimization_baseline_result,
                            service_direction=str(result.get("service_direction") or planner_config_used.get("service_direction", "From School")),
                            key=f"free-template-scenarios-card-{loaded_job_id or selected_job_id}",
                        )

        nearby_private_access_rows = build_private_access_rows(nearby_private_access_analysis)
        if nearby_private_access_rows:
            nearby_private_access_summary = dict(nearby_private_access_analysis.get("summary") or {})
            st.divider()
            st.markdown("**Nearby Aggregated Private Access v1**")
            st.caption(
                "This first-pass view belongs to the nearby-aggregated baseline. It estimates which original rider locations "
                "would need a short private drive to reach the shared nearby pickup stop created by aggregation."
            )
            nearby_out_1, nearby_out_2, nearby_out_3 = st.columns(3)
            nearby_out_1.metric(
                "Private-Drive Riders",
                int(nearby_private_access_summary.get("candidate_stop_count", 0) or 0),
            )
            nearby_out_2.metric(
                "Avg Private Drive",
                format_duration_minutes(float(nearby_private_access_summary.get("avg_private_drive_time_s", 0.0) or 0.0) / 60.0),
            )
            nearby_out_3.metric(
                "Max Private Drive",
                format_duration_minutes(float(nearby_private_access_summary.get("max_private_drive_time_s", 0.0) or 0.0) / 60.0),
            )
            cluster_center_count = int(nearby_private_access_summary.get("cluster_center_count", 0) or 0)
            if cluster_center_count:
                st.caption(f"Nearby cluster centers carrying private-drive riders: {cluster_center_count}")
            st.caption(
                f"Furthest current v1 rider: {str(nearby_private_access_summary.get('furthest_stop_address', '')).strip() or 'N/A'} -> "
                f"{str(nearby_private_access_summary.get('furthest_pickup_address', '')).strip() or 'N/A'} "
                f"via {str(nearby_private_access_summary.get('furthest_pickup_route_id', '')).strip() or 'N/A'}."
            )
            cluster_rows = build_private_access_cluster_rows(nearby_private_access_analysis)
            if cluster_rows:
                st.markdown("**Nearby Cluster Centers**")
                st.caption(
                    "Each row is a nearby-aggregated pickup center. The clustered-address list shows which original rider locations "
                    "would drive to that center and how far/time that access leg would take."
                )
                st.dataframe(pd.DataFrame(cluster_rows), width="stretch")
            st.markdown("**Private-Drive Rider Detail**")
            st.dataframe(pd.DataFrame(nearby_private_access_rows), width="stretch")

        further_most_private_access_rows = build_private_access_rows(further_most_private_access_analysis)
        if further_most_private_access_rows:
            further_most_summary = dict(further_most_private_access_analysis.get("summary") or {})
            st.divider()
            st.markdown("**Further Most**")
            st.caption(
                "This scenario starts from the free-optimization baseline and converts all stops beyond the xx-minute mark "
                "into private-drive stops that connect back to the xx-minute-mark pickup."
            )
            fm_1, fm_2, fm_3 = st.columns(3)
            fm_1.metric(
                "Private Drive Stops",
                int(further_most_summary.get("private_drive_stop_count", 0) or 0),
            )
            fm_2.metric(
                "Avg Private Drive",
                format_duration_minutes(float(further_most_summary.get("avg_private_drive_time_s", 0.0) or 0.0) / 60.0),
            )
            fm_3.metric(
                "Max Private Drive",
                format_duration_minutes(float(further_most_summary.get("max_private_drive_time_s", 0.0) or 0.0) / 60.0),
            )
            st.caption(
                f"Furthest current stop: {str(further_most_summary.get('furthest_stop_address', '')).strip() or 'N/A'} -> "
                f"{str(further_most_summary.get('furthest_pickup_address', '')).strip() or 'N/A'} "
                f"via {str(further_most_summary.get('furthest_pickup_route_id', '')).strip() or 'N/A'}."
            )
            st.dataframe(pd.DataFrame(further_most_private_access_rows), width="stretch")

        further_most_nearby_private_access_rows = build_private_access_rows(further_most_nearby_private_access_analysis)
        if further_most_nearby_private_access_rows:
            further_most_nearby_summary = dict(further_most_nearby_private_access_analysis.get("summary") or {})
            st.divider()
            st.markdown("**Further Most + Nearby Aggregate**")
            st.caption(
                "This scenario starts from the nearby-aggregated baseline and converts all stops beyond the xx-minute mark "
                "into private-drive stops that connect back to the xx-minute-mark pickup."
            )
            fmn_1, fmn_2, fmn_3 = st.columns(3)
            fmn_1.metric(
                "Private Drive Stops",
                int(further_most_nearby_summary.get("private_drive_stop_count", 0) or 0),
            )
            fmn_2.metric(
                "Avg Private Drive",
                format_duration_minutes(float(further_most_nearby_summary.get("avg_private_drive_time_s", 0.0) or 0.0) / 60.0),
            )
            fmn_3.metric(
                "Max Private Drive",
                format_duration_minutes(float(further_most_nearby_summary.get("max_private_drive_time_s", 0.0) or 0.0) / 60.0),
            )
            st.caption(
                f"Furthest current stop: {str(further_most_nearby_summary.get('furthest_stop_address', '')).strip() or 'N/A'} -> "
                f"{str(further_most_nearby_summary.get('furthest_pickup_address', '')).strip() or 'N/A'} "
                f"via {str(further_most_nearby_summary.get('furthest_pickup_route_id', '')).strip() or 'N/A'}."
            )
            st.dataframe(pd.DataFrame(further_most_nearby_private_access_rows), width="stretch")

    original_html_path = Path(result["original_html"])
    current_plan_html_path = Path(result["current_plan_html"])
    subway_html_path = Path(result["subway_html"])
    nearby_html_path = Path(result["nearby_html"])
    further_most_html_path = Path(result["further_most_html"])
    further_most_nearby_html_path = Path(result["further_most_nearby_html"])
    with tabs[3]:
        st.subheader("Maps")
        available_map_options = ["Free Optimization Baseline"]
        if current_plan_assessment:
            available_map_options.insert(0, "Current Plan")
        if bool(scenario_snapshots[2]["enabled"]):
            available_map_options.append("Further Most")
        if bool(scenario_snapshots[3]["enabled"]):
            available_map_options.append("Nearby Aggregated")
        if bool(scenario_snapshots[4]["enabled"]):
            available_map_options.append("Further Most + Nearby Aggregate")

        download_columns = st.columns(max(1, len(available_map_options)))
        for column, option in zip(download_columns, available_map_options):
            with column:
                if option == "Current Plan":
                    st.download_button("Download Current Plan HTML", data=current_plan_html_path.read_text(encoding="utf-8"), file_name=current_plan_html_path.name, mime="text/html", width="stretch")
                elif option == "Free Optimization Baseline":
                    st.download_button("Download Free Baseline HTML", data=original_html_path.read_text(encoding="utf-8"), file_name=original_html_path.name, mime="text/html", width="stretch")
                elif option == "Subway Aggregated":
                    st.download_button("Download Subway HTML", data=subway_html_path.read_text(encoding="utf-8"), file_name=subway_html_path.name, mime="text/html", width="stretch")
                elif option == "Nearby Aggregated":
                    st.download_button("Download Nearby HTML", data=nearby_html_path.read_text(encoding="utf-8"), file_name=nearby_html_path.name, mime="text/html", width="stretch")
                elif option == "Further Most":
                    st.download_button("Download Further Most HTML", data=further_most_html_path.read_text(encoding="utf-8"), file_name=further_most_html_path.name, mime="text/html", width="stretch")
                else:
                    st.download_button("Download Further Most Nearby HTML", data=further_most_nearby_html_path.read_text(encoding="utf-8"), file_name=further_most_nearby_html_path.name, mime="text/html", width="stretch")

        selected_map = st.segmented_control(
            "Optimized Baseline Map View",
            options=available_map_options,
            default=available_map_options[0],
        )
        if selected_map == "Current Plan":
            render_html_preview(current_plan_html_path)
        elif selected_map == "Free Optimization Baseline":
            render_html_preview(original_html_path)
        elif selected_map == "Subway Aggregated":
            render_html_preview(subway_html_path)
        elif selected_map == "Further Most":
            render_html_preview(further_most_html_path)
        elif selected_map == "Nearby Aggregated":
            render_html_preview(nearby_html_path)
        else:
            render_html_preview(further_most_nearby_html_path)

    with tabs[4]:
        st.subheader("Diagnostics")
        geocode_warnings = result["geocode_warnings"]
        excluded_stops = result["excluded_stops"]
        diag_tab_1, diag_tab_2, diag_tab_3 = st.tabs(["Coordinate Warnings", "Excluded Stops", "Run Log"])
        with diag_tab_1:
            if not geocode_warnings:
                st.success("No coordinate parsing warnings were detected in this run.")
            else:
                st.caption("These rows were not auto-corrected. Review the original input address text and fix them in the workbook before rerunning.")
                st.dataframe(pd.DataFrame(geocode_warnings), width="stretch")
        with diag_tab_2:
            if not excluded_stops:
                st.success("No stops were excluded in this run.")
            else:
                st.dataframe(pd.DataFrame(excluded_stops), width="stretch")
        with diag_tab_3:
            st.code(result["logs"] or "No logs captured.", language="text")
