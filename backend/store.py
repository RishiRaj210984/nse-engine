"""File-based state. No database.

All learning state is plain JSON in STATE_DIR (journal of every signal and its
outcome, model versions, bias log). In production the GitHub Actions job
commits these files back to your repo after each run, so the state lives in
GitHub with full history — nothing on premises. The app reads the published
snapshots in PUBLIC_DIR.

The price/news cache is in-process memory only.
"""
import json
import os
import threading
import time
from datetime import datetime

from config import DEFAULT_PARAMS, IST, PUBLIC_DIR, STATE_DIR

_cache = {}
_lock = threading.Lock()


def now_ist():
    return datetime.now(IST)


# ---------- in-memory TTL cache ----------
def cache_get(key):
    hit = _cache.get(key)
    return hit[1] if hit and hit[0] > time.time() else None


def cache_set(key, value, ttl):
    _cache[key] = (time.time() + ttl, value)


# ---------- JSON files ----------
def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path, obj, indent=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=indent, default=str, separators=None if indent else (",", ":"))
        os.replace(tmp, path)


def state_path(name):
    return os.path.join(STATE_DIR, name)


def publish(name, obj):
    _write(os.path.join(PUBLIC_DIR, name), obj, indent=1)


# ---------- model versions ----------
def _states():
    states = _read(state_path("model_state.json"), [])
    if not states:
        states = [{"version": 1, "created_at": now_ist().isoformat(), "params": DEFAULT_PARAMS,
                   "weights": None, "metrics": {}, "note": "Initial rule set (your spec)"}]
        _write(state_path("model_state.json"), states, indent=1)
    return states


def latest_state():
    s = dict(_states()[-1])
    s["params"] = {**DEFAULT_PARAMS, **s["params"]}
    return s


def baseline_state():
    """Latest state saved before today: tuning steps from here, so same-day re-runs can't compound."""
    states = _states()
    today = now_ist().date().isoformat()
    for s in reversed(states):
        if s["created_at"][:10] < today:
            return s
    return states[0]


def model_history(n=20):
    return [{k: v for k, v in s.items() if k != "weights"} for s in _states()[-n:]][::-1]


def save_state(params, weights, metrics, note):
    states = _states()
    version = states[-1]["version"] + 1
    states.append({"version": version, "created_at": now_ist().isoformat(), "params": params,
                   "weights": weights, "metrics": metrics, "note": note})
    _write(state_path("model_state.json"), states[-300:], indent=1)
    return version


# ---------- journal & bias log ----------
def load_journal():
    return _read(state_path("journal.json"), [])


def save_journal(rows):
    _write(state_path("journal.json"), rows)


def load_bias_log():
    return _read(state_path("bias_log.json"), {})


def save_bias_log(log):
    _write(state_path("bias_log.json"), log, indent=1)


def init():
    _states()
