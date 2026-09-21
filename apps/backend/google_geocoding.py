"""Google-mode address resolution with a separate, expiring JSON cache."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import threading
import uuid

from filelock import FileLock
from json_cache_store import load_json_object, save_json_object
from google_pickup_points import PickupResolver

POLICY = "google-geocode-v1"
TTL = timedelta(days=30)
_LOCK = threading.RLock()


def cache_path():
    import client_runtime as runtime
    return Path(os.environ.get("BRP_GOOGLE_GEOCODE_CACHE_PATH") or
                runtime.CACHE_DIR / "google_geocode_cache.json")


def valid_entry(entry, now):
    try:
        created = datetime.fromisoformat(entry["resolved_at"])
        expires = datetime.fromisoformat(entry["expires_at"])
        return (entry["policy"] == POLICY and entry["provider"] == "google"
                and entry["coordinate_system"] == "WGS84"
                and timedelta(0) <= now-created < TTL and now < expires <= created+TTL
                and all(type(entry[k]) in (int, float) and math.isfinite(entry[k])
                        and abs(entry[k]) <= bound for k, bound in (("lat", 90), ("lng", 180))))
    except (KeyError, TypeError, ValueError):
        return False


def clear_address(country, city, address):
    import client_runtime as runtime
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = runtime.geocode_cache_key(country, city, address)
    with _LOCK, FileLock(str(path)+".lock", timeout=60):
        entries = load_json_object(path)
        removed = [key] if key in entries else []
        if removed:
            entries.pop(key)
            save_json_object(path, entries)
    return {"cleared": len(removed), "removed": {"google": removed},
            "country": country, "city": city, "address": address}


def select_location(payload, country, city, address):
    import client_runtime as runtime
    from google_final_validation import ValidationUnavailable, meters
    status = str(payload.get("status") or "").upper()
    if status == "ZERO_RESULTS":
        raise ValidationUnavailable("google_geocode_unresolved", details={"provider_status": status})
    if status != "OK":
        raise ValidationUnavailable("google_geocode_provider_rejected", details={"provider_status": status})
    accepted = []
    for result in payload.get("results") or []:
        geometry = result.get("geometry") or {}
        if result.get("partial_match") or geometry.get("location_type") not in {
                "ROOFTOP", "RANGE_INTERPOLATED", "GEOMETRIC_CENTER"}:
            continue
        if set(result.get("types") or []) & {"country", "locality", "postal_code", "route",
                                             "administrative_area_level_1", "administrative_area_level_2"}:
            continue
        location = geometry.get("location") or {}
        try:
            lat, lng = float(location["lat"]), float(location["lng"])
        except (TypeError, ValueError, KeyError):
            continue
        if (not all(math.isfinite(v) for v in (lat, lng)) or abs(lat) > 90 or abs(lng) > 180
                or not runtime.is_plausible_geocode_result(country, city, lat, lng,
                    result.get("formatted_address", ""), requested_address=address)):
            continue
        accepted.append({"lat": lat, "lng": lng, "google_place_id": str(result.get("place_id") or "")})
    if not accepted:
        raise ValidationUnavailable("google_geocode_unresolved")
    if any(meters((p["lat"], p["lng"]), (accepted[0]["lat"], accepted[0]["lng"])) > 30 for p in accepted[1:]):
        raise ValidationUnavailable("google_geocode_ambiguous")
    return accepted[0]


def request_geocode(country, city, address, budget_id, check_canceled, counted):
    from google_final_validation import require_available, ValidationUnavailable, TZ
    from google_routes_transport import post_geocode
    from google_routes_quota import quota_periods
    from quota_store_sqlite import SqliteQuotaStore
    import requests
    import client_runtime as runtime
    require_available()
    check_canceled()
    code = "CN" if country.strip().upper() == "CN" else runtime.google_country_code(country)
    if code != "CN":
        raise ValidationUnavailable("google_country_not_enabled")
    params = {"address": ", ".join((address, city, country)), "language": "zh-CN",
              "components": "country:CN", "region": "cn"}
    store = SqliteQuotaStore(Path(os.environ["BRP_GOOGLE_FINAL_QUOTA_DB"]))
    periods = quota_periods(budget_id, datetime.now(TZ))
    store.reserve_rate_limit("google-final-geocoding", 2.0)
    check_canceled()
    try:
        # Keep the existing aggregate ledger so switching APIs cannot bypass the cap.
        store.reserve_usage("google_routes", "compute_routes_pro", periods,
                            sku_estimate="google_geocoding", provider_label="Google validation")
    except RuntimeError:
        raise ValidationUnavailable("google_budget_cap") from None
    counted()
    store.reserve_usage("google_geocoding", "geocode", [(kind, key, 0) for kind, key, _ in periods],
                        sku_estimate="google_geocoding", provider_label="Google Geocoding")
    succeeded = False
    try:
        response = post_geocode(params, budget_id)
        if response.status_code != 200:
            raise ValidationUnavailable(f"google_geocode_http_{response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValidationUnavailable("google_geocode_response_invalid")
        succeeded = payload.get("status") in {"OK", "ZERO_RESULTS"}
        return payload
    except (requests.RequestException, ValueError):
        raise ValidationUnavailable("google_geocode_transport_failed") from None
    finally:
        store.mark_usage_result("google_routes", "compute_routes_pro",
                                [(kind, key) for kind, key, _ in periods], succeeded=succeeded)
        store.mark_usage_result("google_geocoding", "geocode",
                                [(kind, key) for kind, key, _ in periods], succeeded=succeeded)


class GoogleGeocodeResolver(PickupResolver):
    """Share service-point conflict checks, not AMap lookup or AMap overrides."""
    def __init__(self, budget_id=None, *, check_canceled=lambda: None, lookup=None,
                 path=None, now=lambda: datetime.now(timezone.utc)):
        self.budget_id = budget_id or uuid.uuid4().hex
        self.check_canceled = check_canceled
        self.lookup = lookup or request_geocode
        self.path = Path(path) if path else cache_path()
        self.now = now
        self.cache = {}
        self.api_calls = 0
        self.cache_hits = 0

    def resolve(self, point):
        import client_runtime as runtime
        from google_final_validation import ValidationUnavailable
        self.check_canceled()
        country, city = str(point.get("country") or "China"), str(point.get("city") or "")
        address = str(point.get("requested_address") or point.get("address") or "").strip()
        if not address or not city:
            raise ValidationUnavailable("google_geocode_address_required")
        key = runtime.geocode_cache_key(country, city, address)
        if key in self.cache:
            code, details = self.cache[key]
            raise ValidationUnavailable(code, details=deepcopy(details))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Cross-process lock covers read/resolve/merge/write, preventing lost entries
        # and duplicate paid lookups for simultaneous requests at the same address.
        with _LOCK, FileLock(str(self.path)+".lock", timeout=60):
            now = self.now()
            entries = load_json_object(self.path)
            valid = {k: v for k, v in entries.items() if isinstance(v, dict) and valid_entry(v, now)}
            if valid != entries:
                save_json_object(self.path, valid)
            entry = valid.get(key)
            if entry is None:
                payload = self.lookup(country, city, address, self.budget_id, self.check_canceled, self._count)
                try:
                    location = select_location(payload, country, city, address)
                except ValidationUnavailable as exc:
                    from google_final_validation import is_local_measurement_error
                    if is_local_measurement_error(exc):
                        self.cache[key] = (str(exc), deepcopy(exc.details))
                    raise
                self.check_canceled()
                entry = {**location, "provider": "google", "coordinate_system": "WGS84",
                         "policy": POLICY, "resolved_at": now.isoformat(), "expires_at": (now+TTL).isoformat()}
                valid[key] = entry
                save_json_object(self.path, valid)
            else:
                self.cache_hits += 1
        # Never carry AMap provenance/entrance warnings into a Google coordinate.
        cleaned = {k: deepcopy(v) for k, v in point.items()
                   if not k.startswith(("amap_", "pickup_", "geocode_"))
                   and k not in {"lat", "lng", "plot_lat", "plot_lng", "formatted_address", "warning"}}
        cleaned.update(provider="google", coordinate_system="WGS84", country=country, city=city,
                       address=address, requested_address=address, formatted_address=address,
                       lat=entry["lat"], lng=entry["lng"], plot_lat=entry["lat"], plot_lng=entry["lng"],
                       google_place_id=entry["google_place_id"], geocode_status="ok",
                       pickup_precision_status="matched", pickup_resolution_status="matched",
                       pickup_precision_issues=[], google_geocode_resolved_at=entry["resolved_at"],
                       google_geocode_expires_at=entry["expires_at"], google_geocode_policy=POLICY)
        return cleaned

    def resolve_address(self, country, city, address, source_excel_rows=None):
        from google_final_validation import ValidationUnavailable, is_local_measurement_error
        try:
            return self.resolve({"country": country, "city": city, "address": address}), None, False
        except ValidationUnavailable as exc:
            if not is_local_measurement_error(exc):
                raise
            return None, {"address": address, "warning": str(exc),
                          "source_excel_rows": ",".join(map(str, source_excel_rows or []))}, False


def refresh_fleet_points(result, geocoder):
    result = deepcopy(result)
    school = dict(result.get("school") or {})
    defaults = {k: school.get(k) for k in ("country", "city")}
    def resolve(point):
        return {**geocoder.resolve({**defaults, **point}), "status": "ok"}
    result["school"] = resolve(school)
    if "demand_points" in result:
        result["demand_points"] = [resolve(point) for point in result["demand_points"]]
    for cluster in result.get("clusters") or []:
        cluster["points"] = [resolve(point) for point in cluster.get("points") or []]
    return result
