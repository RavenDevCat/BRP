# Vehicle Catalog Draft

This catalog is a planning baseline for the future "addresses + riders -> recommended fleet" workflow. It is not a legal compliance list or a final supplier quote. Seat counts vary by trim, licensing, whether the driver is included, and local operating rules, so contractor confirmation is still required before deployment.

## Capacity Rule

- `listed_seats`: published or commonly quoted vehicle seat count.
- `monitor_seats`: non-student seats reserved for bus monitors or assistants.
- `student_capacity`: `listed_seats - monitor_seats`.

The default monitor assumption is `1`, but the user should be able to override it per scenario. For example, a 25-seat vehicle with 1 monitor gives 24 student seats; with 2 monitors it gives 23 student seats.

## Korea Defaults

| Vehicle type | Listed seats | Default student seats | Notes |
| --- | ---: | ---: | --- |
| 9-seat MPV / Carnival class | 9 | 8 | Small sparse routes. |
| 11-seat MPV / Staria class | 11 | 10 | Small-group / narrow-area routes. |
| 15-seat van / Solati class | 15 | 14 | Bridge between MPV and mini bus. |
| 25-seat mini bus / County class | 25 | 24 | Common school / academy shuttle bucket. |
| 29-seat mini bus / County class | 29 | 28 | Larger County-style mini bus bucket. |
| 35-seat mid bus | 35 | 34 | Common Korean charter planning bucket. |
| 45-seat large bus | 45 | 44 | Common Korean large charter planning bucket. |
| 45-seat electric bus | 45 | 44 | Diesel cost should be skipped. |

## China Defaults

| Vehicle type | Listed seats | Default student seats | Notes |
| --- | ---: | ---: | --- |
| 7-seat van | 7 | 6 | Smallest low-density route bucket. |
| 14-seat van / light bus | 14 | 13 | Bridge between MPV and 19-seat bus. |
| 19-seat school bus | 19 | 18 | Common small school-bus bucket. |
| 35-seat mid bus | 35 | 34 | Common mid-size bucket. |
| 45-seat school bus | 45 | 44 | Conservative large-bus default. |
| 56-seat school bus / coach | 56 | 55 | Upper large-bus planning bucket. |
| 44-seat electric bus | 44 | 43 | Diesel cost should be skipped. |

## Implementation

The working catalog lives in `apps/client/vehicle_catalog.py`.

```python
from vehicle_catalog import get_vehicle_catalog

kr_catalog = get_vehicle_catalog("KR", monitor_seats=1)
cn_catalog = get_vehicle_catalog("CN", monitor_seats=2)
```

The first planning layer lives in `apps/client/planning_assumptions.py` and `apps/client/fleet_selector.py`.

```python
from fleet_selector import select_vehicle_for_group

selection = select_vehicle_for_group(22, market="KR", mode="balanced", monitor_seats=1)
print(selection.selected_vehicle["display_name"])
```

The selector uses three hidden operating profiles:

| Mode | Intent | Default behavior |
| --- | --- | --- |
| `balanced` | Normal contractor-style default | Balance fewer vehicles, healthy load factor, and avoiding oversized buses. |
| `cost_saver` | Lower operating cost | Accept longer routes and higher load factors before adding vehicles. |
| `comfort_saver` | Shorter / lighter routes | Accept more vehicles and lower load factors to reduce crowding and route pressure. |

The user should not need to edit raw weights. Product UI should expose the mode, monitor seats, and basic toggles such as vans/electric availability.

## Demand Template v1

The first demand-input contract is intentionally much lighter than the current-plan audit workbook. It is used for early fleet-planning previews before full routing is connected.

Required sheet:

- `demand`

Required columns:

- `country`
- `city`
- `school_name`
- `school_address`
- `student_address`
- `student_count`

Optional columns:

- `notes`

Current behavior:

- the Fleet Planner Preview can download a starter template
- uploaded demand workbooks are parsed locally in the Streamlit client
- the preview summarizes row count, total students, unique addresses, and the demand table
- each row's `student_count` is treated as one early candidate rider group for vehicle selection
- `Validate & Geocode Demand` geocodes the school and student addresses for preview mapping
- successful geocode cache entries are reused before any provider call
- blank, placeholder, too-short, or malformed addresses are marked `bad_address` and are not sent to geocoding providers
- failed non-bad addresses are surfaced in the geocode table and excluded from future clustering
- `Build Demand Clusters` creates first-pass candidate route groups from geocoded demand points
- clustering v1 groups points by school direction sector and splits by max vehicle capacity / max stop count
- each cluster is immediately passed through the vehicle selector to recommend a vehicle type
- `Build OSRM + OR-Tools Route Preview` asks OSRM for road-network matrices inside each cluster and uses OR-Tools to order stops for a single open route
- generated route previews can be downloaded as a user-facing workbook with `generated_plan_assignments` and `generated_plan_fleet`
- the generated workbook represents an auto plan, not a supplier's current operating plan
- `Build Global OR-Tools Plan` skips sector clustering and solves all geocoded demand points against a candidate vehicle pool
- global planning uses OSRM road-network matrices, vehicle capacities, max route duration, max stops, and vehicle fixed costs
- `Submit Global Plan as Job` uses an internal compatibility adapter to map the auto plan into the legacy route-plan parser schema and posts it to the backend job queue

Not connected yet:

- direct opening of the submitted job detail from Fleet Planner Preview; users currently return to the main planner and refresh Job History

Still heuristic:

- clustering v1 uses direction sectors before route preview, so cluster membership is still heuristic; global OR-Tools is the preferred automatic-planning candidate
- route preview optimizes stop order inside each cluster, but does not yet reassign points between clusters based on road-time savings
- preview map route lines show stop order as straight connectors, while route table distance/time comes from OSRM road metrics
- exported plans are reviewable generated auto plans; operators should still inspect overlong routes, road access, and supplier constraints

Naming rule:

- `Current Plan` means a real user/supplier operating plan.
- `Generated Plan` or `Auto Plan` means a system-generated demand plan.
- `current_plan_assignments` / `current_plan_fleet` are legacy parser sheet names only. They should not appear in user-facing generated plan downloads.

This keeps the input contract stable before the heavier planning pipeline is attached.

## Source Pointers

- Korea: Kia Carnival, Hyundai Staria, Hyundai Solati, Hyundai County, Korean charter bus listings.
- China: Chinese school-bus supplier listings, Yutong / school-bus product references, GB 24407 overview references.

These sources are used to set default planning buckets only. The application should keep the catalog editable because real procurement options depend on the vendor and local operating contract.
