# Current Product Overview

## What This Product Is Now

The platform is no longer just a route generator.

It now behaves like a school-bus audit and optimization-advice tool:

- import an existing operating plan
- replay and assess the current network
- generate multiple comparison baselines
- surface route-to-route improvement opportunities
- explain why those opportunities matter operationally

## Main Functional Blocks

### 1. Current Plan Import

The main input is now a current-plan workbook instead of a simple address list.

Required sheets:

- `current_plan_assignments`
- `current_plan_fleet`

What they represent:

- `current_plan_assignments`
  - route-level stop sequence
  - bus type
  - country / city / address
  - passenger count
- `current_plan_fleet`
  - bus type
  - seat count
  - vehicle count

Why this matters:

- the system now understands the supplier's current network as an explicit operating plan
- current-plan facts are separated from system baseline assumptions

Primary implementation:

- `apps/client/client_core.py`
- `apps/client/app.py`

### 2. Service Direction

The same workbook structure now supports both service directions:

- `From School`
- `To School`

Current behavior:

- users do not need a second template
- only the route-row ordering semantics change
- `From School`
  - the first row of each route is the shared school / depot row
- `To School`
  - the last row of each route is the shared school row

Why this matters:

- the product is no longer limited to afternoon drop-off planning
- current-plan replay, baseline generation, map labels, and route interpretation are direction-aware

Primary implementation:

- `apps/client/app.py`
- `apps/client/client_core.py`
- `apps/backend/planner_core.py`
- `apps/backend/BusingProblem.py`

### 3. Current Plan Audit

This is the "what does the current network really look like?" layer.

The system:

- geocodes the imported stops
- rebuilds route metrics on real road-network logic
- computes current-plan operating indicators

Current audit output includes:

- route count
- stop count
- average route distance
- average route duration
- average load factor
- low-load route count
- overlong route count
- route-level diagnostics table

Important behavior:

- metrics are shown as average per route rather than only whole-network totals
- load-factor calculations use current-plan fleet facts when available

Primary implementation:

- `apps/backend/planner_core.py`

### 4. Flexible Fleet Assumptions

The baseline-fleet controls are no longer hard-coded to literal bus-type names such as:

- `Large Bus`
- `Mid Bus`
- `Small Bus`

Current behavior:

- uploaded `current_plan_fleet` can auto-fill the baseline fleet controls
- the three baseline fleet slots now support editable labels
- solver logic now relies on capacity ordering rather than only literal bus-type names

Why this matters:

- supplier bus types can differ from the original naming convention
- the product can still build like-for-like, constrained, and free baselines without forcing the user to rename their fleet first

Primary implementation:

- `apps/client/app.py`
- `apps/backend/planner_core.py`
- `apps/backend/BusingProblem.py`

### 5. Like-for-Like Baseline

This is the fairest first benchmark.

The system keeps:

- the same route count
- the same stop allocation
- the same bus mix

It only improves:

- stop order inside each existing route

Question this baseline answers:

- "If we do not change the operating structure, how much better could the network be just by sequencing stops more intelligently?"

Primary implementation:

- `apps/backend/planner_core.py`

### 6. Route Reallocation Opportunities

This is the local-improvement engine.

The system:

- identifies weak routes
- finds plausible receiving routes
- simulates small local transfers
- evaluates network time / distance improvement
- evaluates whether a weak route moves toward consolidation or removal

Current route-action classifications:

- `Local improvement`
- `Consolidation path`
- `Strong removal path`
- `Route removable now`

Important modeling choice:

- the system prefers bounded, explainable local moves instead of brute-force full-network redesign

Primary implementation:

- `apps/backend/planner_core.py`

### 7. Constrained Improvement Baseline

This is the middle benchmark between like-for-like and free optimization.

The system:

- does not redesign the entire network
- selects a small set of high-confidence route-to-route transfers
- prioritizes routes with stronger route-level action signals
- can apply a small compatible transfer package from the same weak route

What this baseline is meant to represent:

- a mild optimization package that operations teams could realistically discuss or test

Current state:

- selected constrained moves are grouped into packages
- package-level post-action judgments are generated
- those package outcomes now feed both findings and comparison recommendations
- merge-readiness judgments now distinguish between:
  - `Safe merge candidate`
  - `Monitor receiving route`
  - `Receiving route stressed`
- post-package route outcomes now show how both the sending route and receiving route change after the recommended package

Primary implementation:

- `apps/backend/planner_core.py`
- `apps/client/app.py`

### 8. Free Optimization Baseline

This is the theoretical upper-bound reference.

The system:

- is allowed to regroup imported planning stops more freely
- uses free-baseline vehicle-ratio settings rather than current-plan fleet facts

What this baseline answers:

- "If we are not constrained by the current operating structure, what could the network look like?"

Primary implementation:

- `apps/backend/planner_core.py`

### 9. Nearby Private Access v1

This is attached to the `Nearby Aggregated` scenario rather than the current-plan audit.

Current logic:

- when nearby aggregation pulls multiple original addresses into a shared pickup / cluster center
- the system tracks which original addresses were clustered
- computes OSRM-based private-drive time and distance back to the shared pickup
- groups clustered riders back under each cluster center

Current output:

- `Nearby Aggregated Private Access v1`
- `Nearby Cluster Centers`
- `Private-Drive Rider Detail`
- map markers for:
  - cluster centers
  - clustered original addresses
  - OSRM-based private-drive connection paths

Why this matters:

- nearby aggregation is no longer a black-box stop-collapse step
- users can now see who got clustered, where they were clustered to, and what private access burden that creates

Primary implementation:

- `apps/backend/planner_core.py`
- `apps/backend/BusingProblem.py`
- `apps/client/client_runtime.py`
- `apps/client/app.py`

### 10. Further Most Stop Scenario

This is a new scenario layered on top of the nearby-aggregated network.

Current logic:

- treat the nearby-aggregated network as the baseline
- identify the configured `xx-minute mark` on each route
- convert stops beyond that mark into `private drive stops`
- compute OSRM-based private-drive time, distance, and geometry from each removed stop back to the `xx-minute-mark` pickup

Current output:

- a dedicated `Further Most Stop` scenario in `Baseline Scenarios`
- a dedicated `Further Most Stop` map in `Maps`
- route summary entries for:
  - `Private drive stops`
  - `Further Most Pickups`

Why this matters:

- the product can now model a practical service-boundary compromise
- bus service can stop at the workable edge of the route while still quantifying the private-drive burden for the remaining stops

Primary implementation:

- `apps/backend/planner_core.py`
- `apps/backend/BusingProblem.py`
- `apps/client/client_core.py`
- `apps/client/app.py`

### 11. Traffic Assumptions

The product includes a practical time-adjustment layer.

Current approach:

- duration-only multiplier
- distance unchanged
- profile-based traffic assumptions

Supported profiles:

- `Off-Peak`
- `AM Peak`
- `PM Peak`

Current enhancement:

- city-aware defaults for supported cities

Why this matters:

- makes audit and baseline comparisons more realistic without requiring full real-time traffic integration

Primary implementation:

- `apps/backend/planner_core.py`
- `apps/backend/BusingProblem.py`
- `apps/client/app.py`

### 12. Korea / Kakao / South Korea OSRM Support

The Korea path has been separated from the China path.

Current logic:

- Korea geocoding uses Kakao
- Korea routing uses the South Korea OSRM instance
- Korea address preprocessing and alias support exist to improve hit rate on non-standard stop descriptions

Primary implementation:

- `apps/client/client_runtime.py`

## User-Facing Output Structure

The frontend is now organized more like a report than a raw debug tool.

Main sections:

- `Executive Summary`
- `Current Plan Audit`
- `Baseline Scenarios`
- `Maps`
- `Diagnostics`

Current-plan and constrained-baseline output now includes:

- route-level diagnostics
- route-level action signals
- constrained transfer packages
- package-aware findings
- merge-readiness signals
- post-package route outcomes

Map output now includes more than just bus polylines. Depending on scenario, it can also show:

- nearby cluster centers
- clustered rider addresses
- OSRM private-drive connection paths
- further-most pickups
- private-drive stops beyond the route-service boundary

Primary implementation:

- `apps/client/app.py`

## Job Workflow

The product now supports a job-style workflow instead of only synchronous result display.

Current capabilities:

- submit a job
- inspect job history
- refresh job history without reloading the whole page
- reopen completed job results
- terminate running jobs
- delete saved jobs

Time handling:

- backend stores UTC timestamps
- frontend converts and displays timestamps in the browser's local timezone

Primary implementation:

- `apps/backend/backend_service.py`
- `apps/backend/backend_job_runner.py`
- `apps/client/app.py`

## Infra / OSRM Runtime Shape

The live OSRM data path has been moved out of OneDrive-backed project storage.

Current expected OSRM data root:

- `/Users/developer/brp-osrm-data`

Why:

- avoids Docker / OneDrive file-read instability
- separates runtime routing data from the project working tree

Primary implementation:

- `ops/scripts/run_osrm_stack.sh`

## Current Product Positioning

The product should now be thought of as:

- an imported-network replay tool
- a current-plan audit tool
- a baseline comparison tool
- a local route-to-route optimization advisor

It is no longer just:

- "upload addresses and generate routes"

## What Is Still Not Finished

Key gaps still remaining:

- more validation on real workbooks
- continued tuning of package-aware wording and thresholds
- large-vehicle road-access constraints
- stronger authentication / user directory
- additional cleanup of difficult Korean stop descriptions
- additional validation of `To School` recommendation wording against real inbound workbooks
- further tuning of nearby / further-most private-access thresholds and summary wording

## Best Current Summary

If someone asks what the product does today, the shortest accurate answer is:

- import the current school-bus network
- replay and audit it in either `From School` or `To School` mode
- compare it against like-for-like, constrained, free, nearby-aggregated, and further-most baselines
- identify high-value local route-to-route adjustments
- explain whether those adjustments point toward local improvement, consolidation, or route removal
- quantify private-drive burden when nearby aggregation or service-boundary truncation pushes riders to shared pickups
