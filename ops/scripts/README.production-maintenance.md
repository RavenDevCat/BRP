# BRP Production Maintenance Scripts

These scripts are the standard production alignment entry points.

## Rules

- Build React only on CN staging after the final commit.
- CN prod and KR prod receive the CN-staging-built `apps/web/dist`.
- Do not run `npm install`, `npm run build`, or ad-hoc frontend builds on CN prod or KR.
- KR backend is managed only through the `BRP Backend` Scheduled Task.
- Do not start KR backend with `Start-Process` or old `state\start-*.cmd` wrappers.

## CN prod

Run on the CN server after CN staging has the final commit and verified dist:

```bash
/opt/brp/staging/app/ops/scripts/align_cn_prod_from_staging.sh <target-head>
```

The script fast-forwards `/opt/brp/prod/app`, switches prod to the staging-built
dist, restarts `brp-prod-backend.service`, and checks backend health.

## KR prod

First create a tarball from CN staging dist and copy it to the KR repo state
directory through the local control machine:

```bash
cd /opt/brp/staging/app/apps/web
tar -czf /tmp/brp-web-dist-<target-head>.tgz dist
```

Upload the archive to:

```text
C:\Users\Bus.EIM\BRP\state\brp-web-dist-<target-head>.tgz
```

Then run on KR:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\Bus.EIM\BRP\ops\scripts\align_kr_prod_from_dist.ps1 -TargetHead <target-head>
```

The script fast-forwards the KR repo, switches to the supplied dist archive,
restarts `BRP Backend`, starts/checks `BRP-Nginx-Public`, and checks backend
health.
