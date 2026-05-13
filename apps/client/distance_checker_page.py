from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from distance_tool import (
    build_distance_input_rows,
    build_distance_result_dataframe,
    build_download_excel_bytes,
    geocode_records_for_distance_tool,
    get_excel_sheet_names,
    read_excel_sheet,
)


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

    st.title("Distance Checker")
    st.caption("Measure distance from one reference stop to each address in an Excel file.")
    st.caption(read_google_geocode_usage_display())

    if st.button("Back to Main Planner", key="back_to_main_planner"):
        st.query_params.clear()
        st.rerun()

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
    uploaded_file = st.file_uploader("Upload Excel address file", type=["xlsx", "xlsm"])

    source_excel_path: str | None = None
    sheet_names: list[str] = []
    source_df: pd.DataFrame | None = None
    if uploaded_file is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded_file.name).suffix) as temp_file:
            temp_file.write(uploaded_file.getvalue())
            source_excel_path = temp_file.name
        sheet_names = get_excel_sheet_names(source_excel_path)

    if source_excel_path:
        selected_sheet = st.selectbox("Sheet", options=sheet_names, index=0)
        source_df = read_excel_sheet(source_excel_path, sheet_name=selected_sheet)
        source_columns = list(source_df.columns)
        address_col, city_col, country_col = st.columns(3)
        with address_col:
            address_column = st.selectbox("Address Column", options=source_columns)
        optional_city_options = ["(Use reference city)", *source_columns]
        optional_country_options = ["(Use reference country)", *source_columns]
        with city_col:
            city_column_label = st.selectbox("City Column", options=optional_city_options, index=0)
        with country_col:
            country_column_label = st.selectbox("Country Column", options=optional_country_options, index=0)

        city_column = None if city_column_label == "(Use reference city)" else city_column_label
        country_column = None if country_column_label == "(Use reference country)" else country_column_label

        st.caption(
            f"Rows in sheet: {len(source_df)} | Address column: `{address_column}` | "
            f"City source: `{city_column or origin_city or 'blank'}` | "
            f"Country source: `{country_column or origin_country or 'blank'}`"
        )
        st.dataframe(source_df.head(20), width="stretch")

        run_checker = st.button("Run Distance Checker", type="primary")
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
