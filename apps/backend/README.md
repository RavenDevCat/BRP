# Busing Routing Designer

Local interface scaffold for the bus routing planner.

## Current Scope

- Upload a current-plan workbook
- Read `current_plan_assignments` and `current_plan_fleet`
- Assess the current routing scheme
- Compare the imported plan with like-for-like and free-optimization baselines
- Output route-audit summaries plus HTML routing maps

## Planned Next Steps

- Replace monkey-patched globals with a clean core API
- Add parameter controls in the UI
- Add result summary tables and embedded HTML previews
- Prepare for Windows exe packaging

## Quick Start

```powershell
streamlit run app.py
```

## Expected Excel Format

- `current_plan_assignments`
  - `route_id`
  - `stop_sequence`
  - `bus_type`
  - `country`
  - `city`
  - `address`
  - `passenger_count`
- `current_plan_fleet`
  - `bus_type`
  - `seat_count`
  - `vehicle_count`
- The first row of every route in `current_plan_assignments` is treated as the shared depot/start point.
