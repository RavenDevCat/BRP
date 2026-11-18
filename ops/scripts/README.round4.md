# Fourth-round rollout

Defaults preserve legacy JSON persistence, ordinary checkpoints and eager exports.
Never change cache mode while another process still writes the same cache. Stage
one setting at a time; these changes do not require Google or map keys.

- `BRP_KEYED_CACHE=1`: geocoding, final traffic, planner results and display
  geometry import existing JSON once into adjacent per-key SQLite stores.
  Newer committed revisions win conflicts against stale snapshots. Explicit
  clears invalidate old snapshots. Back up the sidecars as well as the legacy
  files. To roll back: stop all cache writers, export each sidecar with
  `ops/scripts/export_keyed_cache.py LEGACY_PATH NEW_JSON`, compare counts,
  preserve both originals, install the export, then disable the flag. If the
  legacy files have since changed, archive the old sidecars before re-enabling.
- `BRP_DELTA_CHECKPOINTS=1`: Direct-to-School workers persist changed stops,
  routes, errors and progress keyed by job. Running detail and history use the
  same persisted evidence. Cancellation/finalization consolidate in the normal
  record; deletion cascades. Drain running workers before reverting to software
  that cannot read checkpoint parts. No historical rewrite or AM/PM input change.
- `BRP_DEFER_FLEET_EXPORTS=1`: enables lazy Fleet workbooks in both saved and
  new results. Deploy the matching UI first; legacy clients can use the eager
  mode. No export action calls the map providers.
- Use the existing CN alignment script after building staging cleanly. It
  validates source and destination checksums, probes `/health` and `/ready`,
  and switches the backend and web artifacts back on failed checks. After
  rollback the backend checkout is detached at the previous commit.
  `/ready` only reads job and dispatch schema. A success is not an actual
  future scheduled run.
- Retention is bounded for *managed, accepted* release directories when
  `BRP_WEB_PRUNE_RELEASES=1` (default off); `BRP_WEB_RELEASE_KEEP` defaults
  to 3, minimum 2. Preview with `verified_web_release.py prune WEB_ROOT`.
  Active, pinned, legacy and unaccepted releases are left alone. Old backup
  directories created by previous deploy tools are not deleted automatically.

Browser acceptance uses the built frontend with entirely local fixtures. Run
`tests/browser/round4_fleet_fixture.py OUTPUT.json`, set `BRP_QA_FLEET_FIXTURE`
to that file and `BRP_QA_WEB_DIST` to the build output. Run
`node tests/browser/round4-history.cjs` on a QA host with Playwright and Edge.
This does not delete any real history. The browser channel is configurable
with `BRP_QA_BROWSER_CHANNEL`.
