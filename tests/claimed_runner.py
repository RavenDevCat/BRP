"""Run one synthetic queued task through the real persistence and token boundary."""
def claimed_store(monkeypatch, tmp_path, record, snapshots=None):
    import backend_job_runner as runner
    path = tmp_path / "claimed.sqlite"
    monkeypatch.setenv("BRP_RUNTIME_DB_PATH", str(path))
    store = runner._runtime_store()
    store.upsert_job(record)
    jid = record["job_id"]
    monkeypatch.setenv("BRP_JOB_RUN_TOKEN", store.claim_queued_job(jid)["worker_token"])
    if snapshots is not None:
        def observe(method):
            def write(*args, **kwargs):
                result = method(*args, **kwargs)
                snapshots.append(store.get_job(jid))
                return result
            return write
        for name in ("change_existing_job", "checkpoint_parts"):
            monkeypatch.setattr(store, name, observe(getattr(store, name)))
    return store
