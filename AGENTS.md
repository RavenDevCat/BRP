# Codex Startup Card

This repository is BRP / Bus Route Planner. Read this file first in every new
Codex session.

Default working model: CN staging is the development and test checkout. The
Windows local machine is only a communication/control surface; do not use a
separate local project checkout for BRP development.

## First Reads

1. Read `docs/session-handoff.md` for the concise current project context.
2. Read `docs/development-release-workflow.md` before running local services or deploying.
3. Read `docs/deployment-overview.md` before fresh-server or environment setup.
4. Read `docs/updates.md` before deciding whether a user-facing change needs a release note.
5. If present in the active server checkout, read ignored private file
   `docs/private/ops-inventory.local.md` for private server addresses and
   environment-specific handoff facts.
6. If that private inventory is missing, restore it from the approved private
   backup outside Git before changing server access or tunnel settings. Do
   not create a separate Windows project checkout just to host private docs.

## Current Focus Snapshot - 2026-06-09

- Interactive Route Audit map polish is included through `bd6379e`; later
  docs-only commits may advance `main` beyond that product revision.
- The Maps tab now has a React MapLibre interactive map with legacy HTML maps
  retained as fallback.
- Current interactive map capabilities include scenario summary tiles, route
  search/filtering, natural route label ordering, selected-route focus,
  route-context toggle, selected stop sequence, stop hover/click details,
  selected-route direction arrows, route status badges, and softened context
  stops.
- Next product polish candidates are route display naming, selected route card
  placement/collapse behavior, demand batch labels in stop sequence, map base
  style/contrast, wider route hit areas, scenario delta metrics, upper-right
  map popup/control content, interactive map download/export behavior, and
  narrow viewport layout.
- Treat those candidates as backlog, not emergency fixes. Keep implementing on
  CN staging first.

## Server Names

- KR server: South Korea deployment. Real address/user/path belong in the
  ignored private inventory.
- CN server: domestic deployment. Real address/user/path belong in the ignored private inventory.
- Do not confuse the KR host with the CN server.

## Development Source

- Primary development and staging validation happen on CN staging.
- When the user says `开发` or `拉齐` without naming another target, treat the
  target as CN staging.
- CN production and KR production are release targets only. Do not change them
  during staging work unless the user explicitly asks for a production release.
- Local checkouts are code-record and light-check workspaces only; they are not
  the main runtime source.
- When the user asks to "take over", "catch up", or check current progress,
  inspect the CN staging checkout and staging runtime first. Use local and
  GitHub state as supporting records, not as the source of truth.
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

- If frontend assets changed, build React from the CN staging checkout and
  verify the resulting `apps/web/dist` there.
- KR currently does not have Node/npm in PATH, so copy the CN-staging-built
  `apps/web/dist` to KR after KR pulls the code when frontend assets changed.
- Restart the KR scheduled tasks:
  - `BRP-Backend-Preview`
  - `BRP-Nginx-Public`
  - `BRP-React-Preview`
  - `BRP-React-Public` remains unused after the public Nginx cutover
- If `BRP-Backend-Preview` returns `Ready` immediately and backend port `8001`
  is not listening, reset the task action to explicitly run `cmd.exe /c` on the
  backend start wrapper, then start the task again. A backend process launched
  directly from an SSH command may pass health while the command is open but is
  not reliable as the persistent production process.
- Verify:
  - backend health
  - public React proxy health; the Nginx origin should return `401` without an
    Access user header and `200` with one
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
- CN staging is the default React build host. For frontend changes, commit the
  intended revision first, then run `npm run lint` and `npm run build` in
  `apps/web` on CN staging so the sidebar version marker matches the release.
- Copy or sync that verified `apps/web/dist` artifact to production targets
  when promoting frontend changes. After copying, ensure the `dist` tree is
  readable/executable by Nginx and reload `nginx.service` where required.
- Verify each touched CN environment with backend health, frontend origin
  behavior (`401` without Access header and `200` with one), same-origin
  `/api/health`, current Git revision, visible sidebar version when frontend
  assets changed, and runtime data counts where relevant.

## Product Naming

- Main product: `Bus Route Planner`.
- Side tool: `Distance & Cost`.
- Result tab order: `AI Audit`, `Audit Detail`, `Actions`, `Baselines`, `Maps`, `Diagnostics`.

## Session Habit

After meaningful implementation rounds, update `docs/session-handoff.md` only for facts needed by the next Codex session. Also ask whether `docs/updates.md` should receive a user-facing update when the change affects features, providers, routing/geocoding/planner behavior, or whether users should rerun jobs.
