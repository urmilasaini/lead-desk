"""Error handling: failures are friendly, never lose data, and never leak provider / model / key details."""

import json
import os
import re
import unittest
from unittest import mock

from helpers import FORBIDDEN, FORM, ROOT, appmod, fresh, service
from leaddesk import ai
from leaddesk.ai import AIError


def no_leak(testcase, text, where=""):
    testcase.assertIsNone(re.search(FORBIDDEN, text, re.I), f"internal detail leaked {where}: {text[:200]}")


class AIErrorTests(unittest.TestCase):
    def test_messages_are_plain_language(self):
        for kind in ("busy", "unavailable", "bad_output", "anything-else"):
            msg = str(AIError(kind, "RESOURCE_EXHAUSTED gemini-2.5-flash key=abc"))
            no_leak(self, msg, kind)
            self.assertNotIn("abc", msg)

    def test_classification(self):
        class E(Exception):
            code = 429
        self.assertEqual(ai._classify(E("x")), "busy")
        self.assertEqual(ai._classify(Exception("RESOURCE_EXHAUSTED")), "busy")
        self.assertEqual(ai._classify(json.JSONDecodeError("bad", "", 0)), "bad_output")
        self.assertEqual(ai._classify(ConnectionError("down")), "unavailable")

    def test_missing_key_is_a_friendly_error_not_a_config_hint(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("GEMINI_API_KEY", None)
            with self.assertRaises(AIError) as cm:
                ai.call_json("system", "user")
        no_leak(self, str(cm.exception), "missing key")


class ProviderError(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


DAILY = ProviderError(429, "RESOURCE_EXHAUSTED: Generate content requests per day limit exceeded. quotaId: "
                           "GenerateRequestsPerDayPerProjectPerModel-FreeTier. retryDelay: '7206s'")
PER_MINUTE = ProviderError(429, "RESOURCE_EXHAUSTED: rate limit reached. Please retry in 12s.")


class FakeClient:
    """Stands in for the provider's client. `outcomes` is consumed one call at a time."""

    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return mock.Mock(text=out)


class QuotaAndRetryTests(unittest.TestCase):
    def call(self, outcomes, **env):
        client = FakeClient(outcomes)
        env = {"GEMINI_FALLBACK_MODELS": "", **env}   # no fallback unless a test asks for one
        with mock.patch.object(ai, "_get_client", return_value=client), mock.patch.object(ai.time, "sleep"), \
                mock.patch.dict(os.environ, env):
            try:
                return ai.call_json("sys", "user"), client, None
            except AIError as e:
                return None, client, e

    def test_daily_limit_fails_fast_with_a_clear_message(self):
        out, client, err = self.call([DAILY])
        self.assertEqual(err.kind, "limit")
        self.assertEqual(len(client.calls), 1, "no pointless retries against a daily quota")
        self.assertIn("usage limit", str(err))
        self.assertIn("about 2 hours", str(err), "tells the user roughly when to come back")
        no_leak(self, str(err), "limit message")

    def test_per_minute_limit_is_retried(self):
        out, client, err = self.call([PER_MINUTE, '{"ok": true}'])
        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(client.calls), 2)

    def test_bad_json_is_retried_and_fenced_json_is_accepted(self):
        out, client, _ = self.call(["not json at all", '```json\n{"ok": 1}\n```'])
        self.assertEqual(out, {"ok": 1})

    def test_gives_up_after_retries_with_a_friendly_error(self):
        out, client, err = self.call([ConnectionError("down")] * 5)
        self.assertEqual(err.kind, "unavailable")
        self.assertEqual(len(client.calls), 3)
        no_leak(self, str(err))

    def test_optional_fallback_model_is_used_when_the_first_is_out_of_quota(self):
        out, client, err = self.call([DAILY, '{"ok": true}'], GEMINI_FALLBACK_MODELS="backup-model")
        self.assertEqual(out, {"ok": True})
        self.assertEqual(client.calls, ["test-model", "backup-model"])

    def test_a_model_that_keeps_failing_hands_over_to_the_fallback(self):
        out, client, err = self.call([ConnectionError("503")] * 3 + ['{"ok": true}'], GEMINI_FALLBACK_MODELS="backup-model")
        self.assertEqual(out, {"ok": True})
        self.assertEqual(client.calls, ["test-model"] * 3 + ["backup-model"])

    def test_fallback_can_be_switched_off(self):
        out, client, err = self.call([DAILY, '{"ok": true}'])
        self.assertIsNotNone(err)
        self.assertEqual(client.calls, ["test-model"])

    def test_missing_model_moves_on_and_config_errors_do_not_retry(self):
        out, client, _ = self.call([ProviderError(404, "NOT_FOUND model"), '{"ok": true}'], GEMINI_FALLBACK_MODELS="b")
        self.assertEqual(out, {"ok": True})
        out, client, err = self.call([ProviderError(400, "INVALID_ARGUMENT"), '{"ok": true}'])
        self.assertEqual(len(client.calls), 1, "a bad request isn't retried")
        self.assertEqual(err.kind, "unavailable")

    def test_defaults_are_the_fast_model_with_a_fallback(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("GEMINI_MODEL", None)
            os.environ.pop("GEMINI_FALLBACK_MODELS", None)
            self.assertEqual(ai._models(), [ai.DEFAULT_MODEL, ai.DEFAULT_FALLBACK])
        self.assertEqual(ai.DEFAULT_MODEL, "gemini-3.5-flash-lite")

    def test_thinking_setting_matches_the_model_generation(self):
        from google.genai import types
        self.assertEqual(ai._thinking(types, "gemini-2.5-flash").thinking_budget, 0)
        self.assertIsNone(ai._thinking(types, "gemini-3.5-flash-lite").thinking_budget)
        level = str(ai._thinking(types, "gemini-3.5-flash-lite").thinking_level).upper()
        self.assertTrue(level.endswith("LOW"), level)

    def test_limit_reaches_the_user_as_a_saved_lead_with_a_clear_reason(self):
        c, fake = fresh()
        fake.fail, fake.fail_kind = 1, "limit"
        lead = c.post("/api/leads", json=FORM).get_json()["lead"]
        self.assertEqual(lead["ai_status"], "failed")
        self.assertIn("usage limit", lead["ai_error"])
        no_leak(self, lead["ai_error"])


class FailureFlowTests(unittest.TestCase):
    def setUp(self):
        self.c, self.ai = fresh()

    def failed_lead(self):
        self.ai.fail = 1
        return self.c.post("/api/leads", json=FORM).get_json()["lead"]

    def test_failed_analysis_still_saves_the_lead(self):
        self.ai.fail = 1
        r = self.c.post("/api/leads", json=FORM)
        self.assertEqual(r.status_code, 201)
        lead = r.get_json()["lead"]
        self.assertEqual((lead["ai_status"], lead["priority"], lead["tag"]), ("failed", "Pending", "Pending"))
        self.assertIsNone(lead["score"])
        self.assertTrue(lead["ai_error"])
        no_leak(self, lead["ai_error"], "ai_error")
        self.assertEqual(lead["form"]["name"], FORM["name"], "what the user typed is kept")
        self.assertIn(lead["id"], [l["id"] for l in self.c.get("/api/leads").get_json()["leads"]])

    def test_failure_after_extract_does_not_leave_half_state(self):
        self.ai.fail = 0
        original = self.ai.__call__
        calls = {"n": 0}

        def second_call_fails(system, user, temperature=0.0):
            calls["n"] += 1
            if calls["n"] == 2:
                raise AIError("busy", "test")
            return original(system, user, temperature)
        service.call_json = second_call_fails
        lead = self.c.post("/api/leads", json=FORM).get_json()["lead"]
        self.assertEqual(lead["ai_status"], "failed")
        self.assertEqual(lead["signals"], {}, "facts are only stored when the whole analysis succeeds")

    def test_reanalyze_failure_then_success(self):
        lead = self.failed_lead()
        self.ai.fail = 1
        self.ai.fail_kind = "busy"
        r = self.c.post(f"/api/leads/{lead['id']}/reanalyze")
        self.assertEqual(r.status_code, 503)
        body = r.get_json()
        self.assertTrue(body["retryable"])
        no_leak(self, body["error"], "503 body")
        still = self.c.get(f"/api/leads/{lead['id']}").get_json()["lead"]
        self.assertEqual(still["ai_status"], "failed")
        self.assertIn("busy", still["ai_error"].lower(), "latest friendly reason is shown")
        ok = self.c.post(f"/api/leads/{lead['id']}/reanalyze").get_json()["lead"]
        self.assertEqual((ok["ai_status"], ok["tag"]), ("ready", "Hot"))
        self.assertIsNone(ok["ai_error"])

    def test_features_that_need_analysis_say_so(self):
        lid = self.failed_lead()["id"]
        for path, body in ((f"/api/leads/{lid}/chat", {"message": "hi"}),
                           (f"/api/leads/{lid}/override", {"to": "Hot", "reason": "x"}),
                           (f"/api/leads/{lid}/reply", {"text": "x"})):
            r = self.c.post(path, json=body)
            self.assertEqual(r.status_code, 400, path)
            no_leak(self, r.get_json()["error"], path)

    def test_chat_failure_is_friendly(self):
        lid = self.c.post("/api/leads", json=FORM).get_json()["lead"]["id"]
        self.ai.fail = 1
        r = self.c.post(f"/api/leads/{lid}/chat", json={"message": "hello"})
        self.assertEqual(r.status_code, 503)
        no_leak(self, r.get_json()["error"], "chat error")
        self.assertEqual(len(self.c.get(f"/api/leads/{lid}").get_json()["lead"]["chat"]), 0, "failed turn isn't stored")

    def test_log_call_still_works_without_ai(self):
        lid = self.c.post("/api/leads", json=FORM).get_json()["lead"]["id"]
        self.ai.fail = 5
        res = self.c.post(f"/api/leads/{lid}/calls", json={"outcome": "visit_done", "notes": "went well"}).get_json()
        self.assertTrue(res["ai_failed"])
        self.assertEqual(len(res["lead"]["calls"]), 1, "call is saved")
        self.assertEqual(res["lead"]["stage"], "site_visit", "stage still advances")
        self.assertEqual(res["lead"]["breakdown"]["activity"], 10, "rule-based score effect still applies")

    def test_log_call_on_unanalysed_lead(self):
        lid = self.failed_lead()["id"]
        res = self.c.post(f"/api/leads/{lid}/calls", json={"outcome": "connected", "notes": "spoke"}).get_json()
        self.assertEqual(len(res["lead"]["calls"]), 1)
        self.assertIsNone(res["call"]["score_before"])
        self.assertEqual(res["lead"]["stage"], "contacted")

    def test_bad_ai_output_is_rejected_not_stored(self):
        original = self.ai.__call__

        def empty_analysis(system, user, temperature=0.0):
            out = original(system, user, temperature)
            return {} if "ANALYZE" in system.upper()[:60] or "Write output" in system else out
        service.call_json = empty_analysis
        lead = self.c.post("/api/leads", json=FORM).get_json()["lead"]
        self.assertEqual(lead["ai_status"], "failed")

    def test_unknown_routes_and_methods_return_json(self):
        r = self.c.get("/api/nope")
        self.assertEqual((r.status_code, r.is_json), (404, True))
        r = self.c.put("/api/leads")
        self.assertEqual((r.status_code, r.is_json), (405, True))
        no_leak(self, r.get_data(as_text=True))

    def test_unexpected_error_is_generic(self):
        with mock.patch.object(service, "create_lead", side_effect=RuntimeError("secret internal detail gemini key")):
            r = self.c.post("/api/leads", json=FORM)
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("secret", r.get_data(as_text=True))
        no_leak(self, r.get_data(as_text=True))


class NothingLeaksTests(unittest.TestCase):
    """Scan everything a user can see for provider / model / key / database names."""

    def test_pages_and_assets(self):
        files = ["index.html", "app.js", "lib.js", "styles.css", "samples.json", "sample_leads.csv"]
        for f in files:
            no_leak(self, (ROOT / "static" / f).read_text(encoding="utf-8"), f"static/{f}")

    def test_api_responses(self):
        c, ai_ = fresh()
        no_leak(self, c.get("/api/health").get_data(as_text=True), "health")
        c.post("/api/leads", json=FORM)
        no_leak(self, c.get("/api/leads").get_data(as_text=True), "leads list")
        ai_.fail = 1
        no_leak(self, c.post("/api/leads", json=FORM).get_data(as_text=True), "failed create")

    def test_health_when_ai_is_not_configured(self):
        c, _ = fresh()
        with mock.patch.dict(os.environ):
            os.environ.pop("GEMINI_API_KEY", None)
            body = c.get("/api/health").get_json()
        self.assertFalse(body["ai_ready"])
        no_leak(self, json.dumps(body))


if __name__ == "__main__":
    unittest.main()
