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
- If that local private file is missing on a new machine, look in OneDrive at
  `OneDrive-EiM/BRP Private/ops-inventory.local.md` and copy it back to
  `docs/private/ops-inventory.local.md`.
- Operating model as of 2026-06-01: CN is the primary place for development,
  staging validation, and production deployment. The local Windows checkout and
  the Mac checkout are code-record and connection workspaces only, mainly for
  Git visibility, SSH/Remote SSH access, and emergency/light local checks. KR is
  a separate production deployment, not the main development line.

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
- planner concurrency state: `BRP_JOB_CONCURRENCY_DIR`

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
  limiter. OSRM protection should use job concurrency limits or service capacity
  controls.

## Server Facts

### KR

- South Korea Windows deployment.
- Real operator access address, Windows user, active checkout, public hostname,
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
- This is the CN server. Do not confuse it with the KR operator access host.
- Primary development, staging, and production work should happen here.
- Confirmed code checkouts:
  - staging: `/opt/brp/staging/app`
  - production: `/opt/brp/prod/app`
- Confirmed runtime job roots:
  - staging: `/opt/brp/staging/data/jobs`
  - production: `/opt/brp/prod/data/jobs`
- CN staging and production share `BRP_JOB_CONCURRENCY_DIR` with
  `BRP_MAX_CONCURRENT_JOBS=1` so heavy planner jobs queue host-wide instead of
  running concurrently beside the shared OSRM stack.
- CN staging is synced to GitHub `main` after the planner concurrency change.
  CN production is still on the older production branch; the concurrency backend
  files were backported there directly instead of resetting production to the
  newer React/staging line.
- CN staging checkout should be kept synced to the current GitHub `main` during
  release work.
- CN staging frontend service now serves React static/proxy from local
  `127.0.0.1:8501`, using `ops/scripts/serve_react_static.py` and
  `apps/web/dist`; `/api/*` proxies to `127.0.0.1:8001`.
- Public React static/proxy hostnames should set
  `BRP_REQUIRE_CLOUDFLARE_ACCESS=true` so the origin returns 401 if a request
  reaches it without the Cloudflare Access user header.
- CN staging backend has `BRP_BACKEND_SERVICE_TOKEN` set, so the React
  static/proxy service loads `ops/env/local.env` through systemd
  `EnvironmentFile` and injects the backend token server-side. Do not expose the
  token to the browser.
- Cloudflared ingress on CN maps `staging.example.com` and
  `$CN_PROD_HOST` to the React staging frontend on `127.0.0.1:8501`.
  `$LEGACY_DOMESTIC_CLIENT_HOST` is no longer in the ingress config and falls through to
  404. As of this handoff, `staging.example.com` still needs a Cloudflare DNS
  record/tunnel route created outside CN because CN has no origin cert/API token
  for `cloudflared tunnel route dns`.
- The old direct domestic legacy client hostname was disabled at DNS level on
  2026-06-01 because it exposed Streamlit without access control. The protected
  domestic app hostname remains available behind Cloudflare Access.

## Deployment Habit

For ordinary code changes:

1. Work in the CN staging checkout.
2. Validate against CN staging services and runtime data.
3. Commit and push the Git revision from CN.
4. Promote to CN production by pulling the intended revision into
   `/opt/brp/prod/app` and restarting production services.
5. Sync KR intentionally when that separate production deployment should receive
   the same revision; KR may require a local React build copied to
   `apps/web/dist` because Node/npm is not in its PATH.
6. Verify health, `/new`, `/jobs`, job count, cache counts, and Google usage
   continuity for every environment touched.

Docs-only changes do not need service restart.

Local/Mac role:

- Use the Windows and Mac checkouts as code-record, SSH/Remote SSH, and emergency
  light-test workspaces only.
- Do not make local or Mac runtime state the source of truth.
- Keep Git commits and pushes as the source-of-truth record even when coding on
  CN.
- Never let development overwrite runtime data or local/server env files.

## Known Gaps / Next Work

- Domestic final React cutover is still separate from the KR React cutover.
- Add the Cloudflare DNS/tunnel route for `staging.example.com` so the CN
  staging React frontend is reachable publicly.
- `BRP_BACKEND_SERVICE_TOKEN` is intentionally empty on the current KR
  same-origin proxy deployment; public security relies on Cloudflare Access.
  CN staging now supports token injection in the React static/proxy service.
- OSRM stability should be handled separately from external provider QPS.
- Continue validating the React Route Audit, Distance & Cost, and Fleet Planner
  flows against real workbooks before broader user rollout.

## Next Session Without Private Inventory

If this repository is pulled on another development machine and
`docs/private/ops-inventory.local.md` is not present, the next Codex session
should first look for the shared private copy at
`OneDrive-EiM/BRP Private/ops-inventory.local.md`. If that OneDrive file is also
missing or unavailable, the committed context still has enough information to
know the work plan. It should ask the user only for the missing private
connection facts:

- CN SSH host and user, or confirmation that operator access access is available
- CN active checkout path only if it differs from `/opt/brp/staging/app` or
  `/opt/brp/prod/app`
- CN runtime data paths, if they differ from the deployment docs
- public/private hostnames only when configuring tunnels or smoke tests

CN development and promotion sequence:

1. Connect to the CN Ubuntu 22.04 LTS server.
2. Work from staging checkout `/opt/brp/staging/app`.
3. Before changing runtime behavior, confirm the relevant runtime paths:
   - code checkout
   - `ops/env/local.env`
   - `state/jobs` or `BRP_BACKEND_JOBS_DIR`
   - `apps/client/cache`
   - `apps/backend/cache`
   - generated outputs
   - OSRM data directory
4. Back up runtime data before replacing code or changing deploy layout.
5. Commit and push changes from CN staging.
6. Promote the intended Git revision to `/opt/brp/prod/app`.
7. Restart only the services for the environment being changed.
8. Validate backend health, OSRM health, `/new`, `/jobs`, history visibility,
   cache continuity, AI Audit, Distance & Cost, and Google usage behavior if
   enabled for that deployment.

Do not invent missing private addresses from memory. If the private inventory is
absent, ask the user for the minimum connection facts and then proceed.

## Documentation Rule

After meaningful implementation:

- update this handoff only for current session recovery facts
- update `docs/architecture.md` for stable design or module boundary changes
- update `docs/development-release-workflow.md` for run/deploy/check changes
- update `docs/deployment-overview.md` for fresh-environment requirements
- ask whether `docs/updates.md` should record major user/operator-facing changes
