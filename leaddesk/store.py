"""Lead storage: Firestore. There is no silent in-memory fallback.

Credentials, first match wins (all are the Firebase service-account JSON):
  FIREBASE_CREDENTIALS_BASE64   the JSON, base64-encoded (easiest to paste into Vercel)
  FIREBASE_CREDENTIALS          the JSON as text
  FIREBASE_SERVICE_ACCOUNT      the JSON as text (older name)
  firebase-service-account.json file in the project root (git-ignored)

If no usable credentials are found the app still starts, but saving is disabled and every data call
answers with a plain "can't save or load leads right now" message. Memory storage exists only when you
ask for it with STORAGE=memory (used by tests).

Failures talking to the database are logged with details and surfaced to users as a friendly
StorageError, never as raw exceptions.
"""

import base64
import copy
import functools
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("leaddesk.store")

ROOT = Path(__file__).resolve().parent.parent
CRED_FILE = ROOT / "firebase-service-account.json"
# its own collection, so it never mixes with documents other tools may have put in "leads"
COLLECTION = os.environ.get("FIRESTORE_COLLECTION", "leaddesk_leads")
CACHE_COLLECTION = os.environ.get("FIRESTORE_CACHE_COLLECTION", "ai_cache")
CACHE_TTL = timedelta(days=7)
META_COLLECTION = os.environ.get("FIRESTORE_META_COLLECTION", COLLECTION + "_meta")
LIST_CACHE_MAX_AGE_S = 60   # safety net: re-read the whole list at least this often
USER_MESSAGE = "We can't save or load leads right now. Please try again in a moment."


class LeadGone(Exception):
    """The lead no longer exists (for example someone deleted it while an action was waiting on the AI)."""


class Append(list):
    """Marks a field whose items are ADDED to the stored list instead of replacing it. Two people appending at
    the same moment (two chat messages, two call logs) then both keep their entry."""


class StorageError(Exception):
    """str(e) is safe to show to users; .detail is for logs only."""

    def __init__(self, detail=""):
        self.detail = detail
        super().__init__(USER_MESSAGE)


def _guarded(method):
    """Turn any database failure into a logged StorageError."""
    @functools.wraps(method)
    def inner(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except (StorageError, LeadGone):
            raise
        except Exception as e:
            log.error("Storage error in %s: %s: %s", method.__name__, type(e).__name__, str(e)[:300])
            raise StorageError(type(e).__name__) from e
    return inner


# ---------------- credentials ----------------
def load_credentials(environ=None, cred_file=None):
    """Return the service-account dict, or None. Never raises, never logs secret values."""
    env = os.environ if environ is None else environ
    cred_file = CRED_FILE if cred_file is None else cred_file
    sources = []
    if env.get("FIREBASE_CREDENTIALS_BASE64"):
        sources.append(("FIREBASE_CREDENTIALS_BASE64", lambda: json.loads(base64.b64decode(env["FIREBASE_CREDENTIALS_BASE64"]).decode("utf-8"))))
    for name in ("FIREBASE_CREDENTIALS", "FIREBASE_SERVICE_ACCOUNT"):
        if env.get(name):
            sources.append((name, lambda name=name: json.loads(env[name])))
    if Path(cred_file).exists():
        sources.append((Path(cred_file).name, lambda: json.loads(Path(cred_file).read_text(encoding="utf-8"))))
    for label, read in sources:
        try:
            info = read()
        except Exception as e:
            log.error("Firebase credentials in %s could not be read (%s)", label, type(e).__name__)
            continue
        if isinstance(info, dict) and info.get("private_key") and info.get("client_email") and info.get("project_id"):
            info["private_key"] = info["private_key"].replace("\\n", "\n")  # tolerate escaped newlines
            return info
        log.error("Firebase credentials in %s are missing required fields", label)
    return None


# ---------------- stores ----------------
class FirestoreStore:
    mode = "firestore"
    ready = True

    def __init__(self, info):
        import firebase_admin
        from firebase_admin import credentials, firestore
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(info))
        db = firestore.client()
        self._col = db.collection(COLLECTION)
        self._cache = db.collection(CACHE_COLLECTION)
        self._meta = db.collection(META_COLLECTION).document("leads_version")
        self._listing = None   # (version, fetched_at, docs)
        self._reload_lock = threading.Lock()

    # The lead list is read in full whenever someone opens it, and every open screen refreshes it. Reading N
    # documents each time makes cost and latency grow with the number of leads. So every write changes a small
    # "version" document, and the list is only re-read when that version changed (otherwise: 1 read, served from memory).
    def _version(self):
        d = self._meta.get()
        return (d.to_dict() or {}).get("v") if d.exists else None

    def _bump(self):
        self._listing = None
        try:
            self._meta.set({"v": uuid.uuid4().hex})
        except Exception as e:  # the write itself succeeded; the max-age safety net covers a missed bump
            log.warning("could not update the list version: %s", type(e).__name__)

    @_guarded
    def all(self):
        try:
            version = self._version()
            if version is None:      # first run, or data written before versioning existed
                self._bump()
                version = self._version()
        except Exception as e:
            log.warning("list version unavailable, reading the full list: %s", type(e).__name__)
            version = None
        hit = self._cached_list(version)
        if hit is not None:
            return hit
        with self._reload_lock:     # when many requests miss at once, one reads and the rest reuse its result
            hit = self._cached_list(version)
            if hit is not None:
                return hit
            docs = [d.to_dict() for d in self._col.stream()]    # version was read first, so this is at least as new
            if version is not None:
                self._listing = (version, time.time(), copy.deepcopy(docs))
            return docs

    def _cached_list(self, version):
        cached = self._listing
        if version is not None and cached and cached[0] == version and time.time() - cached[1] < LIST_CACHE_MAX_AGE_S:
            return copy.deepcopy(cached[2])
        return None

    @_guarded
    def get(self, lead_id):
        d = self._col.document(lead_id).get()
        return d.to_dict() if d.exists else None

    @_guarded
    def save(self, lead):
        self._col.document(lead["id"]).set(lead)
        self._bump()
        return lead

    @_guarded
    def delete(self, lead_id):
        self._col.document(lead_id).delete()
        self._bump()

    @_guarded
    def update(self, lead_id, fields):
        """Write only these fields (dotted paths allowed). Everything else in the document is left as it is."""
        from google.api_core.exceptions import NotFound
        from google.cloud.firestore_v1 import ArrayUnion
        payload = {k: (ArrayUnion(list(v)) if isinstance(v, Append) else v) for k, v in fields.items()}
        try:
            self._col.document(lead_id).update(payload)
        except NotFound:
            raise LeadGone() from None
        self._bump()

    @_guarded
    def ping(self):
        list(self._col.limit(1).stream())
        return True

    # AI response cache. Failures here are the caller's to swallow: a cache must never break a request.
    def cache_get(self, key):
        d = self._cache.document(key).get()
        if not d.exists:
            return None
        data = d.to_dict()
        if datetime.fromisoformat(data["at"]) < datetime.now(timezone.utc) - CACHE_TTL:
            return None
        return data["value"]

    def cache_set(self, key, value):
        self._cache.document(key).set({"value": value, "at": datetime.now(timezone.utc).isoformat()})


class MemoryStore:
    """Only for tests and local experiments (STORAGE=memory). Lost on restart."""
    mode = "memory"
    ready = True

    def __init__(self):
        self._data, self._cache, self._lock = {}, {}, threading.Lock()

    def all(self):
        with self._lock:
            return [json.loads(json.dumps(v)) for v in self._data.values()]

    def get(self, lead_id):
        with self._lock:
            v = self._data.get(lead_id)
            return json.loads(json.dumps(v)) if v else None

    def save(self, lead):
        with self._lock:
            self._data[lead["id"]] = json.loads(json.dumps(lead))
        return lead

    def delete(self, lead_id):
        with self._lock:
            self._data.pop(lead_id, None)

    def update(self, lead_id, fields):
        with self._lock:
            doc = self._data.get(lead_id)
            if doc is None:
                raise LeadGone()
            for key, value in fields.items():
                if isinstance(value, Append):
                    doc[key] = doc.get(key, []) + json.loads(json.dumps(list(value)))
                    continue
                parts = key.split(".")
                node = doc
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                node[parts[-1]] = json.loads(json.dumps(value))

    def ping(self):
        return True

    def cache_get(self, key):
        with self._lock:
            entry = self._cache.get(key)
        if not entry or datetime.fromisoformat(entry[1]) < datetime.now(timezone.utc) - CACHE_TTL:
            return None
        return entry[0]

    def cache_set(self, key, value):
        with self._lock:
            self._cache[key] = (value, datetime.now(timezone.utc).isoformat())


class UnconfiguredStore:
    """Used when there are no usable credentials. Every call fails with the same friendly message."""
    mode = "unconfigured"
    ready = False

    def _fail(self, *args, **kwargs):
        raise StorageError("no storage configured")

    all = get = save = delete = update = ping = cache_get = cache_set = _fail


def make_store():
    if os.environ.get("STORAGE", "").lower() == "memory":
        return MemoryStore()
    info = load_credentials()
    if info:
        try:
            return FirestoreStore(info)
        except Exception as e:
            log.error("Could not start Firestore (%s): %s", type(e).__name__, str(e)[:200])
    else:
        log.error("No Firebase credentials found: saving is disabled. Set FIREBASE_CREDENTIALS_BASE64 "
                  "(or STORAGE=memory for local experiments).")
    return UnconfiguredStore()
