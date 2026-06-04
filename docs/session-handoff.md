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
- Job History no longer takes over the whole screen on narrower viewports.
- Sign out lives in the sidebar user panel.
- React reads `/api/auth/config` so auth button targets and the displayed auth
  provider can be driven by server-local environment. The default provider keeps
  current header-based access behavior.
- AI Audit reports use a tighter briefing-style prompt as of 2026-06-02. Old
  stored reports keep their content, but the React viewer strips common Markdown
  noise and regenerated reports use the improved style.
- Fleet Planner History now collapses into a narrow left-side rail by default,
  preserving the original right-side Fleet Planner workspace layout while
  giving narrower desktop windows more room.
- Distance & Cost now has separate React histories for Reference Distance and
  Route Cost. Successful runs auto-save to the matching history, can be
  reopened, and can be deleted from `/distance`.
- Distance & Cost history tab switching was hardened on 2026-06-04. Opening a
  Route Cost history run and then switching to Reference Distance no longer
  renders incompatible saved-result payloads; the fix is on GitHub `main` at
  `81febde` and has been deployed to CN staging only.
- Fleet Planner now exposes a `Route Time Target` in React run settings. The
  value is saved in Fleet Planner History, restored when a history run is
  opened, and passed to backend fleet preview, clustering diagnostics, grouped
  route preview, and global OR-Tools planning.
- Fleet Planner has a staging React vehicle profile editor in Run settings.
  It can adjust vehicle name, seats, category, energy type, available count,
  enabled state, and notes for a single run. Custom vehicle snapshots are
  passed to fleet preview, clustering diagnostics, grouped route preview, and
  global OR-Tools planning, then saved with Fleet Planner History.
- The KR-only Google usage pill is shown in the React header when
  `BRP_SHOW_GOOGLE_GEOCODE_USAGE=true`.
- Real server addresses, usernames, private hostnames, and environment-specific paths should
  stay out of committed docs. Use ignored local file
  `docs/private/ops-inventory.local.md` for those details.
- If that local private file is missing in a new environment, restore it from the
  private inventory backup outside Git before changing server access or tunnel
  settings.
- Operating model as of 2026-06-01: local checkouts are code-record and testing
  workspaces only. Use them to test the staging host recorded in the private
  inventory and keep Git visibility. CN staging is the active dev/test
  environment. CN production and KR production are release targets only and
  should not change during staging work.
- Operating shorthand as of 2026-06-04: when the user says `开发` or `拉齐`
  without naming a different target, treat the target as CN staging. Do not
  start local services for ordinary alignment work.

## Runtime And Data Rules

Never reset or delete runtime data unless the user explicitly asks.

Critical paths:

- job store: `state/jobs` or `BRP_BACKEND_JOBS_DIR`
- side tool history store: `state/side_tools` or `BRP_SIDE_TOOLS_DIR`
- client cache: `apps/client/cache` or `BRP_CLIENT_CACHE_DIR`
- backend cache: `apps/backend/cache` or `BRP_BACKEND_CACHE_DIR`
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

- South Korea deployment.
- Real access details, active checkout, public hostname, and preview origin
  belong in `docs/private/ops-inventory.local.md`.
- Backend service uses local port `8001`.
- Public React origin uses local Nginx on port `8501`.
- Private React preview uses local port `4173`.
- Persistent scheduled tasks:
  - `BRP-Backend-Preview`
  - `BRP-React-Preview`
  - `BRP-Nginx-Public`
  - `BRP-React-Public` is disabled after the Nginx public-origin cutover.
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
- This is the CN server. Do not confuse it with the KR host.
- CN hosts both staging and domestic production, but development changes should
  happen only in staging unless the user explicitly asks for a production
  promotion.
- Confirmed code checkouts:
  - staging: `/opt/brp/staging/app`
  - production: `/opt/brp/prod/app`
- Confirmed runtime job roots:
  - staging: `/opt/brp/staging/data/jobs`
  - production: `/opt/brp/prod/data/jobs`
- Current public environment boundary:
  - `$CN_STAGING_HOST` -> CN staging frontend `127.0.0.1:8501`
  - `$CN_PROD_HOST` -> CN production frontend `127.0.0.1:8500`
  - `$KR_PROD_HOST` -> KR production frontend on the KR host
- CN staging and production share `BRP_JOB_CONCURRENCY_DIR` with
  `BRP_MAX_CONCURRENT_JOBS=1` so heavy planner jobs queue host-wide instead of
  running concurrently beside the shared OSRM stack.
- CN staging and production should share runtime data roots for job history,
  side-tool history, client cache, backend cache, and provider rate-limit state
  through `BRP_BACKEND_JOBS_DIR`, `BRP_SIDE_TOOLS_DIR`,
  `BRP_CLIENT_CACHE_DIR`, `BRP_BACKEND_CACHE_DIR`, and
  `BRP_API_RATE_LIMIT_DIR`. Keep code checkouts, ports, services, and env files
  separate.
- CN staging and CN production are synced to GitHub `main` as of the Fleet
  Planner History rail release on 2026-06-03. Backup branches were left before
  realigning old server checkouts after the public-history rewrite.
- CN staging checkout should be kept synced to the current GitHub `main` during
  release work. As of the Distance & Cost history tab-switch fix on 2026-06-04,
  CN staging is at `81febde`.
- CN staging and CN production frontends serve React through Nginx. Nginx serves
  `apps/web/dist`, performs SPA fallback, and proxies same-origin `/api/*`.
  Staging proxies to `127.0.0.1:8001`; production proxies to
  `127.0.0.1:8000`.
- CN Nginx returns 401 when a request reaches the origin without the Cloudflare
  Access user header.
- CN staging and CN production backends have `BRP_BACKEND_SERVICE_TOKEN` set.
  The Nginx installer reads `ops/env/local.env` and writes a root-only include
  that injects the backend token server-side. Do not expose the token to the
  browser.
- `brp-staging-frontend.service` and `brp-prod-frontend.service` are disabled on
  CN after the Nginx cutover. Use `nginx.service` as the frontend service on CN.
- Cloudflared ingress on CN maps `$CN_STAGING_HOST` to the React staging
  frontend on `127.0.0.1:8501` and `$CN_PROD_HOST` to the CN production
  frontend on `127.0.0.1:8500`. `$LEGACY_DOMESTIC_CLIENT_HOST` is no longer in
  the ingress config and falls through to 404. `$CN_STAGING_HOST` is the current
  staging hostname; unauthenticated requests redirect to the Access login flow.
- Public OSRM tunnel routes were retired on 2026-06-01. CN staging/production
  and KR production call local OSRM endpoints directly. Local diagnostics should
  use the private inventory's diagnostic loopback mapping instead of public OSRM
  hostnames.
- The old direct domestic legacy client hostname was disabled at DNS level on
  2026-06-01 because it exposed Streamlit without access control. The protected
  domestic app hostname remains available behind Cloudflare Access.

## Deployment Habit

For ordinary code changes:

1. Confirm operator access to the target environment.
2. Work in the CN staging checkout.
3. Validate against CN staging services and `$CN_STAGING_HOST`.
4. Commit and push the intended Git revision.
5. Promote to CN production only when the user explicitly asks by pulling the
   intended revision into `/opt/brp/prod/app` and restarting production services.
6. Sync KR production to the same release unless the user explicitly excludes
   KR; KR may require a local React build copied to
   `apps/web/dist` because Node/npm is not in its PATH.
7. Verify health, `/new`, `/jobs`, job count, cache counts, and Google usage
   continuity for every environment touched.

Docs-only changes do not need service restart.

CN deployment notes from 2026-06-03:

- If a server checkout predates a public-history rewrite, `git pull --ff-only`
  can fail even when tracked files are clean. Confirm there are no tracked local
  changes, leave a local backup branch, then realign the checkout to
  `origin/main`. Preserve untracked env backup files.
- CN staging and production did not have `npm` available during this deploy.
  Build React from a machine that has Node, copy `apps/web/dist` to the target
  checkout, ensure the copied `dist` tree is readable/executable by Nginx, and
  reload `nginx.service`.
- For CN Nginx origin checks, no Cloudflare Access user header should return
  `401`; the same route with an Access user header should return `200`. Also
  verify same-origin `/api/health`.

Local checkout role:

- Use local checkouts as code-record and light-test workspaces only.
- Do not make local runtime state the source of truth.
- Keep Git commits and pushes as the source-of-truth record even when coding on
  CN.
- Never let development overwrite runtime data or local/server env files.
- On the Windows local checkout, prefer Git Bash at
  `C:\Program Files\Git\bin\bash.exe` for complex local `ssh`, `scp`, and Git
  command composition. Keep PowerShell for Windows-specific npm/Python helper
  commands. For remote Windows service work, prefer encoded PowerShell or
  uploaded scripts over deeply nested one-line quoting.

## Known Gaps / Next Work

- Validate the authenticated `$CN_STAGING_HOST` React session in a browser,
  including `/new`, `/jobs`, `/distance`, and `/fleet`.
- Decide the next explicit release target before touching KR production.
  Staging work must not repoint or restart production hostnames.
- `BRP_BACKEND_SERVICE_TOKEN` is intentionally empty on the current KR
  same-origin proxy deployment; public security relies on Cloudflare Access.
  CN staging and production now inject backend tokens through Nginx includes.
- OSRM stability should be handled separately from external provider QPS.
- Continue validating the React Route Audit, Distance & Cost, and Fleet Planner
  flows against real workbooks before broader user rollout.
- The latest Distance & Cost history split and tab-switch fix has not been
  promoted to production in this session. Promote CN production and KR
  production together only after explicit production approval.
- CN staging is the only environment currently intended for Microsoft SSO
  preparation. Keep production deployments on their current auth provider until
  the user explicitly approves an auth migration.

## Next Session Without Private Inventory

If this repository is pulled in a fresh environment and
`docs/private/ops-inventory.local.md` is not present, the next Codex session
should first restore the private inventory from the backup outside Git. If that
private copy is missing or unavailable, the committed context still has enough
information to know the work plan. It should ask the user only for the missing
private connection facts:

- CN SSH host and user, or confirmation that approved access is available
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
   - `apps/client/cache` or `BRP_CLIENT_CACHE_DIR`
   - `apps/backend/cache` or `BRP_BACKEND_CACHE_DIR`
   - generated outputs
   - OSRM data directory
4. Back up runtime data before replacing code or changing deploy layout.
5. Commit and push changes from CN staging.
6. Promote the intended Git revision to `/opt/brp/prod/app` only after explicit
   production approval.
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
