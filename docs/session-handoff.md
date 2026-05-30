# Session Handoff

This is the short recovery note for a new Codex session. It should stay concise.
Put stable architecture in `docs/architecture.md`, operating steps in
`docs/development-release-workflow.md`, fresh-server setup in
`docs/deployment-overview.md`, and major user/operator updates in
`docs/updates.md`.

## Current Product Shape

- Product name: `BRP: Bus Route Planner`.
- Main workflow: `Route Audit`.
- Side tools:
  - `Distance & Cost`
  - `Fleet Planner`
- React in `apps/web` is the long-term frontend and is already the KR public
  frontend. Streamlit in `apps/client` remains useful as a legacy/operator and
  rapid validation client.
- The backend in `apps/backend` is the stable compute and job API layer.

## Current React Surface

Routes:

- `/`: dashboard
- `/new`: new Route Audit job
- `/jobs`: audit history workspace
- `/jobs/$jobId`: job detail
- `/distance`: Distance & Cost
- `/fleet`: Fleet Planner

Job result tab order:

1. `AI Audit`
2. `Audit Detail`
3. `Actions`
4. `Baselines`
5. `Maps`
6. `Diagnostics`

Recent UX state:

- New Audit optional assumption panels only count as custom when fields are
  actually edited. Opening a panel does not override workbook/default values.
- `Run audit` validates the workbook automatically before submission.
- Job detail shows job name, job seed, submitter, and status near the top.
- Job History no longer takes over the whole screen on narrower windows.
- Sign out lives in the sidebar user panel.
- The KR-only Google usage pill is shown in the React header when
  `BRP_SHOW_GOOGLE_GEOCODE_USAGE=true`.
- Real server addresses, usernames, private hostnames, and machine paths should
  stay out of committed docs. Use ignored local file
  `docs/private/ops-inventory.local.md` for those details.

## Runtime And Data Rules

Never reset or delete runtime data unless the user explicitly asks.

Critical paths:

- job store: `state/jobs` or `BRP_BACKEND_JOBS_DIR`
- client cache: `apps/client/cache`
- backend cache: `apps/backend/cache`
- generated outputs
- server-local env: `ops/env/local.env`
- Google usage: `apps/client/cache/google_geocode_usage.json`
- provider rate-limit state: `state/api_rate_limits` or `BRP_API_RATE_LIMIT_DIR`

Google usage semantics:

- The value `134` was only the verified KR count when the counter was restored on
  2026-05-30. It is not a target value.
- Future valid Google calls should increase the counter naturally.
- Cache hits do not increment usage.
- Actual Google requests reserve usage before sending, using a cross-process
  lock, so concurrent workers do not lose increments or overshoot the monthly
  cap.

External provider QPS:

- Kakao, Google, AMap, and DeepSeek use cross-process provider limiters.
- Jobs can still run in parallel; only outbound provider request gates are
  paced.
- OSRM is BRP-managed infrastructure and is not handled by the external provider
  limiter. Future OSRM protection should use job concurrency limits or service
  capacity controls.

## Server Facts

### KR

- South Korea Windows deployment.
- Real private-network address, Windows user, active checkout, public hostname,
  and preview origin belong in `docs/private/ops-inventory.local.md`.
- Backend service uses local port `8001`.
- Public React static/proxy service uses local port `8501`.
- Private React preview uses local port `4173`.
- Persistent scheduled tasks:
  - `BRP-Backend-Preview`
  - `BRP-React-Preview`
  - `BRP-React-Public`
- KR does not currently have Node/npm in PATH. Build React locally and copy
  `apps/web/dist` to KR when frontend assets change.
- KR runtime migration is complete. The old checkout was removed after verifying
  jobs, cache, outputs, env backups, and old logs were migrated or archived.

Last verified KR runtime state in this session:

- backend health: ok
- public React proxy health: ok
- private React preview health: ok
- job files: `5`
- client cache files: `6`
- backend cache files: `6`
- Google usage at verification time: `134 / 10,000`

### CN

- Domestic deployment.
- Real SSH host/user belong in `docs/private/ops-inventory.local.md`.
- OS: Ubuntu 22.04 LTS
- This is the CN server. Do not confuse it with the KR private-network host.
- SSH was inaccessible from the current network; likely IP/network access policy.
- Monday/next office-network task: install or configure private-network access on the CN
  server if access permits.

## Deployment Habit

For ordinary code changes:

1. Validate locally.
2. Commit and push `main`.
3. For KR, pull in the active checkout recorded in the private inventory.
4. If React assets changed, build locally with `npm run build` in `apps/web` and
   copy `apps/web/dist` to KR.
5. Restart KR scheduled tasks.
6. Verify health, `/new`, `/jobs`, job count, cache counts, and Google usage
   continuity.

Docs-only changes do not need service restart.

## Known Gaps / Next Work

- CN private-network access remains pending.
- Domestic final React cutover is still separate from the KR React cutover.
- `BRP_BACKEND_SERVICE_TOKEN` is intentionally empty on the current KR
  same-origin proxy deployment; public security relies on Cloudflare Access. A
  hardened version should inject a backend token server-side or keep API hosts
  fully behind Access.
- OSRM stability should be handled separately from external provider QPS.
- Continue validating the React Route Audit, Distance & Cost, and Fleet Planner
  flows against real workbooks before broader user rollout.

## Documentation Rule

After meaningful implementation:

- update this handoff only for current session recovery facts
- update `docs/architecture.md` for stable design or module boundary changes
- update `docs/development-release-workflow.md` for run/deploy/check changes
- update `docs/deployment-overview.md` for fresh-environment requirements
- ask whether `docs/updates.md` should record major user/operator-facing changes
