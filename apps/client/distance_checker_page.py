from __future__ import annotations

from datetime import datetime, timezone
import json
import tempfile
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from distance_tool import (
    build_distance_input_rows,
    build_distance_result_dataframe,
    build_download_excel_bytes,
    build_current_plan_route_cost_dataframe,
    build_current_plan_route_input_rows,
    geocode_records_for_distance_tool,
    get_excel_sheet_names,
    infer_current_plan_columns,
    read_excel_sheet,
)


DEFAULT_KOREA_DIESEL_KRW_PER_LITER = 2006.19
DEFAULT_CHINA_DIESEL_CNY_PER_LITER = 8.402
DEFAULT_BUS_FUEL_EFFICIENCY_KM_PER_LITER = 3.0
DISTANCE_CHECKER_JOBS_PATH = Path(__file__).resolve().parent / "cache" / "distance_checker_jobs.json"
MAX_DISTANCE_CHECKER_JOBS = 80


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dataframe_to_records(dataframe: pd.DataFrame | None) -> list[dict[str, object]]:
    if not isinstance(dataframe, pd.DataFrame):
        return []
    return json.loads(dataframe.to_json(orient="records", force_ascii=False))


def _records_to_dataframe(records: object) -> pd.DataFrame:
    return pd.DataFrame(records if isinstance(records, list) else [])


def load_distance_checker_jobs() -> list[dict[str, object]]:
    try:
        payload = json.loads(DISTANCE_CHECKER_JOBS_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def save_distance_checker_job(job: dict[str, object]) -> None:
    DISTANCE_CHECKER_JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    jobs = load_distance_checker_jobs()
    jobs.insert(0, job)
    DISTANCE_CHECKER_JOBS_PATH.write_text(
        json.dumps(jobs[:MAX_DISTANCE_CHECKER_JOBS], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def format_distance_checker_job_option(job: dict[str, object]) -> str:
    created_at = str(job.get("created_at", "")).replace("T", " ").replace("+00:00", " UTC")
    job_type = str(job.get("type", "")).replace("_", " ").title() or "Distance Job"
    label = str(job.get("label", "")).strip() or str(job.get("job_id", "")).strip()
    return f"{created_at} | {job_type} | {label}"


def get_request_host() -> str:
    try:
        from streamlit.web.server.websocket_headers import _get_websocket_headers

        headers = _get_websocket_headers() or {}
        return str(headers.get("Host", "") or headers.get("host", "") or "").strip().lower()
    except Exception:
        return ""


def get_route_cost_market_profile() -> dict[str, object]:
    host = get_request_host()
    if host.startswith("brp-kr.") or "brp-kr." in host:
        return {
            "market": "South Korea",
            "default_city": "Seoul",
            "default_country": "South Korea",
            "currency_code": "KRW",
            "currency_label": "KRW",
            "diesel_price": DEFAULT_KOREA_DIESEL_KRW_PER_LITER,
            "diesel_price_source": "GlobalPetrolPrices South Korea diesel prices",
            "diesel_price_source_url": "https://www.globalpetrolprices.com/South-Korea/diesel_prices/",
            "diesel_price_date": "18-May-2026",
        }
    if host.startswith("brp.") or "brp.example.com" in host:
        return {
            "market": "China",
            "default_city": "Shanghai",
            "default_country": "China",
            "currency_code": "CNY",
            "currency_label": "RMB",
            "diesel_price": DEFAULT_CHINA_DIESEL_CNY_PER_LITER,
            "diesel_price_source": "GlobalPetrolPrices China fuel prices",
            "diesel_price_source_url": "https://www.globalpetrolprices.com/China/",
            "diesel_price_date": "18-May-2026",
        }
    return {
        "market": "South Korea",
        "default_city": "Seoul",
        "default_country": "South Korea",
        "currency_code": "KRW",
        "currency_label": "KRW",
        "diesel_price": DEFAULT_KOREA_DIESEL_KRW_PER_LITER,
        "diesel_price_source": "GlobalPetrolPrices South Korea diesel prices",
        "diesel_price_source_url": "https://www.globalpetrolprices.com/South-Korea/diesel_prices/",
        "diesel_price_date": "18-May-2026",
    }


def read_google_geocode_usage_display() -> str:
    usage_path = Path(__file__).resolve().parent / "cache" / "google_geocode_usage.json"
    try:
        payload = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.exists() else {}
    except Exception:
        payload = {}
    month_key = "unknown"
    try:
        from client_runtime import current_google_usage_month_key, read_google_geocode_monthly_usage

        month_key = current_google_usage_month_key()
        usage_value = read_google_geocode_monthly_usage(month_key)
    except Exception:
        usage_value = int(payload.get(month_key, 0) or 0) if isinstance(payload, dict) else 0
    return f"Google geocode usage this month: {usage_value:,} / 10,000"


def render_distance_checker_page() -> None:
    if "distance_checker_results_df" not in st.session_state:
        st.session_state["distance_checker_results_df"] = None
    if "distance_checker_error" not in st.session_state:
        st.session_state["distance_checker_error"] = ""
    if "route_cost_results_df" not in st.session_state:
        st.session_state["route_cost_results_df"] = None
    if "route_cost_legs_df" not in st.session_state:
        st.session_state["route_cost_legs_df"] = None
    if "route_cost_error" not in st.session_state:
        st.session_state["route_cost_error"] = ""
    market_profile = get_route_cost_market_profile()
    currency_code = str(market_profile["currency_code"])
    currency_label = str(market_profile["currency_label"])

    st.title("Distance & Cost")
    st.caption("Measure distance from one reference stop to each address in an Excel file.")
    st.caption(read_google_geocode_usage_display())

    if st.button("Back to Main Planner", key="back_to_main_planner"):
        st.query_params.clear()
        st.rerun()

    cached_jobs = load_distance_checker_jobs()
    with st.expander("Distance & Cost Job Cache", expanded=False):
        if not cached_jobs:
            st.caption("No cached Distance & Cost jobs yet.")
        else:
            cached_job_ids = [str(item.get("job_id", "")).strip() for item in cached_jobs if str(item.get("job_id", "")).strip()]
            selected_cached_job_id = st.selectbox(
                "Cached Jobs",
                options=cached_job_ids,
                format_func=lambda job_id: format_distance_checker_job_option(
                    next(item for item in cached_jobs if str(item.get("job_id", "")).strip() == job_id)
                ),
                key="distance_checker_cached_job_id",
            )
            selected_cached_job = next(
                (item for item in cached_jobs if str(item.get("job_id", "")).strip() == selected_cached_job_id),
                {},
            )
            if st.button("Load Cached Distance Job", key="load_cached_distance_job"):
                if str(selected_cached_job.get("type", "")).strip() == "route_cost":
                    st.session_state["route_cost_results_df"] = _records_to_dataframe(selected_cached_job.get("route_results"))
                    st.session_state["route_cost_legs_df"] = _records_to_dataframe(selected_cached_job.get("leg_results"))
                    st.session_state["route_cost_error"] = ""
                else:
                    st.session_state["distance_checker_results_df"] = _records_to_dataframe(selected_cached_job.get("results"))
                    st.session_state["distance_checker_error"] = ""
                st.success("Cached distance job loaded.")

    tool_tab_1, tool_tab_2 = st.tabs(["Reference Distance Check", "Current Plan Route Cost"])

    with tool_tab_1:
        origin_col, mode_col = st.columns([2.2, 1])
        with origin_col:
            st.subheader("Reference Stop")
            origin_country = st.text_input("Country", value="South Korea")
            origin_city = st.text_input("City", value="Seoul")
            origin_address = st.text_input("Address")
        with mode_col:
            st.subheader("Distance Mode")
            distance_mode = st.radio(
                "Calculation method",
                options=[("road", "Road distance"), ("straight_line", "Straight-line distance")],
                format_func=lambda item: item[1],
                label_visibility="collapsed",
            )[0]
            st.caption(
                "Road distance uses OSRM and returns both distance and estimated travel time. "
                "Straight-line distance is faster and uses geocoded coordinates only."
            )

        st.divider()
        st.subheader("Address File")
        uploaded_file = st.file_uploader("Upload Excel address file", type=["xlsx", "xlsm"], key="distance_checker_upload")

        source_excel_path: str | None = None
        sheet_names: list[str] = []
        source_df: pd.DataFrame | None = None
        if uploaded_file is not None:
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded_file.name).suffix) as temp_file:
                temp_file.write(uploaded_file.getvalue())
                source_excel_path = temp_file.name
            sheet_names = get_excel_sheet_names(source_excel_path)

        if source_excel_path:
            selected_sheet = st.selectbox("Sheet", options=sheet_names, index=0, key="distance_checker_sheet")
            source_df = read_excel_sheet(source_excel_path, sheet_name=selected_sheet)
            source_columns = list(source_df.columns)
            address_col, city_col, country_col = st.columns(3)
            with address_col:
                address_column = st.selectbox("Address Column", options=source_columns, key="distance_checker_address_column")
            optional_city_options = ["(Use reference city)", *source_columns]
            optional_country_options = ["(Use reference country)", *source_columns]
            with city_col:
                city_column_label = st.selectbox("City Column", options=optional_city_options, index=0, key="distance_checker_city_column")
            with country_col:
                country_column_label = st.selectbox("Country Column", options=optional_country_options, index=0, key="distance_checker_country_column")

            city_column = None if city_column_label == "(Use reference city)" else city_column_label
            country_column = None if country_column_label == "(Use reference country)" else country_column_label

            st.caption(
                f"Rows in sheet: {len(source_df)} | Address column: `{address_column}` | "
                f"City source: `{city_column or origin_city or 'blank'}` | "
                f"Country source: `{country_column or origin_country or 'blank'}`"
            )
            st.dataframe(source_df.head(20), width="stretch")

            run_checker = st.button("Run Distance Check", type="primary")
        else:
            run_checker = False

        if run_checker:
            st.session_state["distance_checker_results_df"] = None
            st.session_state["distance_checker_error"] = ""
            if not origin_address.strip():
                st.session_state["distance_checker_error"] = "Please enter the reference stop address."
            elif source_df is None:
                st.session_state["distance_checker_error"] = "Please upload an Excel file first."
            else:
                try:
                    with st.spinner("Running distance check..."):
                        origin_rows, _ = geocode_records_for_distance_tool(
                            [
                                {
                                    "source_excel_row": 1,
                                    "country": origin_country.strip(),
                                    "city": origin_city.strip(),
                                    "address": origin_address.strip(),
                                }
                            ]
                        )
                        origin_row = origin_rows[0]
                        if origin_row.get("status") != "ok":
                            raise RuntimeError(origin_row.get("warning") or "Reference stop could not be geocoded.")

                        input_rows = build_distance_input_rows(
                            source_df,
                            address_column=address_column,
                            city_column=city_column,
                            country_column=country_column,
                            default_city=origin_city.strip(),
                            default_country=origin_country.strip(),
                        )
                        geocoded_rows, _ = geocode_records_for_distance_tool(input_rows)
                        results_df = build_distance_result_dataframe(
                            source_df,
                            input_rows,
                            geocoded_rows,
                            origin_record=origin_row,
                            origin_point=dict(origin_row["point"]),
                            distance_mode=distance_mode,
                        )
                    st.session_state["distance_checker_results_df"] = results_df
                    save_distance_checker_job(
                        {
                            "job_id": uuid4().hex[:12],
                            "type": "reference_distance",
                            "created_at": _utc_now_iso(),
                            "label": f"{Path(uploaded_file.name).stem if uploaded_file is not None else 'uploaded addresses'} from {origin_address.strip()}",
                            "metadata": {
                                "source_label": uploaded_file.name if uploaded_file is not None else "",
                                "selected_sheet": selected_sheet,
                                "origin_country": origin_country.strip(),
                                "origin_city": origin_city.strip(),
                                "origin_address": origin_address.strip(),
                                "distance_mode": distance_mode,
                                "address_column": address_column,
                                "city_column": city_column or "",
                                "country_column": country_column or "",
                            },
                            "results": _dataframe_to_records(results_df),
                        }
                    )
                except Exception as exc:
                    st.session_state["distance_checker_error"] = str(exc)

        if st.session_state["distance_checker_error"]:
            st.error(st.session_state["distance_checker_error"])

        results_df = st.session_state.get("distance_checker_results_df")
        if isinstance(results_df, pd.DataFrame):
            summary_col_1, summary_col_2, summary_col_3 = st.columns(3)
            ok_count = int((results_df["status"] == "ok").sum()) if "status" in results_df.columns else 0
            failed_count = int((results_df["status"] == "geocode_failed").sum()) if "status" in results_df.columns else 0
            summary_col_1.metric("Rows", len(results_df))
            summary_col_2.metric("Resolved", ok_count)
            summary_col_3.metric("Failed", failed_count)

            st.dataframe(results_df, width="stretch")

            download_col_1, download_col_2 = st.columns(2)
            download_col_1.download_button(
                "Download CSV",
                data=results_df.to_csv(index=False).encode("utf-8-sig"),
                file_name="distance_checker_results.csv",
                mime="text/csv",
                width="stretch",
            )
            download_col_2.download_button(
                "Download Excel",
                data=build_download_excel_bytes(results_df),
                file_name="distance_checker_results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )

    with tool_tab_2:
        st.subheader("Current Plan Route Cost")
        st.caption(
            "Upload a current-plan workbook. The tool follows the selected stop-order column within each route, sums road distance between consecutive stops, "
            "then estimates one-way diesel cost."
        )
        fuel_col_1, fuel_col_2, fuel_col_3 = st.columns(3)
        with fuel_col_1:
            default_city = st.text_input("Default City", value=str(market_profile["default_city"]), key="route_cost_default_city")
        with fuel_col_2:
            default_country = st.text_input("Default Country", value=str(market_profile["default_country"]), key="route_cost_default_country")
        with fuel_col_3:
            diesel_price = st.number_input(
                f"Diesel Price ({currency_code}/L)",
                min_value=0.0,
                value=float(market_profile["diesel_price"]),
                step=10.0 if currency_code == "KRW" else 0.1,
                help=f"Default uses {market_profile['diesel_price_source']} for {market_profile['diesel_price_date']}.",
            )
        efficiency_col, note_col = st.columns([1, 2])
        with efficiency_col:
            fuel_efficiency = st.number_input(
                "Fuel Efficiency (km/L)",
                min_value=0.1,
                value=float(DEFAULT_BUS_FUEL_EFFICIENCY_KM_PER_LITER),
                step=0.1,
                help="Adjust this for your fleet. Large diesel buses often vary widely by size, traffic, and idling.",
            )
        with note_col:
            st.caption(
                f"Detected market: {market_profile['market']} | Default diesel price: "
                f"{currency_code} {float(market_profile['diesel_price']):,.3f}/L, latest update {market_profile['diesel_price_date']} from "
                f"{market_profile['diesel_price_source_url']}. "
                "Cost = route distance / km-per-liter * diesel price. This excludes driver, toll, maintenance, depot deadhead, and idling."
            )

        route_file = st.file_uploader("Upload current-plan workbook", type=["xlsx", "xlsm"], key="route_cost_upload")
        route_excel_path: str | None = None
        route_df: pd.DataFrame | None = None
        if route_file is not None:
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(route_file.name).suffix) as temp_file:
                temp_file.write(route_file.getvalue())
                route_excel_path = temp_file.name

        if route_excel_path:
            route_sheet_names = get_excel_sheet_names(route_excel_path)
            default_sheet_index = route_sheet_names.index("current_plan_assignments") if "current_plan_assignments" in route_sheet_names else 0
            route_sheet = st.selectbox("Current Plan Sheet", options=route_sheet_names, index=default_sheet_index, key="route_cost_sheet")
            route_df = read_excel_sheet(route_excel_path, sheet_name=route_sheet)
            route_columns = list(route_df.columns)
            inferred = infer_current_plan_columns(route_df)
            route_col_1, route_col_2, route_col_3 = st.columns(3)
            with route_col_1:
                route_column = st.selectbox(
                    "Route Column",
                    options=route_columns,
                    index=route_columns.index(inferred["route"]) if inferred.get("route") in route_columns else 0,
                    key="route_cost_route_column",
                )
            with route_col_2:
                address_column = st.selectbox(
                    "Address Column",
                    options=route_columns,
                    index=route_columns.index(inferred["address"]) if inferred.get("address") in route_columns else 0,
                    key="route_cost_address_column",
                )
            optional_sequence_options = ["(Use row order)", *route_columns]
            optional_bus_type_options = ["(No bus type column)", *route_columns]
            optional_city_options = ["(Use default city)", *route_columns]
            optional_country_options = ["(Use default country)", *route_columns]
            with route_col_3:
                inferred_sequence = inferred.get("sequence")
                sequence_index = optional_sequence_options.index(inferred_sequence) if inferred_sequence in optional_sequence_options else 0
                sequence_column_label = st.selectbox("Stop Order Column", options=optional_sequence_options, index=sequence_index, key="route_cost_sequence_column")

            route_col_4, route_col_5, route_col_6 = st.columns(3)
            with route_col_4:
                inferred_bus_type = inferred.get("bus_type")
                bus_type_index = optional_bus_type_options.index(inferred_bus_type) if inferred_bus_type in optional_bus_type_options else 0
                bus_type_column_label = st.selectbox("Bus Type Column", options=optional_bus_type_options, index=bus_type_index, key="route_cost_bus_type_column")
            with route_col_5:
                inferred_city = inferred.get("city")
                city_index = optional_city_options.index(inferred_city) if inferred_city in optional_city_options else 0
                city_column_label = st.selectbox("City Column", options=optional_city_options, index=city_index, key="route_cost_city_column")
            with route_col_6:
                inferred_country = inferred.get("country")
                country_index = optional_country_options.index(inferred_country) if inferred_country in optional_country_options else 0
                country_column_label = st.selectbox("Country Column", options=optional_country_options, index=country_index, key="route_cost_country_column")

            sequence_column = None if sequence_column_label == "(Use row order)" else sequence_column_label
            bus_type_column = None if bus_type_column_label == "(No bus type column)" else bus_type_column_label
            city_column = None if city_column_label == "(Use default city)" else city_column_label
            country_column = None if country_column_label == "(Use default country)" else country_column_label

            st.caption(
                f"Rows in sheet: {len(route_df)}. Stop sequence source: `{sequence_column or 'row order'}`. "
                "E-bus/electric/new-energy bus types keep distance results but skip diesel cost. "
                "Unresolved stops are skipped from the distance sum, so any route with failed stops should be reviewed."
            )
            st.dataframe(route_df.head(20), width="stretch")
            run_route_cost = st.button("Calculate Route Distance & Diesel Cost", type="primary", key="run_route_cost")
        else:
            run_route_cost = False

        if run_route_cost:
            st.session_state["route_cost_results_df"] = None
            st.session_state["route_cost_legs_df"] = None
            st.session_state["route_cost_error"] = ""
            if route_df is None:
                st.session_state["route_cost_error"] = "Please upload a current-plan workbook first."
            else:
                try:
                    with st.spinner("Calculating route distances and diesel costs..."):
                        input_rows = build_current_plan_route_input_rows(
                            route_df,
                            route_column=route_column,
                            address_column=address_column,
                            sequence_column=sequence_column,
                            bus_type_column=bus_type_column,
                            city_column=city_column,
                            country_column=country_column,
                            default_city=default_city.strip(),
                            default_country=default_country.strip(),
                        )
                        geocoded_rows, _ = geocode_records_for_distance_tool(input_rows)
                        route_results_df, leg_results_df = build_current_plan_route_cost_dataframe(
                            input_rows,
                            geocoded_rows,
                            diesel_price_per_liter=float(diesel_price),
                            fuel_efficiency_km_per_liter=float(fuel_efficiency),
                        )
                    st.session_state["route_cost_results_df"] = route_results_df
                    st.session_state["route_cost_legs_df"] = leg_results_df
                    save_distance_checker_job(
                        {
                            "job_id": uuid4().hex[:12],
                            "type": "route_cost",
                            "created_at": _utc_now_iso(),
                            "label": f"{Path(route_file.name).stem if route_file is not None else 'current plan'} route cost",
                            "metadata": {
                                "source_label": route_file.name if route_file is not None else "",
                                "selected_sheet": route_sheet,
                                "default_city": default_city.strip(),
                                "default_country": default_country.strip(),
                                "market": str(market_profile["market"]),
                                "currency_code": currency_code,
                                "diesel_price_per_liter": float(diesel_price),
                                "fuel_efficiency_km_per_liter": float(fuel_efficiency),
                                "route_column": route_column,
                                "address_column": address_column,
                                "sequence_column": sequence_column or "",
                                "bus_type_column": bus_type_column or "",
                                "city_column": city_column or "",
                                "country_column": country_column or "",
                            },
                            "route_results": _dataframe_to_records(route_results_df),
                            "leg_results": _dataframe_to_records(leg_results_df),
                        }
                    )
                except Exception as exc:
                    st.session_state["route_cost_error"] = str(exc)

        if st.session_state["route_cost_error"]:
            st.error(st.session_state["route_cost_error"])

        route_results = st.session_state.get("route_cost_results_df")
        leg_results = st.session_state.get("route_cost_legs_df")
        if isinstance(route_results, pd.DataFrame) and not route_results.empty:
            cost_column = "estimated_one_way_fuel_cost"
            if cost_column not in route_results.columns and "estimated_one_way_cost_krw" in route_results.columns:
                cost_column = "estimated_one_way_cost_krw"
            total_distance = float(route_results["route_distance_km"].sum())
            total_cost = float(route_results[cost_column].sum())
            failed_routes = int((route_results["failed_stops"] > 0).sum())
            skipped_electric_routes = int((route_results["diesel_cost_status"] == "skipped_electric_bus").sum()) if "diesel_cost_status" in route_results.columns else 0
            cost_1, cost_2, cost_3, cost_4 = st.columns(4)
            cost_1.metric("Total One-Way Distance", f"{total_distance:,.1f} km")
            cost_2.metric("Estimated One-Way Diesel Cost", f"{currency_label} {total_cost:,.0f}" if currency_code == "KRW" else f"{currency_label} {total_cost:,.2f}")
            cost_3.metric("Routes With Unresolved Stops", failed_routes)
            cost_4.metric("Electric Routes Skipped", skipped_electric_routes)
            st.dataframe(route_results, width="stretch", hide_index=True)
            if isinstance(leg_results, pd.DataFrame) and not leg_results.empty:
                with st.expander("Leg-by-leg details", expanded=False):
                    st.dataframe(leg_results, width="stretch", hide_index=True)

            route_download_col_1, route_download_col_2 = st.columns(2)
            route_download_col_1.download_button(
                "Download Route Cost CSV",
                data=route_results.to_csv(index=False).encode("utf-8-sig"),
                file_name="current_plan_route_costs.csv",
                mime="text/csv",
                width="stretch",
            )
            route_download_col_2.download_button(
                "Download Route Cost Excel",
                data=build_download_excel_bytes(route_results),
                file_name="current_plan_route_costs.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )
