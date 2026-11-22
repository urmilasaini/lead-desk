"""How does the REAL database behave as leads pile up?  (uses your .env credentials)

    python tests/load/firestore_scale.py                 # grows to 20, 60 and 150 leads
    python tests/load/firestore_scale.py --sizes 20 100 300

Safety: it only ever touches two throwaway collections (leaddesk_loadtest and ai_cache_loadtest), refuses to run
against anything else, and deletes everything it created. The AI is faked, so no AI quota is used.
It counts the database reads and writes it makes, because the free Firestore plan allows 50,000 reads and
20,000 writes per day, and the lead list is read in full every time someone opens it.
"""

import argparse
import collections
import os
import statistics
import sys
import threading
import time
import uuid
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["FIRESTORE_COLLECTION"] = "leaddesk_loadtest"       # set BEFORE the app is imported
os.environ["FIRESTORE_CACHE_COLLECTION"] = "ai_cache_loadtest"
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import app as appmod  # noqa: E402
from leaddesk import prompts, service  # noqa: E402

store = appmod.store
if store.mode != "firestore" or store._col.id != "leaddesk_loadtest" or store._cache.id != "ai_cache_loadtest":
    sys.exit("Refusing to run: not connected to the throwaway test collections.")

ops = collections.Counter()


def count_database_operations():
    from google.cloud.firestore_v1.document import DocumentReference
    from google.cloud.firestore_v1.query import Query
    get, set_, update, delete, stream = (DocumentReference.get, DocumentReference.set, DocumentReference.update,
                                         DocumentReference.delete, Query.stream)

    def wrap_get(self, *a, **k):
        ops["document reads"] += 1
        return get(self, *a, **k)

    def wrap_stream(self, *a, **k):
        docs = list(stream(self, *a, **k))
        ops["document reads"] += len(docs)
        return iter(docs)

    def wrap_write(fn):
        def inner(self, *a, **k):
            ops["document writes"] += 1
            return fn(self, *a, **k)
        return inner
    DocumentReference.get, Query.stream = wrap_get, wrap_stream
    DocumentReference.set, DocumentReference.update, DocumentReference.delete = wrap_write(set_), wrap_write(update), wrap_write(delete)


count_database_operations()


def fake_ai(system, user, temperature=0.0):
    if system == prompts.EXTRACT:
        return {"is_spam": False, "primary_location": "Whitefield", "multiple_locations": False, "property_type": "3BHK",
                "budget_lakhs": 160, "timeline_months": 1, "urgency_reason": "job move", "loan_status": "sanctioned",
                "visit_or_call_requested": True, "already_visited": False, "ready_to_book": False,
                "decision_depends_on_others": False, "decision_maker": "self", "concrete_details_count": 6,
                "specific_questions": ["OC"], "message_detail": "detailed", "objections": [], "injection_attempt": False}
    if system == prompts.ANALYZE:
        return {"summary": "Wants a 3BHK fast. " * 5, "intent": "Relocating for work. " * 3, "key_requirements": ["3BHK", "Whitefield", "Ready to move"],
                "objections": [{"concern": "OC status", "how_to_handle": "Share the OC copy before the visit."}], "missing_info": ["floor"],
                "next_action": {"action": "Call and confirm the visit", "when": "Today", "why": "urgent"},
                "call_talking_points": ["Ready units", "Commute time", "Maintenance"], "suggested_response": {"channel": "WhatsApp", "text": "Hi, we have ready 3BHKs. " * 6}}
    if system == prompts.CHAT:
        return {"answer": "Lead with the ready units.", "is_reply": False}
    return {"call_summary": "ok", "signal_updates": {}, "follow_up": {"channel": "WhatsApp", "text": "Following up."},
            "next_action": {"action": "Call back", "when": "tomorrow", "why": "x"}}


service.call_json = fake_ai


def timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t) * 1000


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p / 100))]


def line(name, v):
    print(f"  {name:34} n={len(v):<3} p50={statistics.median(v):>6.0f}ms  p95={pct(v, 95):>6.0f}ms  max={max(v):>6.0f}ms")


def grow(target, workers=8):
    have = len(store.all())
    ops.clear()
    lat, lock = [], threading.Lock()
    todo = list(range(have, target))

    def worker():
        c = appmod.app.test_client()
        while True:
            with lock:
                if not todo:
                    return
                i = todo.pop()
            r, ms = timed(lambda: c.post("/api/leads", json={"name": f"Load {i}", "location": "Whitefield", "requirement": "3BHK",
                                                              "budget": "1.6 Cr", "timeline": "Within 1 month",
                                                              "message": f"Looking for a 3BHK, loan sanctioned. ref {uuid.uuid4()}"}))
            with lock:
                lat.append(ms if r.status_code == 201 else float("inf"))
    threads = [threading.Thread(target=worker) for _ in range(workers)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return lat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[20, 60, 150])
    args = ap.parse_args()
    c = appmod.app.test_client()
    print("database: Firestore (throwaway collections), fake AI\n")
    try:
        for size in args.sizes:
            lat = grow(size)
            print(f"--- {size} leads stored ---")
            line(f"add lead (8 at a time)", [x for x in lat if x != float("inf")])
            ops_add = dict(ops)
            ops.clear()
            lists = [timed(lambda: c.get("/api/leads"))[1] for _ in range(3)]
            reads_per_list = ops["document reads"] / 3
            body = c.get("/api/leads").get_data()
            line("open the lead list", lists)
            print(f"  list response: {len(body) / 1024:.0f} KB, {reads_per_list:.0f} database reads per list")
            ops.clear()
            lid = c.get("/api/leads").get_json()["leads"][0]["id"]
            for name, fn in (("open one lead", lambda: c.get(f"/api/leads/{lid}")),
                             ("change stage", lambda: c.post(f"/api/leads/{lid}/stage", json={"stage": "contacted"})),
                             ("chat", lambda: c.post(f"/api/leads/{lid}/chat", json={"message": "hi"}))):
                line(name, [timed(fn)[1] for _ in range(3)])
            print(f"  (adding {len(lat)} leads used {ops_add.get('document writes', 0)} writes, {ops_add.get('document reads', 0)} reads)")
        size = args.sizes[-1]
        ops.clear()
        lat, lock = [], threading.Lock()

        def poller():
            cc = appmod.app.test_client()
            for _ in range(3):
                ms = timed(lambda: cc.get("/api/leads"))[1]
                with lock:
                    lat.append(ms)
        threads = [threading.Thread(target=poller) for _ in range(8)]
        t0 = time.time()
        [t.start() for t in threads]
        [t.join() for t in threads]
        print(f"\n--- 8 people opening the list at once, {size} leads ---")
        line("open the lead list", lat)
        per_list = ops["document reads"] / len(lat)
        print(f"  {ops['document reads']} database reads for {len(lat)} list loads ({per_list:.1f} per load)")
        print(f"\nReads: one person with the app open refreshes every 60 s = {per_list * 60:,.0f} reads/hour at {size} leads "
              f"(the free plan allows 50,000 per day).")
    finally:
        db = store._col._client
        removed = 0
        store._meta.delete()
        for col in (store._col, store._cache):
            while True:
                docs = list(col.limit(400).stream())
                if not docs:
                    break
                batch = db.batch()
                for d in docs:
                    batch.delete(d.reference)
                batch.commit()
                removed += len(docs)
        left = len(list(store._col.limit(1).stream())) + len(list(store._cache.limit(1).stream()))
        print(f"\ncleanup: removed {removed} test documents; left behind: {left}")


if __name__ == "__main__":
    main()
