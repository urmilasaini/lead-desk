"""The sample CSV and a bulk import through the API (one request per row, like the browser does)."""

import csv
import io
import unittest

from helpers import ROOT, fresh

SAMPLE = ROOT / "static" / "sample_leads.csv"


def sample_rows():
    with open(SAMPLE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def form_of(row):
    return {k: row[k] for k in ("name", "phone", "location", "requirement", "budget", "timeline", "message")}


class SampleFileTests(unittest.TestCase):
    def test_columns_and_size(self):
        rows = sample_rows()
        self.assertEqual(list(rows[0]), ["name", "phone", "location", "requirement", "budget", "timeline", "message"])
        self.assertGreaterEqual(len(rows), 10)
        self.assertLessEqual(len(rows), 50)

    def test_every_row_has_name_and_message(self):
        for i, r in enumerate(sample_rows(), 2):
            self.assertTrue(r["name"].strip() and r["message"].strip(), f"row {i}")

    def test_covers_tricky_formats(self):
        rows = sample_rows()
        self.assertTrue(any("\n" in r["message"] for r in rows), "a multi-line chat transcript")
        self.assertTrue(any("," in r["requirement"] for r in rows), "a comma inside a quoted field")
        self.assertTrue(any("chahiye" in r["message"] for r in rows), "Hinglish")
        self.assertTrue(any("mumbai" in r["location"].lower() for r in rows), "out-of-area lead")
        self.assertTrue(any("lead generation" in r["message"] for r in rows), "spam")

    def test_phone_numbers_are_not_real(self):
        self.assertTrue(all(not r["phone"].strip() for r in sample_rows()), "sample must not contain anyone's number")


class BulkImportTests(unittest.TestCase):
    def setUp(self):
        self.c, self.ai = fresh()

    def test_all_rows_import_and_are_ranked(self):
        rows = sample_rows()
        for r in rows:
            self.assertEqual(self.c.post("/api/leads", json=form_of(r)).status_code, 201)
        leads = self.c.get("/api/leads").get_json()["leads"]
        self.assertEqual(len(leads), len(rows))
        tags = {l["form"]["name"]: l["tag"] for l in leads}
        self.assertEqual(tags["GrowFast Media"], "Disqualified", "spam")
        self.assertEqual(tags["Sunil Deshmukh"], "Disqualified", "wrong city")
        self.assertEqual(tags["Aarav Menon"], "Hot")

    def test_one_failure_does_not_stop_the_rest(self):
        rows = sample_rows()[:4]
        statuses = []
        for i, r in enumerate(rows):
            if i == 1:
                self.ai.fail = 1
            statuses.append(self.c.post("/api/leads", json=form_of(r)).get_json()["lead"]["ai_status"])
        self.assertEqual(statuses, ["ready", "failed", "ready", "ready"])
        pending = [l for l in self.c.get("/api/leads").get_json()["leads"] if l["priority"] == "Pending"]
        self.assertEqual(len(pending), 1)
        fixed = self.c.post(f"/api/leads/{pending[0]['id']}/reanalyze").get_json()["lead"]
        self.assertEqual(fixed["ai_status"], "ready")

    def test_row_without_name_or_message_is_rejected(self):
        bad = form_of(sample_rows()[0])
        self.assertEqual(self.c.post("/api/leads", json={**bad, "name": ""}).status_code, 400)
        self.assertEqual(self.c.post("/api/leads", json={**bad, "message": ""}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
