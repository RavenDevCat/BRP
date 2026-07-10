# BRP Updates

This document tracks major user-facing product, architecture, and operations
updates. It is not a code changelog. Record changes here only when users or
operators should know that behavior, available tools, service providers,
runtime architecture, or recommended rerun guidance changed.

## 2026-07-10

### Hard-Constrained Scenario Selection

- Route candidates now use unscaled OSRM travel times and direct AMap or Kakao
  final-route validation; retired traffic coefficients no longer alter solver
  inputs.
- The balanced and protected scenarios run independently and apply the user's
  stop time-impact limit as a hard constraint across every optimized stop.
- A scenario is recommended only when its provider time window, requested
  vehicle saving, and hard time-impact checks all pass. When several qualify,
  the result with the fewest vehicles is recommended.
- Existing completed jobs keep their stored results. Rerun an audit to use the
  new solve and recommendation flow.

## 2026-06-21

### Backend Runtime Standardization

- Job dispatch now uses a dedicated SQLite-backed queue claim before worker
  subprocess launch, reducing duplicate-start risk while keeping the CN Linux
  and KR Windows worker model.
- FastAPI POST request bodies now use Pydantic request models instead of
  untyped payloads.
- Shared JSON caches now write through a common atomic helper across backend
  and client cache flows.
- Existing completed jobs do not need to be rerun.

### Runtime Coordination Standardization

- Provider QPS limits and usage counters moved to a shared SQLite quota store.
- Google geocode, Google Routes, Kakao Navi, DeepSeek, AMap, and the Bangkok
  Google geocode relay now reserve slots through the same coordination layer.
- Existing provider usage JSON files are legacy migration evidence only and
  should not be edited directly.

### React Static Proxy Retirement

- The supported frontend path is now Nginx serving built React assets with SPA
  fallback and same-origin `/api/*` proxying.
- The old Python React static/proxy helper and KR preview/public scheduled
  tasks are retired.
- KR public frontend should stay on the Nginx origin, not the old `4173`
  preview path.

### Google Geocode Relay And OSRM Manager

- The Bangkok-only Google geocode relay now runs as a FastAPI/uvicorn ASGI app
  with the same restricted API surface and quota controls.
- OSRM manager state, startup locks, stale-lock reporting, and active-use
  leases now use SQLite instead of handwritten JSON state and platform locks.

### Documentation And Env Hygiene

- Public and private handoff documents were trimmed so normal agent startup
  reads focus on current state rather than historical incident detail.
- Retired env keys such as the old runtime-store JSON path were removed from
  active server env files; current runtime paths are SQLite-based.

## 2026-06-20

### Korea Route-Level Traffic Attribution

- KR jobs can use route-network traffic attribution similar to the CN model:
  Kakao Navi samples are matched by route fingerprint and Seoul-metro
  geography instead of applying one flat market coefficient.
- Seoul, Incheon, Gyeonggi, and nearby cities share a reusable Seoul Metro
  attribution bucket.
- Existing completed jobs keep their stored results; rerun a job to regenerate
  route timings with the attributed KR behavior.

### OSRM Manager Capacity Policy

- On-demand OSRM startup has an explicit running-region capacity policy.
- The manager can clean expired idle regions and reclaim manager-owned regions
  without active worker leases when capacity requires it.
- Diagnostics now report active-use, lock, and reclaimability status.

### KR Windows Backend Compatibility

- OSRM manager imports are safe on Windows and Linux.
- KR backend should continue to be started through the standard Scheduled Task
  deployment flow.

## 2026-06-14

### Fleet Planner Map Workspace

- Fleet Planner history results use the shared React MapLibre route map instead
  of the old embedded HTML map panel.
- Results are consolidated into Plan, Map, and Review tabs.
- Fleet Planner maps include fullscreen open, interactive map download, and
  generated workbook download actions.
- Compatible legacy Fleet Planner runs can be hydrated into the interactive map
  when enough route detail exists.

### Bangkok Market Routing Foundation

- Bangkok/BK routing support was added as the current Thailand-market focus.
- Bangkok workbooks use Google geocoding by default and route through Bangkok
  OSRM when available.
- Bangkok timing currently uses a conservative static traffic multiplier until
  richer provider sampling is implemented.

### Bangkok Google Geocode Relay

- A narrow Bangkok-only Google geocode relay supports CN-hosted Thailand tests
  when direct Google access is unavailable.
- The relay requires a bearer token, enforces daily/monthly caps, and rejects
  non-Bangkok requests by default.

## 2026-06-13

### Time Impact Decision Review

- Time Impact now includes conclusion-oriented review cards for optimized
  scenarios: Acceptable, Review needed, High risk, or Incomplete.
- The review explains route and stop impacts without changing solver behavior.

### Input Address Review Warnings

- Accepted input addresses can be flagged for human review when they appear
  outside the expected service area, unusually far from the school, or
  suspicious in route sequence context.
- Warnings are informational only: they do not block solving, delete cache
  entries, auto-correct coordinates, or treat normal cross-district travel as
  an error.
- Existing completed jobs are immutable; rerun an audit to generate new review
  warnings.

### KR Weekday Traffic Profiles

- KR traffic sampling uses Kakao Navi future directions against stable baseline
  JSON exports.
- To School profiles are anchored to arrival time; From School profiles are
  anchored to departure time.
- Weekly KR refreshes cover Monday-Friday AM peak, PM peak, and off-peak
  profiles with independent Kakao usage caps.

## 2026-06-12

### Route Audit Result Workspace

- Route Audit results were reorganized around Summary, Plans, Impact, and
  Review tabs.
- Visible scenarios focus on Current Plan, Free Optimization Baseline, and the
  15-Minute Constrained plan.
- Map actions group Open map, Download map, and Download workbook in the
  route-map workspace.

### Time Impact Workflow

- Route Audit results include Time Impact analysis for compatible completed
  jobs.
- Baseline workbook exports include original and optimized pickup/drop-off time
  columns when timing data is available.

## 2026-06-11

### Legacy Streamlit Client Removed

- The user-facing app is the React frontend. The old Streamlit client is no
  longer part of the supported workflow.

### Interactive Route Map Rollout

- Route maps moved from static/legacy HTML toward the shared React interactive
  MapLibre component.
- The map supports route selection, route list expansion, stop inspection,
  fullscreen display, and standalone interactive HTML download.

### Korean Localization

- Core KR-facing UI labels, fixed readouts, dashboard text, and generated AI
  report display paths gained Korean localization coverage.

## 2026-06-09

### Side Tool History And Maps

- Side-tool history rails were unified with Route Audit history behavior.
- Fleet Planner map outputs moved toward the same interactive map component
  used by Route Audit.

### Aggregated Stop Batching

- Aggregated pickup demand can be split into vehicle-feasible batches instead
  of forcing one overloaded vehicle.
- Comfort preference does not reduce real vehicle capacity; it remains a
  review/labeling concept.

## 2026-06-08

### China Geocoding Hardening

- China geocoding gained distance and locality checks to catch obvious wrong
  city/province/country resolutions before route solving.
- Bad geocode cache entries can be removed and regenerated when the workbook
  address context proves the cached coordinate was wrong.

### Frontend Version Marker

- React builds should stamp the visible app version from the target Git commit
  so staging/prod deployments can be verified quickly.

## 2026-06-04

### Side Tools And Runtime Paths

- Distance & Cost and Fleet Planner gained persistent history workflows.
- Runtime history, caches, generated outputs, and server-local env files are
  preserved outside normal Git sync.
- Fleet Planner gained generated workbook naming improvements, vehicle profile
  preview, and route time target handling.

## 2026-06-02

### AI Audit Report Readability

- AI Audit report presentation was improved for clearer operator review.
- Users are prompted more clearly when a report can be generated from a
  completed job.

### SSO Preparation

- Microsoft SSO setup remains pending external cyber/security review before
  domain and SSO cutover work continues.

## 2026-06-01

### Production Frontend Architecture

- CN and KR public frontends standardized on Nginx-backed React serving.
- Public OSRM hostnames were retired in favor of internal/proxied routing
  paths appropriate to each environment.
- Environment workflow was reset around CN staging as the build/test source and
  CN/KR production as release targets.

### Operational Guards

- React origin access, planner job concurrency, and production promotion
  practices were hardened to reduce deployment drift.

## 2026-05-30

### React Product Cutover

- React preview expanded to Route Audit, Distance & Cost, and Fleet Planner.
- KR public frontend switched to React.
- Backend history persistence, usage counters, AI Audit model configuration,
  route assumptions, and live traffic timer behavior were hardened during the
  cutover.

## Update Log Guidance

- Keep this file concise.
- Add entries only for material user-facing behavior, architecture,
  provider/runtime operations, deployment workflow, or important rerun guidance.
- Do not append daily implementation detail, command transcripts, or private
  host facts here.
