"""Pipeline stages: manual moves and automatic advance from logged calls."""

import unittest

from helpers import FORM, fresh


class StageTests(unittest.TestCase):
    def setUp(self):
        self.c, self.ai = fresh()
        self.lid = self.c.post("/api/leads", json=FORM).get_json()["lead"]["id"]

    def stage(self):
        return self.c.get(f"/api/leads/{self.lid}").get_json()["lead"]["stage"]

    def call(self, outcome):
        self.c.post(f"/api/leads/{self.lid}/calls", json={"outcome": outcome, "notes": ""})

    def test_new_leads_start_in_new(self):
        self.assertEqual(self.stage(), "new")

    def test_manual_move_and_validation(self):
        lid = self.c.post("/api/leads", json={**FORM, "name": "Low-score lead", "message": "price? [cold]"}).get_json()["lead"]["id"]
        before = self.c.get(f"/api/leads/{lid}").get_json()["lead"]["score"]
        lead = self.c.post(f"/api/leads/{lid}/stage", json={"stage": "negotiation"}).get_json()["lead"]
        self.assertEqual(lead["stage"], "negotiation")
        self.assertEqual(lead["score"], before + 10, "manual progress updates the live score")
        self.assertEqual(self.c.post(f"/api/leads/{self.lid}/stage", json={"stage": "flying"}).status_code, 400)
        self.assertEqual(self.c.post("/api/leads/nope/stage", json={"stage": "new"}).status_code, 404)

    def test_connected_call_updates_score(self):
        lid = self.c.post("/api/leads", json={**FORM, "name": "Low-score lead", "message": "price? [cold]"}).get_json()["lead"]["id"]
        self.ai.tweak["call_log"] = lambda out: {**out, "signal_updates": {}}
        before = self.c.get(f"/api/leads/{lid}").get_json()["lead"]["score"]
        res = self.c.post(f"/api/leads/{lid}/calls", json={"outcome": "connected", "notes": "Interested"}).get_json()
        self.assertEqual(res["call"]["score_after"], before + 3)
        self.assertEqual(res["lead"]["breakdown"]["activity"], 3)

    def test_calls_advance_stage_forward(self):
        self.call("no_answer")
        self.assertEqual(self.stage(), "new", "no answer doesn't move the deal")
        self.call("connected")
        self.assertEqual(self.stage(), "contacted")
        self.call("visit_booked")
        self.assertEqual(self.stage(), "site_visit")
        self.call("negotiating")
        self.assertEqual(self.stage(), "negotiation")

    def test_stage_never_moves_backwards_automatically(self):
        self.c.post(f"/api/leads/{self.lid}/stage", json={"stage": "negotiation"})
        self.call("connected")
        self.call("visit_done")
        self.assertEqual(self.stage(), "negotiation")

    def test_not_interested_is_lost_and_stays_lost(self):
        self.call("not_interested")
        self.assertEqual(self.stage(), "lost")
        self.call("connected")
        self.assertEqual(self.stage(), "lost")

    def test_mark_responded_moves_new_to_contacted_only(self):
        self.c.post(f"/api/leads/{self.lid}/responded")
        self.assertEqual(self.stage(), "contacted")
        self.c.post(f"/api/leads/{self.lid}/stage", json={"stage": "booked"})
        self.c.post(f"/api/leads/{self.lid}/responded")
        self.assertEqual(self.stage(), "booked")


if __name__ == "__main__":
    unittest.main()
