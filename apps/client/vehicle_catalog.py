from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


DEFAULT_MONITOR_SEATS = 1


@dataclass(frozen=True)
class VehicleCatalogEntry:
    market: str
    vehicle_type: str
    display_name: str
    listed_seats: int
    default_monitor_seats: int
    propulsion: str = "diesel"
    category: str = "bus"
    notes: str = ""
    source_url: str = ""

    @property
    def default_student_capacity(self) -> int:
        return max(0, int(self.listed_seats) - int(self.default_monitor_seats))

    def with_monitor_seats(self, monitor_seats: int) -> dict[str, Any]:
        reserved = max(0, int(monitor_seats))
        payload = asdict(self)
        payload["student_capacity"] = max(0, int(self.listed_seats) - reserved)
        payload["monitor_seats"] = reserved
        return payload


KOREA_VEHICLE_CATALOG: tuple[VehicleCatalogEntry, ...] = (
    VehicleCatalogEntry(
        market="KR",
        vehicle_type="kr_van_9",
        display_name="9-seat MPV / Carnival class",
        listed_seats=9,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="van",
        notes="Common small-group option; supplier should confirm whether local quoted seats include driver.",
        source_url="https://www.kia.com/kr/vehicles/carnival/specification",
    ),
    VehicleCatalogEntry(
        market="KR",
        vehicle_type="kr_van_11",
        display_name="11-seat MPV / Staria class",
        listed_seats=11,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="van",
        notes="Useful for sparse or narrow-area routes.",
        source_url="https://www.hyundai.com/worldwide/en/mpv/staria-2021/wagon",
    ),
    VehicleCatalogEntry(
        market="KR",
        vehicle_type="kr_solati_15",
        display_name="15-seat van / Solati class",
        listed_seats=15,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="van",
        notes="Hyundai Solati bus has 15/16-seat configurations.",
        source_url="https://www.hyundai.com/kr/ko/c/products/bus/solati",
    ),
    VehicleCatalogEntry(
        market="KR",
        vehicle_type="kr_county_25",
        display_name="25-seat mini bus / County class",
        listed_seats=25,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="mini_bus",
        notes="Hyundai County school/charter line includes 25-seat layouts.",
        source_url="https://www.hyundai.com/kr/ko/c/products/bus/county",
    ),
    VehicleCatalogEntry(
        market="KR",
        vehicle_type="kr_county_29",
        display_name="29-seat mini bus / County class",
        listed_seats=29,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="mini_bus",
        notes="Hyundai County extra-long layouts include 29-seat options.",
        source_url="https://www.hyundai.com/kr/ko/c/products/bus/county",
    ),
    VehicleCatalogEntry(
        market="KR",
        vehicle_type="kr_mid_35",
        display_name="35-seat mid bus",
        listed_seats=35,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="mid_bus",
        notes="Common Korean charter/shuttle planning bucket.",
        source_url="https://www.slbus.co.kr/default/business/carinfo2.php",
    ),
    VehicleCatalogEntry(
        market="KR",
        vehicle_type="kr_large_45",
        display_name="45-seat large bus",
        listed_seats=45,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="large_bus",
        notes="Common Korean large charter/shuttle planning bucket.",
        source_url="https://www.slbus.co.kr/default/business/carinfo2.php",
    ),
    VehicleCatalogEntry(
        market="KR",
        vehicle_type="kr_ebus_45",
        display_name="45-seat electric bus",
        listed_seats=45,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        propulsion="electric",
        category="large_bus",
        notes="Planning bucket for supplier-provided electric large bus; diesel cost should be skipped.",
        source_url="https://www.mobumbus.co.kr/bus_types.php?category=41%EC%9D%B8%EC%8A%B9+%EB%8C%80%ED%98%95",
    ),
)


CHINA_VEHICLE_CATALOG: tuple[VehicleCatalogEntry, ...] = (
    VehicleCatalogEntry(
        market="CN",
        vehicle_type="cn_van_7",
        display_name="7-seat van",
        listed_seats=7,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="van",
        notes="Smallest default planning bucket for very low-density routes.",
    ),
    VehicleCatalogEntry(
        market="CN",
        vehicle_type="cn_van_14",
        display_name="14-seat van / light bus",
        listed_seats=14,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="van",
        notes="Useful bridge between MPV and 19-seat school bus.",
    ),
    VehicleCatalogEntry(
        market="CN",
        vehicle_type="cn_school_19",
        display_name="19-seat school bus",
        listed_seats=19,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="mini_bus",
        notes="Common lower bound in Chinese school-bus supplier listings.",
        source_url="https://www.jinglanbus.com/bus/7.html",
    ),
    VehicleCatalogEntry(
        market="CN",
        vehicle_type="cn_mid_35",
        display_name="35-seat mid bus",
        listed_seats=35,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="mid_bus",
        notes="Common mid-size planning bucket; many suppliers list 31-36 seat ranges.",
        source_url="https://www.alibaba.com/product-detail/Y-utong-ZK6808-35-Seats-Automatic_1601111757383.html",
    ),
    VehicleCatalogEntry(
        market="CN",
        vehicle_type="cn_school_45",
        display_name="45-seat school bus",
        listed_seats=45,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="large_bus",
        notes="Conservative large-bus default; also aligns with preschool special school bus upper-bound discussions.",
        source_url="https://www.chinaautoregs.com/archives/7471",
    ),
    VehicleCatalogEntry(
        market="CN",
        vehicle_type="cn_school_56",
        display_name="56-seat school bus / coach",
        listed_seats=56,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        category="large_bus",
        notes="Upper planning bucket for primary / junior-middle special school bus or large coach suppliers.",
        source_url="https://www.chinabuses.org/product/buses/2176.html",
    ),
    VehicleCatalogEntry(
        market="CN",
        vehicle_type="cn_ebus_44",
        display_name="44-seat electric bus",
        listed_seats=44,
        default_monitor_seats=DEFAULT_MONITOR_SEATS,
        propulsion="electric",
        category="large_bus",
        notes="Planning bucket for new-energy bus; diesel cost should be skipped.",
        source_url="https://en.yutong.com/res/res/2023/10/9/e-bus/91a0b1f019527e44ff320724944a0a32.pdf",
    ),
)


def get_vehicle_catalog(market: str, *, monitor_seats: int = DEFAULT_MONITOR_SEATS) -> list[dict[str, Any]]:
    normalized_market = str(market or "").strip().upper()
    catalog = CHINA_VEHICLE_CATALOG if normalized_market in {"CN", "CHINA"} else KOREA_VEHICLE_CATALOG
    return [entry.with_monitor_seats(monitor_seats) for entry in catalog]


def get_vehicle_catalog_for_country(country: str, *, monitor_seats: int = DEFAULT_MONITOR_SEATS) -> list[dict[str, Any]]:
    normalized_country = str(country or "").strip().lower()
    if normalized_country in {"china", "cn", "\u4e2d\u56fd", "\u4e2d\u570b"}:
        return get_vehicle_catalog("CN", monitor_seats=monitor_seats)
    return get_vehicle_catalog("KR", monitor_seats=monitor_seats)
