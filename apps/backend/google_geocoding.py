"""Google-mode address resolution with a separate, expiring JSON cache."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import threading
import uuid
from collections import Counter

from filelock import FileLock
from json_cache_store import load_json_object, save_json_object
from google_pickup_points import PickupResolver
from google_address_identity import identity_check, candidate_text, query_variants

POLICY = "google-geocode-v2"
TTL = timedelta(days=30)
FAILURE_TTL = timedelta(minutes=15)
_LOCK = threading.RLock()


def cache_path():
    import client_runtime as runtime
    return Path(os.environ.get("BRP_GOOGLE_GEOCODE_CACHE_PATH") or
                runtime.CACHE_DIR / "google_geocode_cache.json")


def valid_entry(entry, now):
    try:
        created = datetime.fromisoformat(entry["resolved_at"])
        expires = datetime.fromisoformat(entry["expires_at"])
        limit = FAILURE_TTL if entry.get("state") == "unresolved" else TTL
        valid = (entry["policy"] == POLICY and entry["provider"] == "google"
                and entry["coordinate_system"] == "WGS84"
                and timedelta(0) <= now-created < limit and now < expires <= created+limit)
        if entry.get("state") == "unresolved":
            return (valid and isinstance(entry.get("details"), dict)
                    and entry.get("code") in {"google_geocode_unresolved", "google_geocode_ambiguous"})
        return (valid and entry.get("identity_evidence", {}).get("identity_status") == "matched"
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
    reasons = Counter()
    for result in payload.get("results") or []:
        geometry = result.get("geometry") or {}
        if geometry.get("location_type") not in {
                "ROOFTOP", "RANGE_INTERPOLATED", "GEOMETRIC_CENTER"}:
            reasons["coarse_geometry"] += 1
            continue
        evidence = identity_check(address, result)
        if evidence["issues"]:
            reasons.update(evidence["issues"])
            continue
        location = geometry.get("location") or {}
        try:
            lat, lng = float(location["lat"]), float(location["lng"])
        except (TypeError, ValueError, KeyError):
            reasons["invalid_coordinate"] += 1
            continue
        if (not all(math.isfinite(v) for v in (lat, lng)) or abs(lat) > 90 or abs(lng) > 180
                or not runtime.is_plausible_geocode_result(country, city, lat, lng,
                    candidate_text(result))):
            reasons["city_or_coordinate_mismatch"] += 1
            continue
        value = {"lat": lat, "lng": lng, "google_place_id": str(result.get("place_id") or ""),
                 "identity_evidence": evidence}
        # Duplicate provider representations are not different entrances. Distinct
        # POIs remain ambiguous even when nearby; distance is never a ranking score.
        duplicate = any(meters((lat, lng), (p["lat"], p["lng"])) < 1 and (
            value["google_place_id"] and value["google_place_id"] == p["google_place_id"] or
            "street_address" in result.get("types", []) and p.get("street_address")) for p in accepted)
        if not duplicate:
            accepted.append({**value, "street_address": "street_address" in result.get("types", [])})
    if not accepted:
        raise ValidationUnavailable("google_geocode_unresolved", details={
            "reason_counts": dict(reasons), "candidate_count": len(payload.get("results") or []),
            "next_action": "resolve_place_or_entrance_identity"})
    if len(accepted) > 1:
        raise ValidationUnavailable("google_geocode_ambiguous", details={
            "reason_counts": {"multiple_matching_identities": len(accepted)},
            "matching_place_ids": [p["google_place_id"] for p in accepted],
            "next_action": "confirm_platform_or_entrance"})
    return {k: v for k, v in accepted[0].items() if k != "street_address"}


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
        self.negative_cache_hits = 0

    def _resolve_uncached(self, country, city, address):
        from google_final_validation import ValidationUnavailable, is_local_measurement_error
        combined = {"status": "OK", "results": []}
        queries = [address]
        failure = None
        for index in range(3):
            if index >= len(queries):
                break
            self.check_canceled()
            payload = self.lookup(country, city, queries[index], self.budget_id, self.check_canceled, self._count)
            if payload.get("status") not in {"OK", "ZERO_RESULTS"}:
                select_location(payload, country, city, address)
            combined["results"].extend(payload.get("results") or [])
            try:
                value = select_location(combined, country, city, address)
                value["identity_evidence"]["query_attempts"] = index+1
                return value
            except ValidationUnavailable as exc:
                if not is_local_measurement_error(exc):
                    raise
                failure = exc
                failure.details["query_attempts"] = index+1
                if index == 0:
                    queries.extend(query_variants(address, city, payload))
        raise failure

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
        with _LOCK, FileLock(str(self.path)+".lock", timeout=180):
            now = self.now()
            entries = load_json_object(self.path)
            valid = {k: v for k, v in entries.items() if isinstance(v, dict) and valid_entry(v, now)}
            if valid != entries:
                save_json_object(self.path, valid)
            entry = valid.get(key)
            if entry is None:
                try:
                    location = self._resolve_uncached(country, city, address)
                except ValidationUnavailable as exc:
                    from google_final_validation import is_local_measurement_error
                    if is_local_measurement_error(exc):
                        self.check_canceled()
                        self.cache[key] = (str(exc), deepcopy(exc.details))
                        valid[key] = {"state": "unresolved", "code": str(exc), "details": deepcopy(exc.details),
                            "provider": "google", "coordinate_system": "WGS84", "policy": POLICY,
                            "resolved_at": now.isoformat(), "expires_at": (now+FAILURE_TTL).isoformat()}
                        save_json_object(self.path, valid)
                    raise
                self.check_canceled()
                entry = {**location, "provider": "google", "coordinate_system": "WGS84",
                         "policy": POLICY, "resolved_at": now.isoformat(), "expires_at": (now+TTL).isoformat()}
                valid[key] = entry
                save_json_object(self.path, valid)
            else:
                self.cache_hits += 1
                if entry.get("state") == "unresolved":
                    self.negative_cache_hits += 1
                    raise ValidationUnavailable(entry["code"], details=deepcopy(entry["details"]))
        # Never carry AMap provenance/entrance warnings into a Google coordinate.
        cleaned = {k: deepcopy(v) for k, v in point.items()
                   if not k.startswith(("amap_", "pickup_", "geocode_"))
                   and k not in {"lat", "lng", "plot_lat", "plot_lng", "formatted_address", "warning"}}
        cleaned.update(provider="google", coordinate_system="WGS84", country=country, city=city,
                       address=address, requested_address=address, formatted_address=address,
                       lat=entry["lat"], lng=entry["lng"], plot_lat=entry["lat"], plot_lng=entry["lng"],
                       google_place_id=entry["google_place_id"], geocode_status="ok",
                       pickup_precision_status="identity_matched", pickup_resolution_status="identity_matched",
                       pickup_precision_issues=[], google_geocode_resolved_at=entry["resolved_at"],
                       google_geocode_expires_at=entry["expires_at"], google_geocode_policy=POLICY,
                       google_geocode_identity=deepcopy(entry["identity_evidence"]))
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
