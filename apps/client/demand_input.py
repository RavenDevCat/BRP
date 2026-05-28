from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
from typing import Any

import folium
import pandas as pd

import client_runtime as runtime


DEMAND_SHEET_NAME = "demand"
DEMAND_NOTES_SHEET_NAME = "template_notes"
REQUIRED_DEMAND_COLUMNS: tuple[str, ...] = (
    "country",
    "city",
    "school_name",
    "school_address",
    "student_address",
    "student_count",
)
OPTIONAL_DEMAND_COLUMNS: tuple[str, ...] = ("notes",)


@dataclass(frozen=True)
class DemandWorkbook:
    school: dict[str, object]
    riders: list[dict[str, object]]
    summary: dict[str, object]
    warnings: list[str]


def build_demand_template_workbook_bytes() -> bytes:
    demand_rows = [
        {
            "country": "South Korea",
            "city": "Seoul",
            "school_name": "Example International School",
            "school_address": "Seoul, Gangnam-gu, Teheran-ro 123",
            "student_address": "Seoul, Seocho-gu, Banpo-daero 45",
            "student_count": 2,
            "notes": "Example pickup group",
        },
        {
            "country": "South Korea",
            "city": "Seoul",
            "school_name": "Example International School",
            "school_address": "Seoul, Gangnam-gu, Teheran-ro 123",
            "student_address": "Seoul, Yongsan-gu, Hannam-daero 72",
            "student_count": 1,
            "notes": "",
        },
    ]
    notes_rows = [
        {"field": "country", "rule": "Required. Use one country per workbook in v1."},
        {"field": "city", "rule": "Required. Use the operating city for geocoding and assumptions."},
        {"field": "school_name", "rule": "Optional but recommended."},
        {"field": "school_address", "rule": "Required. Use the same school address on every row."},
        {"field": "student_address", "rule": "Required. One pickup location per row."},
        {"field": "student_count", "rule": "Required. Positive whole number of students at this address."},
        {"field": "notes", "rule": "Optional free text. Not used by the first-pass planner."},
    ]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(demand_rows).to_excel(writer, sheet_name=DEMAND_SHEET_NAME, index=False)
        pd.DataFrame(notes_rows).to_excel(writer, sheet_name=DEMAND_NOTES_SHEET_NAME, index=False)
    return output.getvalue()


def _normalize_column_name(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _read_demand_sheet(excel_source: str | Path | io.BytesIO) -> pd.DataFrame:
    sheet_names = pd.ExcelFile(excel_source).sheet_names
    target_sheet = DEMAND_SHEET_NAME if DEMAND_SHEET_NAME in sheet_names else sheet_names[0]
    dataframe = pd.read_excel(excel_source, sheet_name=target_sheet)
    dataframe = dataframe.rename(columns={column: _normalize_column_name(column) for column in dataframe.columns})
    return dataframe


def _clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value or "").strip()


def _parse_student_count(value: object, row_number: int) -> int:
    if pd.isna(value) or str(value).strip() == "":
        raise ValueError(f"`student_count` is blank on row {row_number}.")
    try:
        parsed = int(float(str(value).strip()))
    except ValueError as exc:
        raise ValueError(f"`student_count` must be a positive whole number on row {row_number}: {value!r}.") from exc
    if parsed <= 0:
        raise ValueError(f"`student_count` must be greater than zero on row {row_number}.")
    return parsed


def read_demand_workbook(excel_source: str | Path | io.BytesIO) -> DemandWorkbook:
    dataframe = _read_demand_sheet(excel_source)
    missing_columns = [column for column in REQUIRED_DEMAND_COLUMNS if column not in dataframe.columns]
    if missing_columns:
        raise ValueError(f"Demand workbook is missing required columns: {', '.join(missing_columns)}.")

    riders: list[dict[str, object]] = []
    warnings: list[str] = []
    school_keys: set[tuple[str, str, str, str]] = set()

    for row_index, row in dataframe.iterrows():
        row_number = int(row_index) + 2
        country = _clean_text(row.get("country"))
        city = _clean_text(row.get("city"))
        school_name = _clean_text(row.get("school_name"))
        school_address = _clean_text(row.get("school_address"))
        student_address = _clean_text(row.get("student_address"))
        notes = _clean_text(row.get("notes"))

        if not any((country, city, school_name, school_address, student_address, notes, _clean_text(row.get("student_count")))):
            continue
        if not country:
            raise ValueError(f"`country` is blank on row {row_number}.")
        if not city:
            raise ValueError(f"`city` is blank on row {row_number}.")
        if not school_address:
            raise ValueError(f"`school_address` is blank on row {row_number}.")
        if not student_address:
            raise ValueError(f"`student_address` is blank on row {row_number}.")

        student_count = _parse_student_count(row.get("student_count"), row_number)
        school_keys.add((country.lower(), city.lower(), school_name.lower(), school_address.lower()))
        riders.append(
            {
                "source_excel_row": row_number,
                "country": country,
                "city": city,
                "school_name": school_name,
                "school_address": school_address,
                "student_address": student_address,
                "student_count": student_count,
                "notes": notes,
            }
        )

    if not riders:
        raise ValueError("Demand workbook does not contain any rider rows.")
    if len(school_keys) > 1:
        warnings.append(
            "Multiple school/city/country combinations were found. Demand Template v1 is designed for one school per workbook."
        )

    first_rider = riders[0]
    school = {
        "country": first_rider["country"],
        "city": first_rider["city"],
        "school_name": first_rider["school_name"],
        "school_address": first_rider["school_address"],
    }
    total_students = sum(int(item["student_count"]) for item in riders)
    unique_addresses = sorted({str(item["student_address"]).strip().lower() for item in riders})
    summary = {
        "row_count": len(riders),
        "student_count": total_students,
        "unique_address_count": len(unique_addresses),
        "country": school["country"],
        "city": school["city"],
        "school_name": school["school_name"],
        "school_address": school["school_address"],
    }
    return DemandWorkbook(school=school, riders=riders, summary=summary, warnings=warnings)


def demand_riders_to_dataframe(riders: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Country": item.get("country"),
                "City": item.get("city"),
                "School": item.get("school_name"),
                "School Address": item.get("school_address"),
                "Student Address": item.get("student_address"),
                "Students": item.get("student_count"),
                "Notes": item.get("notes"),
            }
            for item in riders
        ]
    )


def _is_obviously_bad_address(address: str) -> bool:
    text = str(address or "").strip()
    lowered = text.lower()
    if not text:
        return True
    if lowered in {"n/a", "na", "none", "null", "unknown", "tbd", "todo", "-", "--"}:
        return True
    if len(text) < 4:
        return True
    return not any(character.isalnum() for character in text)


def _find_cached_geocode_status(country: str, city: str, address: str) -> tuple[dict[str, Any] | None, bool]:
    provider_name = runtime.expected_geocode_provider(country, address)
    for lookup_key in runtime.geocode_cache_lookup_keys(country, city, address):
        candidate = runtime.GEOCODE_CACHE.get(lookup_key)
        if not isinstance(candidate, dict):
            continue
        if runtime.is_failed_geocode_cache_entry(candidate):
            attempted_provider = str(candidate.get("attempted_provider", "")).strip().lower()
            if attempted_provider == provider_name:
                return dict(candidate), True
            continue
        if str(candidate.get("provider", "")).strip().lower() != provider_name:
            continue
        try:
            lat = float(candidate.get("lat", 0.0) or 0.0)
            lng = float(candidate.get("lng", 0.0) or 0.0)
        except Exception:
            continue
        formatted_address = str(candidate.get("formatted_address", "") or address).strip()
        if runtime.is_plausible_korea_geocode_result(country, city, lat, lng, formatted_address):
            return dict(candidate), False
    return None, False


def _geocode_one(
    *,
    country: str,
    city: str,
    address: str,
    source_excel_row: int,
    role: str,
) -> tuple[dict[str, Any], bool]:
    if _is_obviously_bad_address(address):
        return (
            {
                "role": role,
                "source_excel_row": source_excel_row,
                "country": country,
                "city": city,
                "address": address,
                "status": "bad_address",
                "cache_hit": False,
                "provider": "",
                "formatted_address": "",
                "lat": None,
                "lng": None,
                "warning": "Address is blank, too short, or a placeholder.",
            },
            False,
        )

    cached_entry, cached_is_failure = _find_cached_geocode_status(country, city, address)
    point, warning, cache_changed = runtime.resolve_geocoded_point(country, city, address, [source_excel_row])
    cache_hit = cached_entry is not None
    if point is None:
        return (
            {
                "role": role,
                "source_excel_row": source_excel_row,
                "country": country,
                "city": city,
                "address": address,
                "status": "cached_geocode_failed" if cached_is_failure else "geocode_failed",
                "cache_hit": cache_hit,
                "provider": str((cached_entry or {}).get("attempted_provider", "")).strip(),
                "formatted_address": "",
                "lat": None,
                "lng": None,
                "warning": str((warning or {}).get("warning", "Could not geocode this address.")),
            },
            cache_changed,
        )

    return (
        {
            "role": role,
            "source_excel_row": source_excel_row,
            "country": country,
            "city": city,
            "address": address,
            "status": "ok",
            "cache_hit": cache_hit,
            "provider": str(point.get("provider", "")).strip(),
            "formatted_address": str(point.get("formatted_address", "")).strip(),
            "lat": float(point.get("lat", 0.0) or 0.0),
            "lng": float(point.get("lng", 0.0) or 0.0),
            "warning": "",
        },
        cache_changed,
    )


def geocode_demand_workbook(demand_workbook: DemandWorkbook) -> dict[str, Any]:
    school = dict(demand_workbook.school)
    cache_changed = False
    school_row, changed = _geocode_one(
        country=str(school.get("country", "")).strip(),
        city=str(school.get("city", "")).strip(),
        address=str(school.get("school_address", "")).strip(),
        source_excel_row=2,
        role="school",
    )
    cache_changed = cache_changed or changed

    demand_rows: list[dict[str, Any]] = []
    for rider in demand_workbook.riders:
        row, changed = _geocode_one(
            country=str(rider.get("country", "")).strip(),
            city=str(rider.get("city", "")).strip(),
            address=str(rider.get("student_address", "")).strip(),
            source_excel_row=int(rider.get("source_excel_row", 0) or 0),
            role="student",
        )
        row["student_count"] = int(rider.get("student_count", 0) or 0)
        row["notes"] = str(rider.get("notes", "")).strip()
        demand_rows.append(row)
        cache_changed = cache_changed or changed

    if cache_changed:
        runtime.save_json_cache(runtime.GEOCODE_CACHE_PATH, runtime.GEOCODE_CACHE)

    ok_rows = [row for row in demand_rows if row.get("status") == "ok"]
    failed_rows = [row for row in demand_rows if row.get("status") != "ok"]
    summary = {
        "school_status": school_row.get("status"),
        "student_rows": len(demand_rows),
        "resolved_student_rows": len(ok_rows),
        "failed_student_rows": len(failed_rows),
        "resolved_students": sum(int(row.get("student_count", 0) or 0) for row in ok_rows),
        "failed_students": sum(int(row.get("student_count", 0) or 0) for row in failed_rows),
        "cache_hits": sum(1 for row in [school_row, *demand_rows] if bool(row.get("cache_hit"))),
        "cache_changed": cache_changed,
    }
    return {
        "school": school_row,
        "demand_points": demand_rows,
        "summary": summary,
    }


def demand_geocode_results_to_dataframe(geocode_result: dict[str, Any]) -> pd.DataFrame:
    rows = []
    school = dict(geocode_result.get("school") or {})
    rows.append(
        {
            "Role": "school",
            "Row": school.get("source_excel_row"),
            "Address": school.get("address"),
            "Students": "",
            "Status": school.get("status"),
            "Cache Hit": school.get("cache_hit"),
            "Provider": school.get("provider"),
            "Formatted Address": school.get("formatted_address"),
            "Lat": school.get("lat"),
            "Lng": school.get("lng"),
            "Warning": school.get("warning"),
        }
    )
    for point in list(geocode_result.get("demand_points") or []):
        rows.append(
            {
                "Role": "student",
                "Row": point.get("source_excel_row"),
                "Address": point.get("address"),
                "Students": point.get("student_count"),
                "Status": point.get("status"),
                "Cache Hit": point.get("cache_hit"),
                "Provider": point.get("provider"),
                "Formatted Address": point.get("formatted_address"),
                "Lat": point.get("lat"),
                "Lng": point.get("lng"),
                "Warning": point.get("warning"),
            }
        )
    return pd.DataFrame(rows)


def build_demand_geocode_map_html(geocode_result: dict[str, Any]) -> str:
    school = dict(geocode_result.get("school") or {})
    points = [
        point
        for point in [school, *list(geocode_result.get("demand_points") or [])]
        if point.get("status") == "ok" and point.get("lat") is not None and point.get("lng") is not None
    ]
    if not points:
        return ""

    center_lat = sum(float(point["lat"]) for point in points) / len(points)
    center_lng = sum(float(point["lng"]) for point in points) / len(points)
    fmap = folium.Map(location=[center_lat, center_lng], zoom_start=12, tiles="OpenStreetMap")
    bounds: list[list[float]] = []

    if school.get("status") == "ok":
        school_location = [float(school["lat"]), float(school["lng"])]
        bounds.append(school_location)
        folium.Marker(
            location=school_location,
            tooltip="School",
            popup=str(school.get("formatted_address") or school.get("address") or "School"),
            icon=folium.Icon(color="blue", icon="home"),
        ).add_to(fmap)

    for point in list(geocode_result.get("demand_points") or []):
        if point.get("status") != "ok":
            continue
        location = [float(point["lat"]), float(point["lng"])]
        bounds.append(location)
        students = int(point.get("student_count", 0) or 0)
        folium.CircleMarker(
            location=location,
            radius=max(5, min(14, 4 + students)),
            color="#d9480f",
            fill=True,
            fill_color="#ff922b",
            fill_opacity=0.72,
            tooltip=f"{students} student(s)",
            popup=str(point.get("formatted_address") or point.get("address") or ""),
        ).add_to(fmap)

    if len(bounds) > 1:
        fmap.fit_bounds(bounds, padding=(20, 20))
    return fmap._repr_html_()
