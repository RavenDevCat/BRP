from __future__ import annotations

from datetime import datetime
import os
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import tempfile
from pathlib import Path

from client_core import (
    PlannerConfig,
    friendly_error_message,
    prepare_client_payload,
    read_current_plan_from_excel,
    submit_job_to_backend,
)
from demand_clustering import (
    build_demand_cluster_map_html,
    build_demand_clusters,
    cluster_points_to_dataframe,
    demand_clusters_to_dataframe,
    split_cluster_result_by_route_limit,
)
from demand_global_optimizer import build_global_ortools_plan
from demand_routing import (
    build_generated_plan_workbook_bytes,
    build_legacy_route_plan_workbook_bytes,
    build_osrm_route_preview,
    build_route_preview_map_html,
    route_preview_stop_detail_to_dataframe,
    route_preview_to_dataframe,
)
from demand_input import (
    build_demand_geocode_map_html,
    build_demand_template_workbook_bytes,
    demand_geocode_results_to_dataframe,
    demand_riders_to_dataframe,
    geocode_demand_workbook,
    read_demand_workbook,
)
from fleet_selector import estimate_vehicle_mix_for_groups, select_vehicle_for_group
from planning_assumptions import PLANNING_MODES, get_planning_assumptions
from vehicle_catalog import get_vehicle_catalog


MODE_LABELS: dict[str, str] = {
    "balanced": "Balanced",
    "cost_saver": "Cost Saver",
    "comfort_saver": "Comfort Saver",
}

BACKEND_BASE_URL = os.environ.get("BRP_BACKEND_BASE_URL", "http://127.0.0.1:8001")
BACKEND_TIMEOUT_SECONDS = int(os.environ.get("BRP_BACKEND_TIMEOUT_SECONDS", "1800") or 1800)
DEV_USER_EMAIL = os.environ.get("BRP_DEV_USER_EMAIL", "local@brp.dev").strip().lower()


def _current_user_email() -> str:
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


def _service_direction_label(service_direction: str) -> str:
    return "To School" if str(service_direction).strip().lower() == "to_school" else "From School"


def _parse_rider_counts(raw_value: str) -> list[int]:
    values: list[int] = []
    for chunk in str(raw_value or "").replace("\n", ",").split(","):
        text = chunk.strip()
        if not text:
            continue
        try:
            value = int(float(text))
        except ValueError as exc:
            raise ValueError(f"Invalid rider group value: {text!r}") from exc
        if value <= 0:
            raise ValueError(f"Rider group values must be greater than zero: {text!r}")
        values.append(value)
    if not values:
        raise ValueError("Enter at least one rider group.")
    return values


def _format_load_factor(value: object) -> str:
    try:
        return f"{float(value) * 100.0:.1f}%"
    except Exception:
        return "N/A"


def _build_catalog_dataframe(market: str, monitor_seats: int) -> pd.DataFrame:
    rows = []
    for vehicle in get_vehicle_catalog(market, monitor_seats=monitor_seats):
        rows.append(
            {
                "Vehicle": vehicle.get("display_name"),
                "Category": vehicle.get("category"),
                "Propulsion": vehicle.get("propulsion"),
                "Listed seats": vehicle.get("listed_seats"),
                "Monitor seats": vehicle.get("monitor_seats"),
                "Student capacity": vehicle.get("student_capacity"),
                "Notes": vehicle.get("notes"),
            }
        )
    return pd.DataFrame(rows)


def _build_selection_dataframe(rider_counts: list[int], market: str, mode: str, monitor_seats: int) -> pd.DataFrame:
    rows = []
    for rider_count in rider_counts:
        selection = select_vehicle_for_group(
            rider_count,
            market=market,
            mode=mode,
            monitor_seats=monitor_seats,
        )
        selected = selection.selected_vehicle or {}
        rows.append(
            {
                "Riders": rider_count,
                "Recommended vehicle": selected.get("display_name", "No feasible vehicle"),
                "Student capacity": selected.get("student_capacity", ""),
                "Load factor": _format_load_factor(selected.get("load_factor")),
                "Empty seats": selected.get("empty_seats", ""),
                "Feasible options": len(selection.feasible_options),
                "Rejected options": len(selection.rejected_options),
            }
        )
    return pd.DataFrame(rows)


def _planner_config_from_route_preview(route_preview: dict[str, object], service_direction: str, max_route_duration_minutes: int) -> PlannerConfig:
    fleet_items: dict[str, dict[str, int | str]] = {}
    for route in list(route_preview.get("routes") or []):
        vehicle = dict(route.get("selected_vehicle") or {})
        bus_type = str(vehicle.get("display_name", "")).strip() or "Generated Bus"
        capacity = int(vehicle.get("student_capacity", vehicle.get("capacity", 0)) or 0)
        fleet_item = fleet_items.setdefault(
            bus_type,
            {
                "bus_type": bus_type,
                "seat_count": capacity,
                "vehicle_count": 0,
            },
        )
        fleet_item["vehicle_count"] = int(fleet_item["vehicle_count"]) + 1

    sorted_fleet = sorted(
        fleet_items.values(),
        key=lambda item: (int(item.get("seat_count", 0) or 0), str(item.get("bus_type", "")).lower()),
        reverse=True,
    )
    slots = list(sorted_fleet[:3])
    while len(slots) < 3:
        slots.append({"bus_type": f"Generated Slot {len(slots) + 1}", "seat_count": 0, "vehicle_count": 0})

    large, mid, small = slots[0], slots[1], slots[2]
    return PlannerConfig(
        large_bus_name=str(large["bus_type"]),
        mid_bus_name=str(mid["bus_type"]),
        small_bus_name=str(small["bus_type"]),
        large_bus_capacity=int(large["seat_count"]),
        mid_bus_capacity=int(mid["seat_count"]),
        small_bus_capacity=int(small["seat_count"]),
        large_bus_max_count=int(large["vehicle_count"]),
        mid_bus_max_count=int(mid["vehicle_count"]),
        small_bus_max_count=int(small["vehicle_count"]),
        free_baseline_large_bus_ratio=float(max(0, int(large["vehicle_count"]))),
        free_baseline_mid_bus_ratio=float(max(0, int(mid["vehicle_count"]))),
        free_baseline_small_bus_ratio=float(max(0, int(small["vehicle_count"]))),
        max_route_duration_minutes=int(max_route_duration_minutes),
        service_direction=_service_direction_label(service_direction),
        include_subway_aggregation_scenario=False,
        include_nearby_aggregation_scenario=False,
    )


def _submit_generated_plan_as_job(route_preview: dict[str, object], *, job_name: str, max_route_duration_minutes: int) -> dict[str, object]:
    workbook_bytes = build_legacy_route_plan_workbook_bytes(route_preview)
    service_direction = str(dict(route_preview.get("summary") or {}).get("service_direction", "to_school"))
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as temp_file:
            temp_file.write(workbook_bytes)
            temp_path = Path(temp_file.name)
        route_plan_for_legacy_parser = read_current_plan_from_excel(temp_path, service_direction=_service_direction_label(service_direction))
        input_records = [dict(item) for item in list(route_plan_for_legacy_parser.get("input_records") or [])]
        config = _planner_config_from_route_preview(
            route_preview,
            service_direction,
            max_route_duration_minutes,
        )
        payload = prepare_client_payload(
            input_records,
            current_plan_data=route_plan_for_legacy_parser,
            config=config,
        )
        metadata = {
            "source_mode": "fleet_planner_preview",
            "source_label": "Fleet Planner Global OR-Tools Plan",
            "job_name": job_name,
            "generated_from": "Fleet Planner Preview",
            "service_direction": _service_direction_label(service_direction),
            "plan_type": "generated_auto_plan",
        }
        return submit_job_to_backend(
            payload["prepared_payload"],
            config,
            BACKEND_BASE_URL,
            metadata=metadata,
            user_email=_current_user_email(),
            timeout_seconds=60,
        )
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


def render_fleet_planner_page() -> None:
    st.title("Fleet Planner Preview")
    st.caption(
        "Preview how BRP chooses vehicle types from rider counts before the full address clustering and routing workflow is connected."
    )

    if st.button("Back to Main Planner", key="back_to_main_planner_from_fleet_preview"):
        st.query_params.clear()
        st.rerun()

    st.download_button(
        "Download Demand Template",
        data=build_demand_template_workbook_bytes(),
        file_name="brp_demand_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    uploaded_demand_file = st.file_uploader(
        "Upload Demand Workbook (optional)",
        type=["xlsx"],
        help="Use this for address + student-count demand input. The preview will summarize it without running geocoding or routing yet.",
        key="fleet_preview_demand_upload",
    )
    upload_signature = (
        uploaded_demand_file.name,
        uploaded_demand_file.size,
    ) if uploaded_demand_file is not None else None
    if st.session_state.get("fleet_preview_demand_signature") != upload_signature:
        st.session_state["fleet_preview_demand_signature"] = upload_signature
        st.session_state.pop("fleet_preview_geocode_result", None)

    control_col, summary_col = st.columns([1.1, 1.4])
    with control_col:
        st.subheader("Scenario")
        market_label = st.radio("Market", options=["KR", "CN"], horizontal=True, key="fleet_preview_market")
        mode_label = st.selectbox(
            "Planning Mode",
            options=list(PLANNING_MODES),
            format_func=lambda value: MODE_LABELS.get(value, value.title()),
            key="fleet_preview_mode",
        )
        monitor_seats = st.number_input(
            "Bus Monitor Seats",
            min_value=0,
            max_value=10,
            value=1,
            step=1,
            key="fleet_preview_monitor_seats",
        )
        rider_count_text = st.text_area(
            "Rider Groups",
            value="8, 22, 34, 44",
            help="Enter one candidate route size per line or separate values with commas. If a demand workbook is uploaded, its student counts are used instead.",
            key="fleet_preview_rider_counts",
        )

    assumptions = get_planning_assumptions(
        market_label,
        mode=mode_label,
        monitor_seats=int(monitor_seats),
    )
    with summary_col:
        st.subheader("Active Assumptions")
        metric_cols = st.columns(4)
        metric_cols[0].metric("Max Route Time", f"{assumptions.max_route_duration_minutes} min")
        metric_cols[1].metric("Max Stops", assumptions.max_stops_per_route)
        metric_cols[2].metric("Target Load", f"{assumptions.target_load_factor * 100.0:.0f}%")
        metric_cols[3].metric("Min Load", f"{assumptions.min_reasonable_load_factor * 100.0:.0f}%")
        st.caption(
            "Mode changes hidden scoring weights. Users see simple business choices, while the system handles vehicle-size tradeoffs."
        )

    demand_workbook = None
    if uploaded_demand_file is not None:
        try:
            demand_workbook = read_demand_workbook(uploaded_demand_file)
            st.subheader("Demand Workbook Preview")
            demand_cols = st.columns(4)
            demand_cols[0].metric("Demand Rows", demand_workbook.summary["row_count"])
            demand_cols[1].metric("Students", demand_workbook.summary["student_count"])
            demand_cols[2].metric("Unique Addresses", demand_workbook.summary["unique_address_count"])
            demand_cols[3].metric("City", str(demand_workbook.summary["city"]) or "N/A")
            if demand_workbook.warnings:
                for warning in demand_workbook.warnings:
                    st.warning(warning)
            st.dataframe(
                demand_riders_to_dataframe(demand_workbook.riders),
                use_container_width=True,
                hide_index=True,
            )
            if st.button("Validate & Geocode Demand", key="fleet_preview_geocode_demand"):
                with st.spinner("Validating addresses and reusing geocode cache where possible..."):
                    st.session_state["fleet_preview_geocode_result"] = geocode_demand_workbook(demand_workbook)
                    st.session_state.pop("fleet_preview_cluster_result", None)
                    st.session_state.pop("fleet_preview_route_result", None)
        except Exception as exc:
            st.error(f"Demand workbook parsing failed: {exc}")
            demand_workbook = None

    if demand_workbook is not None:
        rider_counts = [int(item["student_count"]) for item in demand_workbook.riders]
    else:
        try:
            rider_counts = _parse_rider_counts(rider_count_text)
        except ValueError as exc:
            st.error(str(exc))
            rider_counts = []

    geocode_result = st.session_state.get("fleet_preview_geocode_result")
    cluster_signature = (
        market_label,
        mode_label,
        int(monitor_seats),
        int(st.session_state.get("fleet_preview_cluster_sector_count", 8) or 8),
        st.session_state.get("fleet_preview_demand_signature"),
    )
    if st.session_state.get("fleet_preview_cluster_signature") != cluster_signature:
        st.session_state["fleet_preview_cluster_signature"] = cluster_signature
        st.session_state.pop("fleet_preview_cluster_result", None)
        st.session_state.pop("fleet_preview_route_result", None)
    if isinstance(geocode_result, dict) and demand_workbook is not None:
        st.subheader("Demand Geocode Preview")
        geocode_summary = dict(geocode_result.get("summary") or {})
        geocode_cols = st.columns(5)
        geocode_cols[0].metric("School", str(geocode_summary.get("school_status", "unknown")))
        geocode_cols[1].metric("Resolved Rows", geocode_summary.get("resolved_student_rows", 0))
        geocode_cols[2].metric("Failed Rows", geocode_summary.get("failed_student_rows", 0))
        geocode_cols[3].metric("Resolved Students", geocode_summary.get("resolved_students", 0))
        geocode_cols[4].metric("Cache Hits", geocode_summary.get("cache_hits", 0))
        st.caption(
            "Successful cached coordinates are reused. Blank, placeholder, or malformed addresses are marked as bad_address and are not sent to geocoding providers."
        )
        geocode_df = demand_geocode_results_to_dataframe(geocode_result)
        st.dataframe(geocode_df, use_container_width=True, hide_index=True)
        map_html = build_demand_geocode_map_html(geocode_result)
        if map_html:
            components.html(map_html, height=520, scrolling=False)

        cluster_col, sector_col = st.columns([1, 1])
        with cluster_col:
            build_clusters_clicked = st.button("Build Demand Clusters", key="fleet_preview_build_clusters")
        with sector_col:
            sector_count = st.selectbox(
                "Direction Sectors",
                options=[4, 8, 12],
                index=1,
                help="More sectors create narrower directional groups. This is a preview heuristic, not final routing.",
                key="fleet_preview_cluster_sector_count",
            )
        if build_clusters_clicked:
            try:
                st.session_state["fleet_preview_cluster_result"] = build_demand_clusters(
                    geocode_result,
                    market=market_label,
                    mode=mode_label,
                    monitor_seats=int(monitor_seats),
                    sector_count=int(sector_count),
                )
                st.session_state["fleet_preview_cluster_signature"] = cluster_signature
                st.session_state.pop("fleet_preview_route_result", None)
            except Exception as exc:
                st.error(f"Demand clustering failed: {exc}")

    cluster_result = st.session_state.get("fleet_preview_cluster_result")
    if isinstance(cluster_result, dict) and demand_workbook is not None:
        st.subheader("Demand Clustering Preview")
        cluster_summary = dict(cluster_result.get("summary") or {})
        cluster_cols = st.columns(5)
        cluster_cols[0].metric("Clusters", cluster_summary.get("cluster_count", 0))
        cluster_cols[1].metric("Resolved Points", cluster_summary.get("resolved_points", 0))
        cluster_cols[2].metric("Resolved Students", cluster_summary.get("resolved_students", 0))
        cluster_cols[3].metric("Failed Points", cluster_summary.get("failed_points", 0))
        cluster_cols[4].metric("Max Vehicle Capacity", cluster_summary.get("max_vehicle_student_capacity", 0))
        st.caption(
            "Preview clusters are grouped by school direction sector and split by vehicle capacity / stop count. They are candidate route groups, not final OSRM-optimized routes."
        )
        st.dataframe(demand_clusters_to_dataframe(cluster_result), use_container_width=True, hide_index=True)
        with st.expander("Cluster Stop Detail", expanded=False):
            st.dataframe(cluster_points_to_dataframe(cluster_result), use_container_width=True, hide_index=True)
        cluster_map_html = build_demand_cluster_map_html(cluster_result)
        if cluster_map_html:
            components.html(cluster_map_html, height=560, scrolling=False)

        route_direction = st.radio(
            "Route Preview Direction",
            options=["to_school", "from_school"],
            format_func=lambda value: "To School" if value == "to_school" else "From School",
            horizontal=True,
            key="fleet_preview_route_direction",
        )
        route_signature = (
            cluster_signature,
            route_direction,
            len(list(cluster_result.get("clusters") or [])),
        )
        if st.session_state.get("fleet_preview_route_signature") != route_signature:
            st.session_state["fleet_preview_route_signature"] = route_signature
            st.session_state.pop("fleet_preview_route_result", None)
        if st.button("Build OSRM + OR-Tools Route Preview", key="fleet_preview_route_preview"):
            try:
                with st.spinner("Building OSRM road matrix and solving stop order with OR-Tools..."):
                    route_preview = build_osrm_route_preview(
                        cluster_result,
                        service_direction=route_direction,
                        max_route_duration_minutes=assumptions.max_route_duration_minutes,
                    )
                    overlong_route_ids = {
                        str(row.get("cluster_id", "")).strip()
                        for row in list(route_preview.get("route_rows") or [])
                        if float(row.get("duration_min", 0.0) or 0.0) > float(assumptions.max_route_duration_minutes)
                    }
                    if overlong_route_ids:
                        refined_cluster_result = split_cluster_result_by_route_limit(
                            cluster_result,
                            overlong_route_ids,
                            market=market_label,
                            mode=mode_label,
                            monitor_seats=int(monitor_seats),
                        )
                        route_preview = build_osrm_route_preview(
                            refined_cluster_result,
                            service_direction=route_direction,
                            max_route_duration_minutes=assumptions.max_route_duration_minutes,
                        )
                        route_preview["refinement_note"] = (
                            "One or more clusters exceeded the route-duration target and were split once by distance from school."
                        )
                        st.session_state["fleet_preview_cluster_result"] = refined_cluster_result
                    st.session_state["fleet_preview_route_result"] = route_preview
                    st.session_state["fleet_preview_route_signature"] = route_signature
            except Exception as exc:
                st.error(f"Route preview failed: {exc}")

    route_result = st.session_state.get("fleet_preview_route_result")
    if isinstance(route_result, dict) and demand_workbook is not None:
        st.subheader("OSRM + OR-Tools Route Preview")
        route_summary = dict(route_result.get("summary") or {})
        route_cols = st.columns(4)
        route_cols[0].metric("Routes", route_summary.get("route_count", 0))
        route_cols[1].metric("Total Distance", f"{float(route_summary.get('total_distance_km', 0.0) or 0.0):.1f} km")
        route_cols[2].metric("Total Time", f"{float(route_summary.get('total_duration_min', 0.0) or 0.0):.1f} min")
        route_cols[3].metric("Direction", "To School" if route_summary.get("service_direction") == "to_school" else "From School")
        st.caption(
            "This preview uses OSRM road-network matrices and OR-Tools single-vehicle ordering inside each candidate cluster. It is still not the final multi-vehicle global optimizer."
        )
        if route_result.get("refinement_note"):
            st.info(str(route_result["refinement_note"]))
        st.dataframe(route_preview_to_dataframe(route_result), use_container_width=True, hide_index=True)
        st.download_button(
            "Download Generated Plan Workbook",
            data=build_generated_plan_workbook_bytes(route_result),
            file_name="fleet_planner_generated_plan.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        with st.expander("Route Stop Detail", expanded=False):
            st.dataframe(route_preview_stop_detail_to_dataframe(route_result), use_container_width=True, hide_index=True)
        route_map_html = build_route_preview_map_html(route_result)
        if route_map_html:
            components.html(route_map_html, height=560, scrolling=False)

    if isinstance(geocode_result, dict) and demand_workbook is not None:
        st.subheader("Global OR-Tools Plan")
        global_direction = st.radio(
            "Global Plan Direction",
            options=["to_school", "from_school"],
            format_func=lambda value: "To School" if value == "to_school" else "From School",
            horizontal=True,
            key="fleet_preview_global_direction",
        )
        global_signature = (
            market_label,
            mode_label,
            int(monitor_seats),
            global_direction,
            st.session_state.get("fleet_preview_demand_signature"),
        )
        if st.session_state.get("fleet_preview_global_signature") != global_signature:
            st.session_state["fleet_preview_global_signature"] = global_signature
            st.session_state.pop("fleet_preview_global_result", None)
        if st.button("Build Global OR-Tools Plan", key="fleet_preview_global_plan"):
            try:
                with st.spinner("Building OSRM full matrix and solving global vehicle plan with OR-Tools..."):
                    st.session_state["fleet_preview_global_result"] = build_global_ortools_plan(
                        geocode_result,
                        market=market_label,
                        mode=mode_label,
                        monitor_seats=int(monitor_seats),
                        service_direction=global_direction,
                    )
                    st.session_state["fleet_preview_global_signature"] = global_signature
                    st.session_state.pop("fleet_preview_submitted_job", None)
            except Exception as exc:
                st.error(f"Global OR-Tools plan failed: {exc}")

        global_result = st.session_state.get("fleet_preview_global_result")
        if isinstance(global_result, dict):
            global_summary = dict(global_result.get("summary") or {})
            global_cols = st.columns(5)
            global_cols[0].metric("Routes", global_summary.get("route_count", 0))
            global_cols[1].metric("Total Distance", f"{float(global_summary.get('total_distance_km', 0.0) or 0.0):.1f} km")
            global_cols[2].metric("Total Time", f"{float(global_summary.get('total_duration_min', 0.0) or 0.0):.1f} min")
            global_cols[3].metric("Candidates", global_summary.get("candidate_vehicle_count", 0))
            global_cols[4].metric("Solver", str(global_summary.get("solver", "global_ortools")))
            st.caption(
                "Global mode lets OR-Tools choose from a generated vehicle pool across all geocoded demand points. This is the main automatic-planning candidate."
            )
            st.dataframe(route_preview_to_dataframe(global_result), use_container_width=True, hide_index=True)
            st.download_button(
                "Download Global Plan Workbook",
                data=build_generated_plan_workbook_bytes(global_result),
                file_name="fleet_planner_global_plan.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            default_job_name = f"Demand Auto Plan - {datetime.now().strftime('%Y-%m-%d %H%M')}"
            job_name = st.text_input(
                "Generated Job Name",
                value=st.session_state.get("fleet_preview_generated_job_name", default_job_name),
                key="fleet_preview_generated_job_name",
            )
            if st.button("Submit Global Plan as Job", key="fleet_preview_submit_global_job"):
                try:
                    with st.spinner("Submitting generated plan to backend job queue..."):
                        submitted_job = _submit_generated_plan_as_job(
                            global_result,
                            job_name=str(job_name or default_job_name).strip() or default_job_name,
                            max_route_duration_minutes=assumptions.max_route_duration_minutes,
                        )
                    st.session_state["fleet_preview_submitted_job"] = submitted_job
                    st.success(f"Generated plan submitted as job `{submitted_job.get('job_id')}`.")
                except Exception as exc:
                    st.error(f"Generated plan submission failed: {friendly_error_message(exc)}")
            submitted_job = st.session_state.get("fleet_preview_submitted_job")
            if isinstance(submitted_job, dict) and submitted_job.get("job_id"):
                st.info(
                    f"Latest submitted generated job: `{submitted_job.get('job_id')}` "
                    f"({submitted_job.get('status', 'queued')}). Return to the main planner and refresh Job History to open it."
                )
            with st.expander("Global Plan Stop Detail", expanded=False):
                st.dataframe(route_preview_stop_detail_to_dataframe(global_result), use_container_width=True, hide_index=True)
            global_map_html = build_route_preview_map_html(global_result)
            if global_map_html:
                components.html(global_map_html, height=560, scrolling=False)

    if rider_counts:
        result_df = _build_selection_dataframe(rider_counts, market_label, mode_label, int(monitor_seats))
        mix_summary = estimate_vehicle_mix_for_groups(
            rider_counts,
            market=market_label,
            mode=mode_label,
            monitor_seats=int(monitor_seats),
        )

        st.subheader("Recommended Vehicles")
        st.dataframe(result_df, use_container_width=True, hide_index=True)

        mix_items = [
            {"Vehicle": vehicle_name, "Count": count}
            for vehicle_name, count in dict(mix_summary.get("vehicle_mix", {})).items()
        ]
        if mix_items:
            st.subheader("Estimated Mix")
            st.dataframe(pd.DataFrame(mix_items), use_container_width=True, hide_index=True)

        with st.expander("Decision Details", expanded=False):
            for rider_count in rider_counts:
                selection = select_vehicle_for_group(
                    rider_count,
                    market=market_label,
                    mode=mode_label,
                    monitor_seats=int(monitor_seats),
                )
                selected = selection.selected_vehicle or {}
                st.markdown(f"**{rider_count} riders -> {selected.get('display_name', 'No feasible vehicle')}**")
                if selection.feasible_options:
                    detail_rows = []
                    for option in selection.feasible_options[:5]:
                        detail_rows.append(
                            {
                                "Vehicle": option.get("display_name"),
                                "Capacity": option.get("student_capacity"),
                                "Load": _format_load_factor(option.get("load_factor")),
                                "Empty seats": option.get("empty_seats"),
                                "Score": round(float(option.get("selection_score", 0.0)), 2),
                            }
                        )
                    st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)
                if selection.rejected_options:
                    st.caption(
                        "Rejected: "
                        + "; ".join(
                            f"{item.get('display_name')}: {item.get('rejection_reason')}"
                            for item in selection.rejected_options[:4]
                        )
                    )

    st.subheader("Vehicle Catalog")
    st.dataframe(
        _build_catalog_dataframe(market_label, int(monitor_seats)),
        use_container_width=True,
        hide_index=True,
    )
