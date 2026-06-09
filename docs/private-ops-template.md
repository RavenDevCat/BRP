# Private Operations Inventory Template

Copy this file to `docs/private/ops-inventory.local.md` only inside an active
server checkout that needs private handoff facts. The `docs/private/`
directory is ignored by Git. Do not create a separate Windows project checkout
just to host this file.

Use this for server addresses, usernames, private hostnames, environment-specific
paths, and other handoff details that should not be shared with the repository.

## Server Aliases

### KR

- host:
- user:
- checkout:
- backend origin:
- public frontend origin:
- public Nginx config:
- public Nginx startup:
- private preview origin:
- supervisor/tasks:

### CN

- host:
- user:
- checkout:
- backend origin:
- frontend origin:
- Nginx config:
- cloudflared config:
- OSRM local ports:
- supervisor/services:

## Runtime Data To Preserve

- job store:
- client cache:
- backend cache:
- generated outputs:
- local env:
- Google usage:
- provider rate-limit state:

## Production Service Maintenance

### CN

- staging app root:
- production app root:
- staging backend service:
- production backend service:
- Nginx service:
- Cloudflare service:
- React build host:
- frontend dist promotion path:
- required smoke checks:

### KR

- active checkout:
- backend scheduled task/service:
- backend start wrapper:
- public Nginx task/service:
- private preview task/service:
- frontend dist source:
- required smoke checks:
- known anti-patterns:
  - do not use CN as a KR jump host unless explicitly approved
  - do not build React on KR unless Node/npm was deliberately provisioned
  - do not launch a one-off backend over SSH and call it persistent production

## Access Notes

- SSH/access-control notes:
- Monday/next access task:
- Known blockers:

## Public Repository Guardrails

- placeholder names to use in public docs:
  - `$CN_STAGING_HOST`
  - `$CN_PROD_HOST`
  - `$KR_PROD_HOST`
  - `$LEGACY_DOMESTIC_CLIENT_HOST`
  - `$CN_SSH_HOST`
  - `$CN_SSH_USER`
  - `$KR_PRIVATE_HOST`
  - `$KR_USER`
  - `$KR_APP_ROOT`
- private denylist file:
  - local checkout: `docs/private/public-denylist.local.txt`
  - private backup: `BRP Private/public-denylist.local.txt`
- before commits touching docs, README files, env examples, ops scripts,
  Cloudflare examples, or handoff notes, run the private denylist scan described
  in the local inventory against tracked public files and reachable Git history
