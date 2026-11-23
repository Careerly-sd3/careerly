"""Runs AI work in the background, so no page waits for Gemini.

Each job has a key, such as ("cv", user_id). A key runs at most once at a time.
Pages ask status(key) to show progress. After a failure the key pauses for a
while, so pages that reload themselves don't call Gemini again and again.

Statuses live in this process's memory: they reset when the app restarts, and
each worker process keeps its own. The results themselves are saved in the
database by the jobs.
"""
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time

from flask import current_app

logger = logging.getLogger(__name__)

MAX_PARALLEL_JOBS = 2
FAILURE_PAUSE_SECONDS = 120
UNEXPECTED_FAILURE = "Something went wrong. We'll try again in a few minutes."

_executor = ThreadPoolExecutor(max_workers=MAX_PARALLEL_JOBS, thread_name_prefix="ai-job")
_lock = threading.Lock()
_running = set()
_failed = {}  # key -> (message, time it failed)
_done = {}    # key -> message to show once


def is_running(key):
    with _lock:
        return key in _running


def start(key, work, *, expected_errors=(), force=False):
    """Run work() in the background unless the key is running or paused; return True if started.

    work() may return a message to show once when it finishes. Errors listed in
    expected_errors carry a user-facing `message` and are shown as the failure.
    force=True skips the pause after a failure (the user asked for this job directly).
    """
    with _lock:
        failure = _failed.get(key)
        if not force and failure and time.monotonic() - failure[1] < FAILURE_PAUSE_SECONDS:
            return False
        if key in _running:
            return False
        _running.add(key)
        _failed.pop(key, None)
        _done.pop(key, None)
    try:
        _executor.submit(_run, current_app._get_current_object(), key, work, expected_errors)
    except Exception:  # e.g. the worker pool is shutting down; never leave the key "running"
        logger.exception("Could not start AI job %s", key[0])
        _finish(key, failure=UNEXPECTED_FAILURE)
        return False
    return True


def _run(app, key, work, expected_errors):
    try:
        with app.app_context():
            message = work()
    except expected_errors as error:  # each carries a message that is safe to show
        _finish(key, failure=getattr(error, "message", None) or str(error) or UNEXPECTED_FAILURE)
    except Exception:
        logger.exception("AI job %s failed", key[0])
        _finish(key, failure=UNEXPECTED_FAILURE)
    else:
        _finish(key, done=message)


def _finish(key, *, failure=None, done=None):
    with _lock:
        _running.discard(key)
        if failure:
            _failed[key] = (failure, time.monotonic())
        elif done:
            _done[key] = done


def status(key):
    """{"state": "running"}, {"state": "failed", "message"}, {"state": "done", "message"} (shown once), or None."""
    with _lock:
        if key in _running:
            return {"state": "running"}
        failure = _failed.get(key)
        if failure and time.monotonic() - failure[1] < FAILURE_PAUSE_SECONDS:
            return {"state": "failed", "message": failure[0]}
        done = _done.pop(key, None)
    return {"state": "done", "message": done} if done else None
