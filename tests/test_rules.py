"""Scoring rules: pure Python, no AI. These pin down the behaviour the README promises."""

import json
import unittest

import helpers  # noqa: F401  (sets up sys.path)
from leaddesk import rules
from leaddesk.rules import rescore, score_lead

HOT = {"is_spam": False, "primary_location": "Whitefield", "multiple_locations": False, "property_type": "3BHK",
       "budget_lakhs": 160, "timeline_months": 1, "urgency_reason": "job move", "loan_status": "sanctioned",
       "visit_or_call_requested": True, "already_visited": False, "ready_to_book": False,
       "decision_depends_on_others": False, "decision_maker": "self", "concrete_details_count": 6,
       "specific_questions": ["OC", "maintenance"], "message_detail": "detailed", "objections": []}


class ScoringTests(unittest.TestCase):
    def test_strong_lead_is_hot(self):
        r = score_lead(HOT)
        self.assertEqual(r["tag"], "Hot")
        self.assertGreaterEqual(r["score"], 90)
        self.assertEqual(sum(r["breakdown"].values()), r["score"])

    def test_unknown_is_neutral_not_zero(self):
        unknown = score_lead({**HOT, "loan_status": "unknown"})["breakdown"]["financial"]
        none = score_lead({**HOT, "loan_status": "none"})["breakdown"]["financial"]
        self.assertEqual(unknown, 5)
        self.assertEqual(none, 0)

    def test_unrealistic_budget_scores_low(self):
        r = score_lead({**HOT, "property_type": "villa", "budget_lakhs": 60})
        self.assertLessEqual(r["breakdown"]["budget_fit"], 4)
        self.assertNotEqual(r["tag"], "Disqualified")

    def test_no_budget_is_small_but_not_zero(self):
        self.assertEqual(score_lead({**HOT, "budget_lakhs": None})["breakdown"]["budget_fit"], 4)

    def test_disqualified_cases(self):
        self.assertEqual(score_lead({**HOT, "is_spam": True})["tag"], "Disqualified")
        mumbai = score_lead({**HOT, "primary_location": "Mumbai"})
        self.assertEqual(mumbai["tag"], "Disqualified")
        self.assertIn("service area", mumbai["reasons"][0])

    def test_city_only_is_not_out_of_area(self):
        self.assertNotEqual(score_lead({**HOT, "primary_location": "Bangalore"})["tag"], "Disqualified")
        self.assertNotEqual(score_lead({**HOT, "primary_location": None})["tag"], "Disqualified")

    def test_objections_do_not_lower_score(self):
        base = score_lead(HOT)["score"]
        self.assertEqual(score_lead({**HOT, "objections": ["price", "competitor cheaper", "trust"]})["score"], base)

    def test_high_value_never_cold(self):
        empty = {"primary_location": "Sarjapur Road", "property_type": "4BHK", "budget_lakhs": 400}
        r = score_lead(empty)
        self.assertGreaterEqual(r["score"], rules.WARM)
        self.assertIn("high_value", r["flags"])

    def test_borderline_flag(self):
        self.assertIn("borderline", rules._finish(68, {}, [], [], None)["flags"])
        self.assertNotIn("borderline", rules._finish(55, {}, [], [], None)["flags"])

    def test_authority_matters(self):
        alone = score_lead(HOT)["score"]
        others = score_lead({**HOT, "decision_maker": "needs_others"})["score"]
        self.assertEqual(alone - others, 8)

    def test_market_ranges(self):
        self.assertEqual(list(rules.market_range("Whitefield", "2BHK")), [80, 130])
        self.assertIsNotNone(rules.market_range("Unknown Area", "2BHK"), "falls back to a generic range")
        self.assertIsNone(rules.market_range("Whitefield", "castle"))


class RescoreTests(unittest.TestCase):
    def test_silence_lowers_score(self):
        base = score_lead(HOT)["score"]
        self.assertEqual(base - rescore(HOT, {"days_since_customer_reply": 2})["score"], 0)
        self.assertEqual(base - rescore(HOT, {"days_since_customer_reply": 4})["score"], 5)
        self.assertEqual(base - rescore(HOT, {"days_since_customer_reply": 8})["score"], 15)
        self.assertEqual(base - rescore(HOT, {"days_since_customer_reply": 20})["score"], 25)

    def test_events_raise_score(self):
        weak = {**HOT, "loan_status": "none", "visit_or_call_requested": False, "timeline_months": 6}
        base = score_lead(weak)["score"]
        self.assertEqual(rescore(weak, {"events": ["site_visit_done"]})["score"] - base, 10)

    def test_not_interested_disqualifies(self):
        self.assertEqual(rescore(HOT, {"not_interested": True})["tag"], "Disqualified")

    def test_high_value_floor_survives_silence(self):
        big = {"primary_location": "Hebbal", "property_type": "3BHK", "budget_lakhs": 350}
        self.assertGreaterEqual(rescore(big, {"days_since_customer_reply": 30})["score"], rules.WARM)


class RegressionOnTestLeads(unittest.TestCase):
    """The 18 hand-labelled leads (tests/fixtures) scored from their saved extracted facts."""

    def test_tag_accuracy_at_least_17_of_18(self):
        fixtures = helpers.ROOT / "tests" / "fixtures"
        leads = json.loads((fixtures / "test_leads.json").read_text(encoding="utf-8"))["leads"]
        signals = json.loads((fixtures / "extracted_facts.json").read_text(encoding="utf-8"))
        misses = [l["id"] for l in leads if score_lead(signals[l["id"]])["tag"] != l["expected"]["tag"]]
        self.assertLessEqual(len(misses), 1, f"mismatched leads: {misses}")


if __name__ == "__main__":
    unittest.main()
