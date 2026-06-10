# Codex Startup Card

This repository is BRP / Bus Route Planner. Read this file first in every new
Codex session, then read the private handoff.

## Source Of Truth

- The Windows local machine is only a Codex/control surface.
- CN staging is the active development and test checkout.
- CN production and KR production are release targets only.
- Current progress, next tasks, environment status, host-specific commands,
  release ledger entries, and operational recovery notes live in the private
  handoff, not in public repository docs.
- In this checkout, the private handoff is expected at
  `docs/private/ops-inventory.local.md` when available. The OneDrive private
  backup is the canonical portable copy.

## First Reads

1. Read this file.
2. Read the private handoff/inventory.
3. Read `docs/development-release-workflow.md` for stable workflow rules.
4. Read `docs/deployment-overview.md` only for environment setup or service
   maintenance.
5. Read `docs/updates.md` only when a user/operator-facing change needs a
   release note.

## Working Rules

- Do not recreate a local or OneDrive BRP project checkout for normal
  development.
- When taking over work, inspect CN staging first: Git revision, tracked status,
  service health, runtime behavior, and private handoff.
- Work in CN staging, validate there, commit/push the intended revision, and
  promote only when the user explicitly approves.
- For frontend changes, build React from CN staging and reuse that verified
  artifact for production targets.
- Preserve runtime data and server-local env files unless the user explicitly
  asks for a cleanup.

## Repository Hygiene

Public docs must not contain real credentials, private hostnames, concrete
access paths, tunnel tokens, operational passwords, or machine-specific recovery
facts. Put those in the private handoff only.
