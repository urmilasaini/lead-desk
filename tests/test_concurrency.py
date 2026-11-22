"""Several people using the app at once. Found by the load test, pinned here.

1. A lead that is still being analysed must look like "analysing", not like a failure.
2. An action that waits for the AI (chat, log call, re-analyze) must not wipe out a change someone else made to the
   same lead while it was waiting.
"""

import threading
import time
import unittest
from datetime import timedelta

from helpers import FORM, appmod, fresh
from leaddesk import service


def in_thread(fn):
    out = {}

    def run():
        out["value"] = fn()
    t = threading.Thread(target=run)
    t.start()
    return t, out


class AnalysingStateTests(unittest.TestCase):
    def setUp(self):
        self.c, self.ai = fresh()

    def test_a_lead_being_created_is_analysing_not_failed(self):
        self.ai.latency = 0.5
        t, out = in_thread(lambda: self.c.post("/api/leads", json=FORM))
        time.sleep(0.2)
        listed = self.c.get("/api/leads").get_json()["leads"]
        self.assertEqual(len(listed), 1)
        mid = listed[0]
        self.assertEqual((mid["ai_status"], mid["priority"]), ("analyzing", "Pending"))
        self.assertFalse(mid["ai_error"], "no error message for something that is still running")
        t.join()
        done = self.c.get(f"/api/leads/{mid['id']}").get_json()["lead"]
        self.assertEqual((done["ai_status"], done["tag"]), ("ready", "Hot"))

    def test_others_get_a_clear_message_instead_of_a_duplicate_analysis(self):
        self.ai.latency = 0.5
        t, out = in_thread(lambda: self.c.post("/api/leads", json=FORM))
        time.sleep(0.2)
        lid = self.c.get("/api/leads").get_json()["leads"][0]["id"]
        r = self.c.post(f"/api/leads/{lid}/reanalyze")
        self.assertEqual(r.status_code, 400)
        self.assertIn("being analysed", r.get_json()["error"])
        self.assertEqual(self.c.post(f"/api/leads/{lid}/chat", json={"message": "hi"}).status_code, 400)
        t.join()
        self.assertEqual(self.ai.calls, ["extract", "analyze"], "the blocked requests made no extra AI calls")

    def test_an_analysis_that_never_finished_turns_into_a_failure_after_a_while(self):
        lead = self.c.post("/api/leads", json=FORM).get_json()["lead"]
        stuck = appmod.store.get(lead["id"])
        old = (service.now() - timedelta(minutes=10)).isoformat()
        stuck.update(ai_status="analyzing", analyzing_since=old)
        appmod.store.save(stuck)
        shown = self.c.get(f"/api/leads/{lead['id']}").get_json()["lead"]
        self.assertEqual(shown["ai_status"], "failed")
        self.assertTrue(shown["ai_error"])
        again = self.c.post(f"/api/leads/{lead['id']}/reanalyze").get_json()["lead"]
        self.assertEqual(again["ai_status"], "ready")

    def test_a_crash_during_analysis_does_not_leave_the_lead_analysing(self):
        original = service.call_json
        service.call_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertEqual(self.c.post("/api/leads", json=FORM).status_code, 500)
        finally:
            service.call_json = original
        lead = self.c.get("/api/leads").get_json()["leads"][0]
        self.assertEqual(lead["ai_status"], "failed")


class LostUpdateTests(unittest.TestCase):
    def setUp(self):
        self.c, self.ai = fresh()
        self.lid = self.c.post("/api/leads", json=FORM).get_json()["lead"]["id"]
        self.ai.latency = 0.5

    def lead(self):
        return self.c.get(f"/api/leads/{self.lid}").get_json()["lead"]

    def during_ai_call(self, slow, quick):
        """Run `slow` (waits on the AI) and, while it waits, `quick` from another request."""
        t, out = in_thread(slow)
        time.sleep(0.2)
        quick()
        t.join()
        self.ai.latency = 0

    def test_chat_does_not_undo_a_stage_change(self):
        self.during_ai_call(lambda: self.c.post(f"/api/leads/{self.lid}/chat", json={"message": "hello"}),
                            lambda: self.c.post(f"/api/leads/{self.lid}/stage", json={"stage": "booked"}))
        lead = self.lead()
        self.assertEqual(lead["stage"], "booked")
        self.assertEqual(len(lead["chat"]), 2)

    def test_log_call_does_not_undo_a_stage_change_or_a_phone_number(self):
        def quick():
            self.c.post(f"/api/leads/{self.lid}/stage", json={"stage": "negotiation"})
            self.c.patch(f"/api/leads/{self.lid}", json={"phone": "98765 43210"})
        self.during_ai_call(lambda: self.c.post(f"/api/leads/{self.lid}/calls", json={"outcome": "connected", "notes": "spoke"}), quick)
        lead = self.lead()
        self.assertEqual(lead["stage"], "negotiation", "an earlier stage from the call must not overwrite a later one")
        self.assertEqual(lead["form"]["phone"], "98765 43210")
        self.assertEqual(len(lead["calls"]), 1)

    def test_two_chats_at_once_keep_all_messages(self):
        t1, _ = in_thread(lambda: self.c.post(f"/api/leads/{self.lid}/chat", json={"message": "first"}))
        t2, _ = in_thread(lambda: self.c.post(f"/api/leads/{self.lid}/chat", json={"message": "second"}))
        t1.join(); t2.join()
        self.assertEqual(len(self.lead()["chat"]), 4)

    def test_two_call_logs_at_once_keep_both(self):
        t1, _ = in_thread(lambda: self.c.post(f"/api/leads/{self.lid}/calls", json={"outcome": "connected", "notes": "a"}))
        t2, _ = in_thread(lambda: self.c.post(f"/api/leads/{self.lid}/calls", json={"outcome": "visit_booked", "notes": "b"}))
        t1.join(); t2.join()
        self.assertEqual(len(self.lead()["calls"]), 2)

    def test_reanalyze_does_not_undo_a_stage_change_or_override(self):
        self.c.post(f"/api/leads/{self.lid}/override", json={"to": "Cold", "reason": "told me later"})
        self.during_ai_call(lambda: self.c.post(f"/api/leads/{self.lid}/reanalyze"),
                            lambda: self.c.post(f"/api/leads/{self.lid}/stage", json={"stage": "site_visit"}))
        lead = self.lead()
        self.assertEqual((lead["stage"], lead["priority"]), ("site_visit", "Cold"))

    def test_a_lead_deleted_while_the_ai_thinks_is_not_resurrected(self):
        self.during_ai_call(lambda: self.c.post(f"/api/leads/{self.lid}/chat", json={"message": "hello"}),
                            lambda: self.c.delete(f"/api/leads/{self.lid}"))
        self.assertEqual(self.c.get(f"/api/leads/{self.lid}").status_code, 404)
        self.assertEqual(self.c.get("/api/leads").get_json()["leads"], [])


class ManyAtOnceTests(unittest.TestCase):
    def test_twenty_people_adding_leads_at_once(self):
        c, ai = fresh()
        ai.latency = 0.05
        results = []

        def add(i):
            client = appmod.app.test_client()
            results.append(client.post("/api/leads", json={**FORM, "name": f"P{i}", "message": f"unique message {i}"}).status_code)
        threads = [threading.Thread(target=add, args=(i,)) for i in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        leads = c.get("/api/leads").get_json()["leads"]
        self.assertEqual(results, [201] * 20)
        self.assertEqual(len(leads), 20)
        self.assertTrue(all(l["ai_status"] == "ready" for l in leads))


if __name__ == "__main__":
    unittest.main()
