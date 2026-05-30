# BRP Updates

This document tracks major user-facing product and operations updates.

It is not a code changelog. Record changes here when users or operators should know that behavior, available tools, service providers, or recommended rerun guidance changed.

## 2026-05-30

### Product Name Standardized

- Standardized the product name to `BRP: Bus Route Planner`.
- The main workflow is named `Route Audit`.
- Auxiliary workflows are organized under `Side Tools`.

### React Preview Adds Distance & Cost

- Added the first React preview slice for Distance & Cost at `/distance`.
- The first slice supports reference-stop distance checks from uploaded Excel workbooks.
- Users can preview workbook sheets and columns, choose address/city/country fields, select road or straight-line distance mode, and review result tables.
- Current-plan route cost is now available in the React preview. Users can map route/order/bus columns, choose market cost defaults, and calculate per-route distance and one-way diesel cost.

### React Preview Adds Fleet Planner First Slice

- Added the first React preview slice for Fleet Planner at `/fleet`.
- Users can download the demand template, upload a demand workbook or enter rider groups manually, choose market and planning mode, and preview recommended vehicles plus estimated fleet mix.
- Demand workbook geocode preview is now available in the React preview, including summary metrics, result table, and map rendering.
- Demand clustering preview is now available in the React preview, including direction-sector selection, cluster metrics, cluster/stop tables, and map rendering.
- OSRM route preview is now available in the React preview, including to-school/from-school direction, route metrics, route/stop tables, and map rendering.
- Generated plan workbook download is now available for route preview and global plan results.
- Global OR-Tools planning is now available in the React preview, including generated route metrics, candidate vehicle count, stop detail, map rendering, and workbook download.
- Global plan job submission has been added to the React preview; it creates a normal backend planner job from the generated global plan.

### Mac Local Development Runtime Rebuilt

- Rebuilt the local Mac development runtime around Apple Silicon Anaconda at `/opt/anaconda3`.
- Created the `brp` conda environment for backend and Streamlit client development.
- Restored local historical job browsing after moving the project out of OneDrive into `/Users/developer/Developer/BRP`.

### React Job History Responsive Fix

- The React job history workspace no longer lets the History panel occupy the whole screen on smaller windows.
- Narrower layouts collapse History by default when viewing a selected job, reducing user confusion.

## Update Log Guidance

Add a new dated entry when a change affects user workflow, operational behavior, or interpretation of results, such as:

- a new user-facing tool or workflow
- a service provider change
- routing/geocoding/provider logic changes
- planner assumptions or optimization behavior changes
- report or audit interpretation changes
- migration notes that require users to rerun jobs for updated conclusions

Do not record routine refactors, local-only environment fixes, dependency churn, or small copy/style tweaks unless they change how users should operate or interpret BRP.
