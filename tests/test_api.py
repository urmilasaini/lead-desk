"""Main API flow with a fake AI: create -> analyse -> chat -> log call -> override -> delete."""

import unittest

from helpers import FORM, ROOT, fresh, service


class ApiFlowTests(unittest.TestCase):
    def setUp(self):
        self.c, self.ai = fresh()

    def test_health_has_no_internal_details(self):
        h = self.c.get("/api/health").get_json()
        self.assertEqual(set(h), {"ok", "ai_ready", "storage_ready"})
        self.assertTrue(h["ai_ready"])
        self.assertTrue(h["storage_ready"])

    def test_starts_empty_and_ranks_leads_by_priority(self):
        self.assertEqual(self.c.get("/api/leads").get_json()["leads"], [], "nothing is pre-filled")
        self.c.post("/api/leads", json={**FORM, "name": "Weak", "message": "price? [cold]"})
        self.c.post("/api/leads", json=FORM)
        leads = self.c.get("/api/leads").get_json()["leads"]
        self.assertEqual([l["form"]["name"] for l in leads], ["Rohit Kulkarni", "Weak"])
        self.assertEqual([l["priority"] for l in leads], ["Hot", "Cold"])

    def test_create_lead_runs_two_ai_steps_and_scores_in_code(self):
        r = self.c.post("/api/leads", json=FORM)
        self.assertEqual(r.status_code, 201)
        lead = r.get_json()["lead"]
        self.assertEqual(self.ai.calls, ["extract", "analyze"])
        self.assertEqual((lead["tag"], lead["ai_status"], lead["stage"]), ("Hot", "ready", "new"))
        self.assertGreaterEqual(lead["score"], 90)
        self.assertNotIn("unknown_key_from_ai", lead["signals"], "unknown AI keys are dropped")
        self.assertEqual(lead["analysis"]["next_action"]["action"], "Call and confirm the visit")
        self.assertEqual(lead["waiting_minutes"], 0)

    def test_validation(self):
        self.assertEqual(self.c.post("/api/leads", json={"name": "", "message": ""}).status_code, 400)
        self.assertEqual(self.c.post("/api/leads", json={"name": "A"}).status_code, 400)
        self.assertEqual(self.c.post("/api/leads", data="not json", content_type="text/plain").status_code, 400)

    def test_long_input_is_trimmed(self):
        lead = self.c.post("/api/leads", json={**FORM, "message": "x" * 9000, "phone": "98765 43210 abc"}).get_json()["lead"]
        self.assertEqual(len(lead["form"]["message"]), 6000)
        self.assertEqual(lead["form"]["phone"], "98765 43210")

    def test_chat_is_stored_and_flags_replies(self):
        lid = self.c.post("/api/leads", json=FORM).get_json()["lead"]["id"]
        reply = self.c.post(f"/api/leads/{lid}/chat", json={"message": "Make my reply more assertive"}).get_json()["reply"]
        self.assertTrue(reply["is_reply"])
        self.assertEqual(len(self.c.get(f"/api/leads/{lid}").get_json()["lead"]["chat"]), 2)
        self.assertEqual(self.c.post(f"/api/leads/{lid}/chat", json={"message": "  "}).status_code, 400)

    def test_log_call_updates_facts_score_and_follow_up(self):
        lid = self.c.post("/api/leads", json=FORM).get_json()["lead"]["id"]
        res = self.c.post(f"/api/leads/{lid}/calls", json={"outcome": "visit_done", "notes": "wife must agree"}).get_json()
        lead, call = res["lead"], res["call"]
        self.assertFalse(res["ai_failed"])
        self.assertEqual(lead["signals"]["decision_maker"], "needs_others")
        self.assertEqual(lead["signals"]["budget_lakhs"], 160, "bad AI value is rejected")
        self.assertEqual(lead["breakdown"]["activity"], 10)
        self.assertEqual(lead["analysis"]["next_action"]["action"], "Video call with wife")
        self.assertEqual(lead["analysis"]["suggested_response"]["text"], "Following up on our call.")
        self.assertIsNone(lead["waiting_minutes"], "response clock stops after a call")
        self.assertEqual(len(lead["calls"]), 1)
        self.assertIn("score_before", call)
        self.assertEqual(self.c.post(f"/api/leads/{lid}/calls", json={"outcome": "bogus"}).status_code, 400)

    def test_not_interested_disqualifies_and_closes_stage(self):
        lid = self.c.post("/api/leads", json=FORM).get_json()["lead"]["id"]
        lead = self.c.post(f"/api/leads/{lid}/calls", json={"outcome": "not_interested", "notes": ""}).get_json()["lead"]
        self.assertEqual((lead["tag"], lead["stage"]), ("Disqualified", "lost"))

    def test_override_needs_reason_and_keeps_ai_tag(self):
        lid = self.c.post("/api/leads", json=FORM).get_json()["lead"]["id"]
        self.assertEqual(self.c.post(f"/api/leads/{lid}/override", json={"to": "Cold"}).status_code, 400)
        self.assertEqual(self.c.post(f"/api/leads/{lid}/override", json={"to": "Maybe", "reason": "x"}).status_code, 400)
        lead = self.c.post(f"/api/leads/{lid}/override", json={"to": "Cold", "reason": "told me to call in 6 months"}).get_json()["lead"]
        self.assertEqual((lead["priority"], lead["tag"], lead["override"]["from"]), ("Cold", "Hot", "Hot"))
        lead = self.c.post(f"/api/leads/{lid}/override", json={"to": None}).get_json()["lead"]
        self.assertIsNone(lead["override"])
        self.assertEqual(lead["priority"], "Hot")

    def test_manual_priority_corrects_an_out_of_service_extraction(self):
        lid = self.c.post("/api/leads", json={**FORM, "message": "Looking in Mumbai"}).get_json()["lead"]["id"]
        corrected = self.c.post(f"/api/leads/{lid}/override", json={"to": "Cold", "reason": "We do serve this lead"}).get_json()["lead"]
        self.assertEqual((corrected["score"], corrected["priority"]), (20, "Cold"))
        logged = self.c.post(f"/api/leads/{lid}/calls", json={"outcome": "connected", "notes": "Interested"}).get_json()
        self.assertEqual(logged["call"]["score_after"], 23)

    def test_reply_replace_mark_responded_phone_delete(self):
        lid = self.c.post("/api/leads", json=FORM).get_json()["lead"]["id"]
        lead = self.c.post(f"/api/leads/{lid}/reply", json={"text": "New wording"}).get_json()["lead"]
        self.assertEqual(lead["analysis"]["suggested_response"]["text"], "New wording")
        self.assertEqual(self.c.post(f"/api/leads/{lid}/reply", json={"text": " "}).status_code, 400)
        self.assertIsNone(self.c.post(f"/api/leads/{lid}/responded").get_json()["lead"]["waiting_minutes"])
        self.assertEqual(self.c.patch(f"/api/leads/{lid}", json={"phone": "+971 50 123 4567"}).get_json()["lead"]["form"]["phone"], "+971 50 123 4567")
        self.assertEqual(self.c.delete(f"/api/leads/{lid}").status_code, 200)
        self.assertEqual(self.c.get(f"/api/leads/{lid}").status_code, 404)

    def test_reanalyze_runs_the_ai_again(self):
        lid = self.c.post("/api/leads", json=FORM).get_json()["lead"]["id"]
        before = len(self.ai.calls)
        self.assertEqual(self.c.post(f"/api/leads/{lid}/reanalyze").get_json()["lead"]["ai_status"], "ready")
        self.assertEqual(self.ai.calls[before:], ["extract", "analyze"])

    def test_pages_are_served(self):
        def status(path):
            with self.c.get(path) as r:  # closing the response releases the file handle
                return r.status_code
        with self.c.get("/") as r:
            self.assertIn(b"Lead Desk", r.data)
        for f in ("app.js", "lib.js", "styles.css", "samples.json", "sample_leads.csv"):
            self.assertEqual(status(f"/static/{f}"), 200, f)
        self.assertEqual(status("/static/../app.py"), 404, "no path traversal")


class NoCannedDataTests(unittest.TestCase):
    """Every analysis must come from a live AI call. Guard against demo / canned data creeping back."""

    def test_no_demo_machinery(self):
        c, _ = fresh()
        self.assertFalse((ROOT / "leaddesk" / "demo_leads.json").exists())
        self.assertFalse(hasattr(service, "seed_demo"))
        self.assertIn(c.post("/api/demo/reset").status_code, (404, 405))
        self.assertEqual(c.get("/api/leads").get_json()["leads"], [])

    def test_analysis_text_always_comes_from_the_ai(self):
        c, ai_ = fresh()
        ai_.fail = 1
        lead = c.post("/api/leads", json=FORM).get_json()["lead"]
        self.assertEqual(lead["analysis"]["summary"], "", "a failed analysis stays empty, nothing is made up")
        self.assertEqual(lead["analysis"]["suggested_response"]["text"], "")
        self.assertEqual(ai_.calls, ["extract"])

    def test_page_and_scripts_have_no_demo_content(self):
        for f in ("index.html", "app.js"):
            text = (ROOT / "static" / f).read_text(encoding="utf-8").lower()
            self.assertNotIn("demo analysis", text, f)
            self.assertNotIn("reset demo", text, f)


if __name__ == "__main__":
    unittest.main()
