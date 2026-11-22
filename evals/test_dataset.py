"""Checks the dataset itself, no AI involved:  python -m unittest evals.test_dataset"""
import json
import unittest
from collections import Counter

from evals import build
from evals.chat_set import CHAT
from evals.handwritten import DEFAULT_GOLD, HAND

ROWS = build.build()


def load(name):
    return [json.loads(l) for l in (build.DATA / name).read_text(encoding="utf-8").splitlines()]


class DatasetTest(unittest.TestCase):
    def setUp(self):
        self.leads = load("leads.jsonl")

    def test_ids_unique(self):
        ids = [l["id"] for l in self.leads]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_evidence_phrase_is_in_the_text(self):
        for l in self.leads:
            text = " ".join(l["form"].values())
            for field, phrase in l["evidence"].items():
                self.assertIn(phrase.lower(), text.lower(), f"{l['id']}.{field}: '{phrase}' not in the lead text")

    def test_stated_facts_have_evidence(self):
        for l in self.leads:
            g = l["gold"]
            for field in ("primary_location", "property_type", "budget_lakhs", "timeline_months", "has_reason", "ready_to_book", "already_visited"):
                if g[field] and field not in l["ambiguous_fields"] and l["id"] != "H22":
                    self.assertIn(field, l["evidence"], f"{l['id']}: gold {field} has no evidence")

    def test_gold_has_all_fields(self):
        for l in self.leads:
            self.assertEqual(set(l["gold"]), set(DEFAULT_GOLD), l["id"])

    def test_split_and_balance(self):
        c = Counter(l["split"] for l in self.leads)
        self.assertGreaterEqual(c["heldout"], 20)
        self.assertGreaterEqual(c["dev"], 60)
        cats = Counter(l["category"] for l in self.leads)
        for k in ("natural", "tricky", "spam", "out_of_area", "synthetic"):
            self.assertGreater(cats[k], 0, k)

    def test_languages_and_formats_covered(self):
        langs = {l["lang"] for l in self.leads}
        fmts = {l["format"] for l in self.leads}
        self.assertTrue({"en", "hindi", "hinglish"} <= langs)
        self.assertTrue({"plain", "short", "chat", "call", "rambling"} <= fmts)

    def test_chat_cases_point_at_real_leads(self):
        ids = {l["id"] for l in self.leads}
        for case in load("chat.jsonl"):
            self.assertIn(case["lead_id"], ids)
        kinds = Counter(k for _, k, _, _ in CHAT)
        self.assertGreaterEqual(kinds["unanswerable"], 10)
        self.assertGreaterEqual(kinds["answerable"], 10)

    def test_attacks_and_metamorphic(self):
        self.assertGreaterEqual(len(load("attacks.jsonl")), 12)
        meta = load("metamorphic.jsonl")
        self.assertGreater(len(meta), 30)
        self.assertEqual({m["kind"] for m in meta}, {"invariant", "monotonic"})

    def test_deterministic(self):
        before = (build.DATA / "leads.jsonl").read_bytes()
        build.build()
        self.assertEqual(before, (build.DATA / "leads.jsonl").read_bytes())

    def test_handwritten_count(self):
        self.assertEqual(len(HAND), 29)


if __name__ == "__main__":
    unittest.main()
