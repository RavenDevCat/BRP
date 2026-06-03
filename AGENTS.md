# Codex Startup Card

This repository is BRP / Bus Route Planner. Read this file first in every new
Codex session.

## First Reads

1. Read `docs/session-handoff.md` for the concise current project context.
2. Read `docs/development-release-workflow.md` before running local services or deploying.
3. Read `docs/deployment-overview.md` before fresh-server or environment setup.
4. Read `docs/updates.md` before deciding whether a user-facing change needs a release note.
5. If present, read ignored local file `docs/private/ops-inventory.local.md`
   for private server addresses and environment-specific handoff facts.
6. If that local private inventory is missing, restore it from the approved
   private backup outside Git before changing server access or tunnel settings.

## Server Names

- KR server: South Korea deployment. Real address/user/path belong in the
  ignored private inventory.
- CN server: domestic deployment. Real address/user/path belong in the ignored private inventory.
- Do not confuse the KR host with the CN server.

## Development Source

- Primary development and staging validation happen on CN staging.
- CN production and KR production are release targets only. Do not change them
  during staging work unless the user explicitly asks for a production release.
- Local checkouts are code-record and light-check workspaces only; they are not
  the main runtime source.
- KR is maintained as a separate production deployment that follows the Git
  revision intentionally; do not treat KR as the main development line.

## Runtime Data Safety

Never overwrite, reset, or delete runtime data unless the user explicitly asks:

- `state/jobs`
- `apps/client/cache`
- `apps/backend/cache`
- `state/side_tools`
- generated output folders
- `ops/env/local.env`
- `apps/client/cache/google_geocode_usage.json`
- `state/api_rate_limits`

When deploying, verify that job history, caches, and env-specific behavior survived the deploy.

The Google geocode usage counter is persistent runtime state. The value `134` was only the known KR count when the counter was restored on 2026-05-30. Future valid Google calls should increase the count. Preserve and verify the current value; do not reset it to `134`.
Google usage updates are protected with a cross-process lock and atomic reservation in `apps/client/client_runtime.py`; do not bypass this by writing the usage JSON directly.
External provider QPS is protected with a cross-process rate limiter under `state/api_rate_limits` by default. Do not replace it with per-process-only throttling when touching Kakao, Google, AMap, or DeepSeek API calls.

## Public Repository Hygiene

Private deployment values belong only in ignored private files or approved
private backups outside Git. Public docs and examples must use placeholders such
as `$CN_STAGING_HOST`, `$CN_PROD_HOST`, and `$KR_PROD_HOST`.
Do not include connectivity details, operator network assumptions, or connection
failure modes in public docs. Keep public deployment instructions to role,
order, validation, and rollback behavior.

Before committing or pushing changes that touch docs, README files, environment
examples, ops scripts, Cloudflare examples, or handoff notes:

- read the `Public Repository Guardrails` section in
  `docs/private/ops-inventory.local.md` when the private inventory is available
- run the private denylist scan from that section
- treat any hit in committed files, reachable Git history, or commit messages as
  a blocker until it is replaced with a placeholder

## KR Deploy Pattern

- If frontend assets changed, build React locally from `apps/web` with `npm run build`.
- KR currently does not have Node/npm in PATH, so copy local `apps/web/dist` to KR after KR pulls the code when frontend assets changed.
- Restart the KR scheduled tasks:
  - `BRP-Backend-Preview`
  - `BRP-React-Preview`
  - `BRP-React-Public`
- Verify:
  - backend health
  - public React proxy health
  - private React preview health
  - `/new` and `/jobs`
  - job count
  - client/backend cache file counts
  - Google usage endpoint when KR has `BRP_SHOW_GOOGLE_GEOCODE_USAGE=true`

## CN Deploy Pattern

- Work and validate on CN staging first; promote CN production only after the
  user explicitly approves.
- Confirm operator access to the target environment before deployment, but keep
  connection details out of public handoff notes.
- If a server checkout cannot fast-forward after a public-history rewrite,
  confirm there are no tracked local changes, create a local backup branch, then
  realign the checkout to `origin/main`. Preserve untracked env backup files.
- If CN does not have Node/npm available, build React from a machine that does
  and copy `apps/web/dist` into the CN checkout. After copying, make the `dist`
  tree readable/executable by Nginx and reload `nginx.service`.
- Verify each touched CN environment with backend health, frontend origin
  behavior (`401` without Access header and `200` with one), same-origin
  `/api/health`, current Git revision, and runtime data counts where relevant.

## Product Naming

- Main product: `Bus Route Planner`.
- Side tool: `Distance & Cost`.
- Result tab order: `AI Audit`, `Audit Detail`, `Actions`, `Baselines`, `Maps`, `Diagnostics`.

## Session Habit

After meaningful implementation rounds, update `docs/session-handoff.md` only for facts needed by the next Codex session. Also ask whether `docs/updates.md` should receive a user-facing update when the change affects features, providers, routing/geocoding/planner behavior, or whether users should rerun jobs.
