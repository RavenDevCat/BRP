from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _section(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


def test_route_audit_inline_and_fullscreen_maps_keep_map_and_workbook_download_controls() -> None:
    source = _source("apps/web/src/features/results/job-result-view.tsx")
    maps_panel = _section(source, "function MapsPanel({", "function RouteDiagnosticsTable")

    assert "downloadInteractiveMap" in maps_panel
    assert "workbookExportUrl" in maps_panel
    assert maps_panel.count('t("Download map")') >= 2
    assert maps_panel.count('t("Download workbook")') >= 2
    assert maps_panel.count("<FileSpreadsheet") >= 2


def test_fleet_planner_inline_and_fullscreen_maps_keep_map_and_workbook_download_controls() -> None:
    source = _source("apps/web/src/features/fleet/fleet-planner-page.tsx")
    maps_panel = _section(source, "function ToolMapsPanel({", "function VehicleConfigModal")

    assert "downloadInteractiveMapHtml" in maps_panel
    assert "downloadBase64Workbook" in maps_panel
    assert "canDownloadWorkbook" in maps_panel
    assert maps_panel.count("<FileSpreadsheet") >= 2
    assert maps_panel.count("Workbook") >= 2


def test_standalone_route_audit_map_does_not_depend_on_private_tile_or_api_hosts() -> None:
    source = _source("apps/web/src/features/results/job-result-view.tsx")
    standalone_builder = _section(
        source,
        "function buildStandaloneInteractiveMapHtml",
        "type MapOutput =",
    )

    assert "https://tile.openstreetmap.de/{z}/{x}/{y}.png" in standalone_builder
    forbidden_private_dependencies = (
        "/api/map-tiles",
        "127.0.0.1",
        "localhost",
        "100.87.",
        "143.64.",
        "ravenapis.com",
        "cloudflareaccess.com",
        "CF_AppSession",
    )
    for dependency in forbidden_private_dependencies:
        assert dependency not in standalone_builder
