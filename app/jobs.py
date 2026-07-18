"""In-process background job runner for slow ops (render, verify).

A slow op (~5-6 min on qwen3.7-max for render/verify) would block a request
thread; instead the endpoint submits the op here and returns a job_id the
client polls via GET /api/jobs/{id}. Single process, daemon threads; fine for
the v1 single-user localhost deployment. (v2: a real queue.)
"""
import threading
import uuid
import datetime

_jobs: dict = {}
_lock = threading.Lock()


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def submit(kind, fn, *args, **kwargs):
    """Run fn(*args, **kwargs) on a daemon thread. Returns a job_id immediately."""
    jid = uuid.uuid4().hex
    with _lock:
        _jobs[jid] = {
            "status": "pending", "kind": kind, "result": None, "error": None,
            "started_at": None, "finished_at": None,
        }

    def _run():
        with _lock:
            _jobs[jid]["status"] = "running"
            _jobs[jid]["started_at"] = _now()
        try:
            r = fn(*args, **kwargs)
            with _lock:
                _jobs[jid]["status"] = "done"
                _jobs[jid]["result"] = r
                _jobs[jid]["finished_at"] = _now()
        except Exception as e:  # noqa: BLE001 — capture, don't crash the thread
            with _lock:
                _jobs[jid]["status"] = "failed"
                _jobs[jid]["error"] = str(e)[:500]
                _jobs[jid]["finished_at"] = _now()

    threading.Thread(target=_run, daemon=True).start()
    return jid


def status(jid):
    with _lock:
        j = _jobs.get(jid)
        return dict(j) if j else {"status": "unknown"}
