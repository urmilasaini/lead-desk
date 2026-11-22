# Tests

Run everything:

```bash
python tests/run_all.py
```

| Command | What it covers | Needs |
|---|---|---|
| `python -m unittest discover -s tests` | Python tests (below) | Python only |
| `cd tests/frontend && npm test` | Browser-level tests in jsdom (first run: `npm install`) | Node 20+ |
| `python tests/live_check.py` | **Optional.** End-to-end with the real AI, including a prompt-injection attack, about 7 calls | AI key in `.env` |
| `python tests/load/load_test.py` | **Optional.** 10, 25 and 50 simulated users against a local server (fake AI as slow as the real one), then checks no lead, chat message or call log was lost | Python only |
| `python tests/load/firestore_scale.py` | **Optional.** The real database as leads pile up, in throwaway collections it deletes afterwards; counts reads and writes | Firebase credentials in `.env` |

`fixtures/` holds those 18 test leads and the facts extracted from them (see its README).

The normal tests use a **fake AI** (`helpers.py`), so they are instant and free. They also never open a real database.

## What each file checks

| File | Checks |
|---|---|
| `test_rules.py` | Scoring rubric: hot lead, unknown ≠ no, unrealistic budget, spam, wrong city, objections don't lower the score, high-value floor, borderline flag, score drops with silence, call events raise it. Also scores the 18 hand-labelled leads in `fixtures/` and requires at least 17 correct tags. |
| `test_api.py` | Create → analyse → chat → log call → change priority → delete, input trimming, validation, the app starts empty with no canned data, pages are served. |
| `test_errors.py` | A failed analysis still **saves the lead** and offers Re-analyze; Log call works without the AI; chat/override on an unanalysed lead say what to do; unknown routes return JSON; unexpected crashes return a generic message. **Nothing about the AI provider, model, keys or database ever appears** in any response, page or asset. |
| `test_store.py` | Credentials from base64 / JSON / file (secrets never logged), **no silent in-memory fallback**, a failing database becomes a friendly 503 with no AI quota spent, health reports real storage status, documents written by other tools are ignored, partial updates and atomic appends, and the lead-list cache (re-read only when something changed, one reload when many miss at once). |
| `test_cache.py` | AI cache: identical lead makes no AI calls, a cold instance is served from the database, Re-analyze bypasses it, failures and cache faults never break a request, expiry, model change invalidates. HTTP caching: versioned assets cached for a year, API never cached. |
| `test_safety.py` | Prompt injection: a message can't close its own data block, attack phrases are flagged and normal messages aren't, manipulated facts (500 crore budget, invented loan status) are rejected, links the customer didn't write are removed from replies, chat and follow-ups. |
| `test_concurrency.py` | Several people at once: a lead being analysed shows as "analysing" (not failed), and chat, log call and re-analyze never undo someone else's stage change, phone number, override or message. |
| `test_stages.py` | Pipeline stages: manual moves, automatic advance from logged calls, never backwards, Lost stays Lost. |
| `test_import_csv.py` | `static/sample_leads.csv` is valid (columns, quoting, multi-line chat, Hinglish, spam, wrong city, no real phone numbers) and every row imports; one failing row doesn't stop the rest. |
| `frontend/lib.test.js` | CSV parser (quotes, BOM, `;` and tab separators, flexible column names, 50-row cap), phone cleanup, WhatsApp links. |
| `frontend/smoke.test.js` | The real page: sidebar, How it works, board, lead page, **chat widget**, WhatsApp link with a phone number, stage select, **Pipeline drag-and-drop**, Add lead failing once then **Re-analyze + Open working in place**, **CSV import** with a failing row, Log call, call plan, and no JavaScript errors. |
