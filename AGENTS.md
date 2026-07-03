# Codex Startup Card

This repository is BRP / Bus Route Planner. Read this file first in every new
Codex session, then ask the operator for the current handoff.

## Source Of Truth

- The Windows local machine is only a Codex/control surface.
- CN staging is the active development and test checkout.
- CN production and KR production are release targets only.
- Current progress, next tasks, environment status, host-specific commands,
  release ledger entries, and operational recovery notes are not stored in this
  repository.
- Do not create server-side copies of current operational handoff material for
  convenience.

## First Reads

1. Read this file.
2. Ask the operator for the current handoff outside this repository.
3. Read `docs/development-release-workflow.md` for stable workflow rules.
4. Read `docs/deployment-overview.md` only for environment setup or service
   maintenance.
5. Read `docs/updates.md` only when a user/operator-facing change needs a
   release note.

## Working Rules

- Do not recreate a local synced BRP project checkout for normal development.
- When taking over work, inspect CN staging first: Git revision, tracked status,
  service health, runtime behavior, and operator-provided current handoff.
- Work in CN staging, validate there, commit/push the intended revision, and
  promote only when the user explicitly approves.
- For frontend changes, build React from CN staging and reuse that verified
  artifact for production targets.
- When inspecting Route Audit jobs, use the API or read-only runtime SQLite
  (`BRP_RUNTIME_DB_PATH`). Do not search legacy `state/jobs/*.json` files by
  job id; those files are migration/archive material only.
- Preserve runtime data and server-local env files unless the user explicitly
  asks for a cleanup.
- KR traffic profile refresh is not the CN AMap live timer. It uses Kakao Navi
  future weekday samples through the checked-in KR traffic profile wrappers,
  and it keeps a separate Kakao Navi usage counter that must be preserved with
  runtime state. Do not switch this back to Google Routes for Seoul driving
  profiles; production diagnostics returned empty routes for KR/Seoul.

## Repository Hygiene

Public docs must not contain real credentials, hostnames, concrete access paths,
tunnel tokens, operational passwords, machine-specific recovery facts, or
current handoff details.
