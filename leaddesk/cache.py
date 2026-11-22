"""Cache for AI responses.

The same lead text always produces the same extraction prompt, so re-importing a file, re-adding a lead
or retrying after a crash can reuse a previous real AI answer instead of spending quota and seconds.
It is a cache of genuine AI output for identical input, never a stand-in for one.

- Key: hash of (model, temperature, system prompt, user text). Change any of them and it's a miss.
- Two levels: this process (fast), then the database (shared across serverless instances, 7 days).
- Failures are never cached, "Re-analyze" bypasses the lookup and refreshes the entry, and any cache
  problem is swallowed: a cache must never break a request.
"""

import hashlib
import json
import logging
import threading
import time

log = logging.getLogger("leaddesk.cache")

MEMORY_TTL_S = 24 * 3600
MEMORY_MAX = 512
_mem = {}          # key -> (expires_at, json string)
_lock = threading.Lock()
stats = {"hits": 0, "misses": 0}


def make_key(namespace, system, user, temperature):
    raw = json.dumps([namespace, temperature, system, user], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def clear():
    with _lock:
        _mem.clear()
        stats.update(hits=0, misses=0)


def _remember_local(key, value):
    with _lock:
        _mem[key] = (time.time() + MEMORY_TTL_S, value)
        while len(_mem) > MEMORY_MAX:
            _mem.pop(next(iter(_mem)))


def _lookup(store, key):
    with _lock:
        entry = _mem.get(key)
    if entry and entry[0] > time.time():
        return entry[1]
    try:
        value = store.cache_get(key)
    except Exception as e:
        log.warning("cache read failed: %s", type(e).__name__)
        return None
    if value is not None:
        _remember_local(key, value)
    return value


def cached(store, namespace, system, user, temperature, call, refresh=False):
    """Return the AI answer for this input: from the cache if present (unless refresh), else call() and remember it."""
    key = make_key(namespace, system, user, temperature)
    if not refresh:
        hit = _lookup(store, key)
        if hit is not None:
            try:
                out = json.loads(hit)
                stats["hits"] += 1
                return out
            except ValueError:
                pass  # corrupt entry: fall through to a fresh call
    stats["misses"] += 1
    out = call()
    value = json.dumps(out, ensure_ascii=False)
    _remember_local(key, value)
    try:
        store.cache_set(key, value)
    except Exception as e:
        log.warning("cache write failed: %s", type(e).__name__)
    return out
