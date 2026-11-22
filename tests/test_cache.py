"""AI response cache and HTTP caching."""

import json
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from helpers import FORM, appmod, fresh
from leaddesk import cache


class AICacheTests(unittest.TestCase):
    def setUp(self):
        self.c, self.ai = fresh()

    def add(self, **over):
        return self.c.post("/api/leads", json={**FORM, **over}).get_json()["lead"]

    def test_identical_lead_reuses_the_previous_ai_answer(self):
        a = self.add()
        self.assertEqual(self.ai.calls, ["extract", "analyze"])
        b = self.add()
        self.assertEqual(self.ai.calls, ["extract", "analyze"], "second lead made no AI calls")
        self.assertEqual((b["tag"], b["score"], b["analysis"]), (a["tag"], a["score"], a["analysis"]))
        self.assertEqual(cache.stats["hits"], 2)

    def test_different_input_is_a_miss(self):
        self.add()
        self.add(message="A completely different message about a 2BHK")
        self.assertEqual(self.ai.calls, ["extract", "analyze", "extract", "analyze"])

    def test_reanalyze_always_asks_the_ai_again(self):
        lead = self.add()
        before = len(self.ai.calls)
        self.c.post(f"/api/leads/{lead['id']}/reanalyze")
        self.assertEqual(self.ai.calls[before:], ["extract", "analyze"])

    def test_failures_are_never_cached(self):
        self.ai.fail = 1
        self.assertEqual(self.add()["ai_status"], "failed")
        again = self.add()
        self.assertEqual(again["ai_status"], "ready")
        self.assertEqual(self.ai.calls, ["extract", "extract", "analyze"])

    def test_chat_and_call_log_are_not_cached(self):
        lid = self.add()["id"]
        for _ in range(2):
            self.c.post(f"/api/leads/{lid}/chat", json={"message": "What should I say?"})
        self.assertEqual(self.ai.calls.count("chat"), 2)

    def test_shared_store_serves_a_cold_instance(self):
        self.add()
        cache.clear()                       # a new serverless instance: empty memory, same database
        self.add()
        self.assertEqual(self.ai.calls, ["extract", "analyze"])

    def test_cache_problems_never_break_a_request(self):
        with mock.patch.object(appmod.store, "cache_get", side_effect=RuntimeError("down")), \
                mock.patch.object(appmod.store, "cache_set", side_effect=RuntimeError("down")):
            lead = self.add()
        self.assertEqual(lead["ai_status"], "ready")

    def test_corrupt_entry_falls_back_to_a_real_call(self):
        out = cache.cached(appmod.store, "m", "s", "u", 0.0, lambda: {"v": 1})
        key = cache.make_key("m", "s", "u", 0.0)
        cache.clear()
        appmod.store._cache[key] = ("{not json", datetime.now(timezone.utc).isoformat())
        again = cache.cached(appmod.store, "m", "s", "u", 0.0, lambda: {"v": 2})
        self.assertEqual((out, again), ({"v": 1}, {"v": 2}))

    def test_expired_entries_are_ignored(self):
        cache.cached(appmod.store, "m", "s", "u", 0.0, lambda: {"v": 1})
        key = cache.make_key("m", "s", "u", 0.0)
        cache.clear()
        value = appmod.store._cache[key][0]
        appmod.store._cache[key] = (value, (datetime.now(timezone.utc) - timedelta(days=8)).isoformat())
        self.assertEqual(cache.cached(appmod.store, "m", "s", "u", 0.0, lambda: {"v": 2}), {"v": 2})

    def test_key_depends_on_model_prompt_text_and_temperature(self):
        base = cache.make_key("m", "sys", "user", 0.0)
        for other in (("m2", "sys", "user", 0.0), ("m", "sys2", "user", 0.0), ("m", "sys", "user2", 0.0), ("m", "sys", "user", 0.3)):
            self.assertNotEqual(base, cache.make_key(*other))
        self.assertEqual(base, cache.make_key("m", "sys", "user", 0.0))

    def test_changing_the_model_invalidates_the_cache(self):
        self.add()
        with mock.patch.dict(os.environ, {"GEMINI_MODEL": "another-model"}):
            self.add()
        self.assertEqual(self.ai.calls, ["extract", "analyze", "extract", "analyze"])

    def test_cached_answers_are_independent_copies(self):
        first = cache.cached(appmod.store, "m", "s", "u", 0.0, lambda: {"a": [1]})
        first["a"].append(2)
        self.assertEqual(cache.cached(appmod.store, "m", "s", "u", 0.0, lambda: {"a": []}), {"a": [1]})

    def test_memory_level_is_bounded(self):
        with mock.patch.object(cache, "MEMORY_MAX", 3):
            for i in range(10):
                cache.cached(appmod.store, "m", "s", f"u{i}", 0.0, lambda: {"i": 1})
        self.assertLessEqual(len(cache._mem), 3)


class HttpCacheTests(unittest.TestCase):
    def setUp(self):
        self.c, _ = fresh()

    def test_page_links_versioned_assets(self):
        html = self.c.get("/").get_data(as_text=True)
        for name in ("styles.css", "lib.js", "app.js"):
            self.assertRegex(html, rf'/static/{re.escape(name)}\?v=[0-9a-f]{{10}}"')
        self.assertEqual(self.c.get("/").headers["Cache-Control"], "no-cache")

    def test_versioned_assets_are_cached_for_a_year(self):
        with self.c.get(f"/static/app.js?v={appmod.ASSET_VERSION}") as r:
            self.assertIn("immutable", r.headers["Cache-Control"])
            self.assertIn("max-age=31536000", r.headers["Cache-Control"])
        with self.c.get("/static/sample_leads.csv") as r:
            self.assertNotIn("immutable", r.headers.get("Cache-Control", ""))

    def test_missing_versioned_file_is_not_cached(self):
        r = self.c.get("/static/nope.js?v=abc")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("immutable", r.headers.get("Cache-Control", ""))

    def test_api_responses_are_never_cached(self):
        for path in ("/api/health", "/api/leads"):
            self.assertEqual(self.c.get(path).headers["Cache-Control"], "no-store", path)
        self.assertEqual(self.c.post("/api/leads", json={"name": ""}).headers["Cache-Control"], "no-store")

    def test_asset_version_is_stable_and_follows_content(self):
        self.assertEqual(appmod._asset_version(), appmod.ASSET_VERSION)
        with mock.patch.object(appmod.Path, "read_bytes", return_value=b"changed"):
            self.assertNotEqual(appmod._asset_version(), appmod.ASSET_VERSION)


if __name__ == "__main__":
    unittest.main()
