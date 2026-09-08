"""Operator-confirmed CN pickups, independent of disposable geocode caches."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Any


class PickupRevisionConflict(ValueError):
    pass


def _path() -> Path | None:
    configured = os.environ.get("BRP_PICKUP_OVERRIDES_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    runtime = os.environ.get("BRP_RUNTIME_DB_PATH", "").strip()
    return Path(runtime).expanduser().with_name("pickup_overrides.sqlite") if runtime else None


def _identity(city_code: str, address: str) -> tuple[str, str]:
    address = " ".join(str(address).strip().split())
    if not (isinstance(city_code, str) and len(city_code) == 6 and city_code.isdigit()):
        raise ValueError("A supported CN city is required.")
    if not address or len(address) > 500:
        raise ValueError("A complete original address is required.")
    return city_code, address


def _decode(row) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["point"] = json.loads(result.pop("point_json"))
    result["active"] = result["point"] is not None
    return result


def _latest(db, city_code, address):
    return _decode(db.execute(
        "SELECT * FROM pickup_corrections WHERE city_code=? AND address=? ORDER BY revision DESC LIMIT 1",
        (city_code, address),
    ).fetchone())


def read_pickup_correction(city_code: str, address: str) -> dict[str, Any] | None:
    city_code, address = _identity(city_code, address)
    path = _path()
    if path is None or not path.exists():
        return None
    # Reads never initialize a database or mutate another process's cache.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return _latest(db, city_code, address)


def confirmed_pickup(city_code: str, address: str) -> dict[str, Any] | None:
    row = read_pickup_correction(city_code, address)
    if row is None or not row["active"]:
        return None
    return {**row["point"], "pickup_precision_status": "operator_confirmed",
            "pickup_precision_issues": [], "pickup_override_revision": row["revision"],
            "pickup_override_confirmed_at": row["created_at"]}


def change_pickup_correction(*, city_code: str, address: str, point: dict[str, Any] | None,
                            operator: str, reason: str, expected_revision: int) -> dict[str, Any]:
    city_code, address = _identity(city_code, address)
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("The current integer revision is required.")
    if not operator.strip() or not reason.strip() or len(reason) > 1000:
        raise ValueError("An authenticated operator and a short confirmation reason are required.")
    if point is not None:
        if point.get("provider") != "amap" or point.get("coordinate_system") != "GCJ02":
            raise ValueError("Confirmed pickups must carry explicit AMap GCJ02 coordinates.")
        for field, limit in (("lat", 90), ("lng", 180), ("plot_lat", 90), ("plot_lng", 180)):
            value = point.get(field)
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > limit:
                raise ValueError("Invalid pickup coordinate.")
    encoded = json.dumps(point, ensure_ascii=False, sort_keys=True, allow_nan=False)
    path = _path()
    if path is None:
        raise ValueError("Configure the runtime or pickup-correction database before saving.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(path), timeout=10)) as db:
        db.row_factory = sqlite3.Row
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS pickup_corrections (
                city_code TEXT NOT NULL, address TEXT NOT NULL, revision INTEGER NOT NULL,
                point_json TEXT NOT NULL, operator TEXT NOT NULL, reason TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY (city_code, address, revision))""")
            db.execute("BEGIN IMMEDIATE")
            previous = _latest(db, city_code, address)
            revision = previous["revision"] if previous else 0
            if revision != expected_revision:
                # A retry after an uncertain response is safe, but a stale edit is not.
                if (previous and revision == expected_revision + 1 and previous["point"] == point
                        and previous["operator"] == operator and previous["reason"] == reason.strip()):
                    return previous
                raise PickupRevisionConflict("The pickup changed; read its current revision before saving.")
            if point is None and not previous:
                raise ValueError("There is no saved correction to deactivate.")
            db.execute("INSERT INTO pickup_corrections VALUES (?, ?, ?, ?, ?, ?, ?)", (
                city_code, address, revision + 1, encoded, operator, reason.strip(),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ))
            return _latest(db, city_code, address)


def prepare_confirmation(payload: dict[str, Any], runtime) -> tuple[str, str, dict[str, Any] | None]:
    allowed = {"country", "city", "address", "lat", "lng", "poi_id", "poi_name", "reason",
               "expected_revision", "confirm", "active"}
    if payload.keys() - allowed or payload.get("confirm") is not True:
        raise ValueError("Explicit pickup confirmation is required; unknown fields are not accepted.")
    country, city = str(payload.get("country") or ""), str(payload.get("city") or "")
    config = runtime._china_city_config(city) if runtime.is_china_country(country) else None
    if not config:
        raise ValueError("A supported CN city is required.")
    city_code, address = _identity(str(config["amap_city"]), str(payload.get("address") or ""))
    if type(payload.get("active", True)) is not bool:
        raise ValueError("Active must be true or false.")
    if payload.get("active") is False:
        return city_code, address, None
    lat, lng = payload.get("lat"), payload.get("lng")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (lat, lng)):
        raise ValueError("Finite AMap GCJ02 latitude and longitude are required.")
    north, east, south, west = config["bbox"]
    if not (south <= lat <= north and west <= lng <= east):
        raise ValueError("The confirmed pickup must be inside the selected city.")
    poi_id, poi_name = str(payload.get("poi_id") or "").strip(), str(payload.get("poi_name") or "").strip()
    if not poi_id or not poi_name or len(poi_id) > 100 or len(poi_name) > 500:
        raise ValueError("The operator-confirmed AMap POI identity is required.")
    plot_lat, plot_lng = runtime.gcj02_to_wgs84(lat, lng)
    return city_code, address, {
        "provider": "amap", "coordinate_system": "GCJ02", "country": "China", "city": config["canonical"],
        "address": address, "requested_address": address, "lat": lat, "lng": lng,
        "plot_lat": plot_lat, "plot_lng": plot_lng, "formatted_address": poi_name,
        "geocode_level": "poi", "amap_poi_id": poi_id, "amap_poi_name": poi_name,
        "geocode_status": "ok", "geocode_source": "operator_confirmed_pickup",
    }
