"""Google candidate identity checks, separate from navigation endpoint checks."""
from dataclasses import dataclass
import re
import unicodedata

from amap_geocode_quality import _compact, _gate_tokens, _local_address, _road_tokens, _without_gates

COARSE_TYPES = {"country", "locality", "postal_code", "route", "political",
                "administrative_area_level_1", "administrative_area_level_2",
                "sublocality", "sublocality_level_1", "sublocality_level_2"}
TRANSIT_TYPES = {"bus_station", "bus_stop", "transit_station"}
NUMBER = re.compile(r"(?<!\d)\d+(?:-\d+)?(?:\u53f7|\u5f04)")
BUS = re.compile(r"\u516c\u4ea4(?:\u8f66)?\u7ad9|\u7ad9\u53f0")
SIDE = re.compile(r"\u5bf9\u9762|\u8def[\u4e1c\u897f\u5357\u5317]\u4fa7|\u5411[\u4e1c\u897f\u5357\u5317]|\u5f80[\u4e1c\u897f\u5357\u5317]")


def normalized(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def gate_text(value):
    text = re.sub(r"(?<=[\u4e1c\u897f\u5357\u53170-9])\s+(?=\u95e8|\u53f7|\u53e3)", "", normalized(value))
    return SIDE.sub(lambda match: " " + match.group() + " ", text)


@dataclass(frozen=True)
class AddressIdentity:
    district: str
    roads: tuple
    numbers: tuple
    gates: frozenset
    bus_stop: bool
    sides: tuple
    landmark: str


def parse_address(address):
    text = normalized(address)
    local = _local_address(text.removeprefix("\u4e2d\u56fd"))
    administrative = text.removeprefix("\u4e2d\u56fd")
    for suffix in ("\u7701", "\u5e02"):
        administrative = re.sub(r"^[\u4e00-\u9fff]{2,8}?" + suffix, "", administrative, count=1)
    district_match = re.match(r"[\u4e00-\u9fff]{2,8}?[\u533a\u53bf]", administrative)
    district = district_match.group() if district_match else ""
    if district.endswith(("\u5c0f\u533a", "\u793e\u533a", "\u6821\u533a", "\u56ed\u533a", "\u4e1c\u533a", "\u897f\u533a", "\u5357\u533a", "\u5317\u533a")):
        district = ""
    roads = tuple(dict.fromkeys(_road_tokens(re.sub(r"(\u8def|\u8857|\u9053)[\u4e0e\u548c\u53ca]", r"\1 ", local))))
    without_gates = _without_gates(gate_text(local))
    numbers = tuple(NUMBER.findall(without_gates))
    sides = tuple(SIDE.findall(local))
    residual = without_gates
    for road in roads:
        residual = residual.replace(road, "")
    residual = NUMBER.sub("", BUS.sub("", SIDE.sub("", residual)))
    residual = re.sub(r"\u4ea4\u53c9\u8def?\u53e3|\u4ea4\u6c47\u5904|\u8def\u53e3|\u95e8\u53e3|\u95e8\u5916", "", residual)
    residual = re.sub(r"^[\u4e0e\u548c\u53ca]+|[\u4e0e\u548c\u53ca]+$", "", residual)
    return AddressIdentity(district, roads, numbers, frozenset(_gate_tokens(gate_text(text))), bool(BUS.search(text)),
                           sides, _compact(residual))


def candidate_text(candidate):
    # Components supplement provider display text; request text is never evidence.
    parts = [candidate.get("formatted_address", "")]
    parts += [c.get("long_name", "") for c in candidate.get("address_components", [])]
    return " ".join(str(part) for part in parts)


def identity_check(address, candidate):
    spec = parse_address(address)
    text = candidate_text(candidate)
    compact = _compact(text)
    types = set(candidate.get("types") or [])
    issues = []
    if spec.district and _compact(spec.district) not in compact:
        issues.append("district_mismatch")
    if not types or types <= COARSE_TYPES:
        issues.append("coarse_place")
    if any(_compact(road) not in compact for road in spec.roads):
        issues.append("road_mismatch")
    for number in spec.numbers:
        if not re.search(r"(?<!\d)" + re.escape(number) + r"(?!\d)", compact):
            issues.append("lane_mismatch" if number.endswith("\u5f04") else "street_number_mismatch")
    if spec.gates and not spec.gates <= _gate_tokens(gate_text(text)):
        issues.append("gate_unconfirmed")
    if spec.sides and any(_compact(side) not in compact for side in spec.sides):
        issues.append("road_side_unconfirmed")
    if spec.bus_stop and not (types & TRANSIT_TYPES or BUS.search(text)):
        issues.append("bus_platform_unconfirmed")
    if spec.bus_stop and len(spec.roads) >= 2:
        positions = [compact.find(_compact(road)) for road in spec.roads]
        if all(p >= 0 for p in positions) and positions != sorted(positions):
            issues.append("bus_road_order_mismatch")
    if spec.landmark and spec.landmark not in compact:
        issues.append("landmark_unconfirmed")
    if not (spec.numbers or spec.landmark or spec.bus_stop or len(spec.roads) >= 2):
        issues.append("insufficient_identity")
    return {"issues": sorted(set(issues)), "partial_match": bool(candidate.get("partial_match")),
            "location_type": (candidate.get("geometry") or {}).get("location_type"),
            "identity_status": "unresolved" if issues else "matched",
            "entrance_status": "named_gate_matched" if spec.gates and not issues else "not_verified",
            "route_endpoint_status": "not_measured",
            "matched_fields": [name for name, present in (
                ("district", spec.district), ("roads", spec.roads), ("number_and_kind", spec.numbers), ("named_gate", spec.gates),
                ("bus_platform", spec.bus_stop), ("road_side", spec.sides), ("landmark", spec.landmark))
                if present and not issues]}


def query_variants(address, city, payload):
    """At most two identity-preserving text alternatives; never invent a gate."""
    import client_runtime as runtime
    config = runtime._china_city_config(city)
    city_name = next((a for a in (config or {}).get("aliases", []) if re.search(r"[\u4e00-\u9fff]", a)), city)
    if re.search(r"[\u4e00-\u9fff]", city_name) and not city_name.endswith("\u5e02"):
        city_name += "\u5e02"
    text = normalized(address).removeprefix("\u4e2d\u56fd")
    if not text.startswith(city_name):
        text = city_name + text
    variants = ["\u4e2d\u56fd" + text]
    # An agreed district from road-matching candidates can refine a query, not a coordinate.
    spec = parse_address(address)
    districts = set()
    for item in payload.get("results") or []:
        components = item.get("address_components") or []
        city_matches = any(_compact(city_name) == _compact(c.get("long_name"))
            for c in components if set(c.get("types") or []) & {"locality", "administrative_area_level_1"})
        if city_matches and spec.roads and all(_compact(r) in _compact(candidate_text(item)) for r in spec.roads):
            districts.update(c["long_name"] for c in components
                if "sublocality_level_1" in c.get("types", []) and str(c.get("long_name", "")).endswith("\u533a"))
    if len(districts) == 1:
        district = next(iter(districts))
        local = text.removeprefix(city_name)
        if not re.match(r"[\u4e00-\u9fff]{2,8}\u533a", local):
            variants.append("\u4e2d\u56fd" + city_name + district + local)
    seen = {_compact(address)}
    result = []
    for variant in variants:
        key = _compact(variant)
        if key not in seen:
            seen.add(key)
            result.append(variant)
    return result[:2]
