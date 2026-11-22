"""Load test for the HTTP layer: many salespeople using the app at once.

    python tests/load/load_test.py                          # starts its own server (fake AI, in-memory storage)
    python tests/load/load_test.py --users 10 25 50 --seconds 20 --ai-latency 1.5
    python tests/load/load_test.py --url http://localhost:5000   # an already running server (it will create leads there)

The AI is faked, but it sleeps like the real one (default 1.5 s per call), so this measures OUR code: routes,
scoring, caching and the storage layer under concurrent requests, not the AI provider. See firestore_scale.py
for how the real database behaves as leads accumulate.

Each simulated user: opens the page, loads the lead list, then keeps working with a short think time between
actions: add a lead (2 AI calls), open a lead, chat (1 AI call), log a call (1 AI call), change stage, mark replied.
"""

import argparse
import collections
import json
import os
import random
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
MESSAGES = ["Looking for a 3BHK in Whitefield, budget 1.6 Cr, loan sanctioned, want to visit this Saturday.",
            "2BHK near Wipro Sarjapur Road, max 95L, lease ends in March, 20L down payment ready.",
            "Interested in plots near Devanahalli airport, is it A-Khata? Clear title?",
            "price?", "Need ready to move 3BHK, relocating for job, joining on the 15th."]


def request(method, url, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            return r.status, (time.perf_counter() - t) * 1000, payload
    except urllib.error.HTTPError as e:
        return e.code, (time.perf_counter() - t) * 1000, e.read()
    except Exception:
        return 0, (time.perf_counter() - t) * 1000, b""


def session(base, deadline, results, lock, seed):
    rnd = random.Random(seed)
    ids = []

    def call(label, method, path, body=None):
        status, ms, payload = request(method, base + path, body)
        with lock:
            results.append((label, ms, 200 <= status < 300, status))
        return status, payload

    call("page", "GET", "/")
    call("asset", "GET", "/static/app.js")
    call("health", "GET", "/api/health")
    while time.time() < deadline:
        status, payload = call("list leads", "GET", "/api/leads")
        if status == 200:   # like the real screen, only act on leads that have finished analysing
            ids = [l["id"] for l in json.loads(payload)["leads"] if l["ai_status"] == "ready"][:200]
        r = rnd.random()
        if r < 0.25 or not ids:
            unique = f"{rnd.choice(MESSAGES)} (ref {seed}-{rnd.random():.6f})"   # unique, so the AI cache doesn't hide the cost
            call("add lead (2 AI calls)", "POST", "/api/leads", {"name": f"User {seed}", "location": "Whitefield", "requirement": "3BHK",
                                                                 "budget": "1.5 Cr", "timeline": "Within 1 month", "message": unique})
        else:
            lid = rnd.choice(ids)
            if r < 0.50:
                call("open lead", "GET", f"/api/leads/{lid}")
            elif r < 0.70:
                call("chat (1 AI call)", "POST", f"/api/leads/{lid}/chat", {"message": "What should I emphasize on the call?"})
            elif r < 0.80:
                call("log call (1 AI call)", "POST", f"/api/leads/{lid}/calls", {"outcome": "connected", "notes": "spoke briefly"})
            elif r < 0.92:
                call("change stage", "POST", f"/api/leads/{lid}/stage", {"stage": rnd.choice(["contacted", "site_visit", "negotiation"])})
            else:
                call("mark replied", "POST", f"/api/leads/{lid}/responded")
        time.sleep(rnd.uniform(0.3, 1.2))   # think time


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p / 100))]


def run_level(base, users, seconds):
    results, lock = [], threading.Lock()
    deadline = time.time() + seconds
    t0 = time.time()
    threads = [threading.Thread(target=session, args=(base, deadline, results, lock, i)) for i in range(users)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - t0
    errors = sum(1 for _, _, ok, _ in results if not ok)
    print(f"\n=== {users} concurrent users, {seconds}s: {len(results)} requests, {len(results) / wall:.1f} req/s, "
          f"{errors} errors ({errors / max(1, len(results)) * 100:.2f}%) ===")
    print(f"{'endpoint':26} {'n':>5} {'p50':>8} {'p95':>8} {'p99':>8} {'max':>8}   (ms)")
    by = {}
    for label, ms, ok, _ in results:
        by.setdefault(label, []).append(ms)
    for label in sorted(by):
        v = by[label]
        print(f"{label:26} {len(v):>5} {statistics.median(v):>8.0f} {pct(v, 95):>8.0f} {pct(v, 99):>8.0f} {max(v):>8.0f}")
    if errors:
        failed = {}
        for label, ms, ok, status in results:
            if not ok:
                failed.setdefault((label, status), []).append(ms)
        for (label, status), v in sorted(failed.items()):
            print(f"  FAILED {len(v)}x  {label}  status={status or 'no response'}  after ~{statistics.median(v):.0f} ms")
    return errors, by, collections.Counter(label for label, ms, ok, _ in results if ok)


def verify(base, totals):
    """Did every successful request leave its mark? Lost updates would show up as missing leads, chats or calls."""
    status, _, payload = request("GET", base + "/api/leads")
    leads = json.loads(payload)["leads"]
    issues = []
    adds = totals["add lead (2 AI calls)"]
    if len(leads) != adds:
        issues.append(f"{len(leads)} leads stored, but {adds} adds succeeded")
    stuck = sum(1 for l in leads if l["ai_status"] != "ready")
    if stuck:
        issues.append(f"{stuck} leads never finished analysing")
    chats, calls = sum(len(l["chat"]) for l in leads), sum(len(l["calls"]) for l in leads)
    if chats != 2 * totals["chat (1 AI call)"]:
        issues.append(f"{chats} chat messages stored, expected {2 * totals['chat (1 AI call)']}")
    if calls != totals["log call (1 AI call)"]:
        issues.append(f"{calls} call logs stored, expected {totals['log call (1 AI call)']}")
    print("consistency:", "OK (every lead, chat message and call log that was acknowledged is stored)" if not issues else "PROBLEMS " + "; ".join(issues))
    return len(issues)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, nargs="+", default=[10, 25, 50])
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--ai-latency", type=float, default=1.5)
    ap.add_argument("--port", type=int, default=5061)
    ap.add_argument("--url")
    args = ap.parse_args()

    server = None
    base = args.url
    if not base:
        base = f"http://127.0.0.1:{args.port}"
        env = {**os.environ, "FAKE_AI_LATENCY": str(args.ai_latency)}
        server = subprocess.Popen([sys.executable, str(TESTS / "fake_server.py"), str(args.port)], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if request("GET", base + "/api/health", timeout=2)[0] == 200:
                break
            time.sleep(0.2)
        else:
            server.kill()
            sys.exit("server did not start")
        print(f"server: Flask dev server, fake AI with {args.ai_latency}s per call, in-memory storage")
    total_errors, totals = 0, collections.Counter()
    try:
        for users in args.users:
            errors, by, ok_counts = run_level(base, users, args.seconds)
            total_errors += errors
            totals.update(ok_counts)
            total_errors += verify(base, totals)
    finally:
        if server:
            server.kill()
    print("\nRESULT:", "no errors, data consistent" if not total_errors else f"{total_errors} problem(s)")
    sys.exit(1 if total_errors else 0)


if __name__ == "__main__":
    main()
