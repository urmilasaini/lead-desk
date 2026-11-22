"""Shared test helpers.

- A fake AI, so tests run offline, are instant and never spend real quota.
- A fresh in-memory app per test. Tests NEVER touch a real database or the real AI:
  the store factory is patched before the app is imported, and the AI function is replaced.
"""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["GEMINI_API_KEY"] = "test-key"          # app thinks the AI is configured; calls are faked below
os.environ["GEMINI_MODEL"] = "test-model"          # deterministic, whatever is in .env
os.environ["GEMINI_FALLBACK_MODELS"] = ""

import leaddesk.store as _store  # noqa: E402
REAL_MAKE_STORE = _store.make_store                # kept only so test_store.py can test the real factory
_store.make_store = lambda: _store.MemoryStore()    # before `import app`, so a real Firestore is never opened

import app as appmod  # noqa: E402
from leaddesk import cache, prompts, service  # noqa: E402
from leaddesk.ai import AIError  # noqa: E402
from leaddesk.store import MemoryStore  # noqa: E402

FORBIDDEN = r"gemini|genai|firebase|firestore|openrouter|api[_ ]?key"   # must never reach the user


NAMES = {prompts.EXTRACT: "extract", prompts.ANALYZE: "analyze", prompts.CHAT: "chat", prompts.CALL_LOG: "call_log"}


class FakeAI:
    """Stands in for the AI. Reads the prompt to decide what to return, so tests can vary behaviour.

    fail / fail_kind  make the next N calls raise AIError
    latency           seconds to sleep per call (load tests)
    tweak[name]       function that rewrites the answer, to simulate a model that was manipulated
    prompts           every (name, system, user) it was sent
    """

    def __init__(self):
        self.calls = []
        self.prompts = []
        self.tweak = {}
        self.latency = 0.0
        self.fail = 0                  # make the next N calls fail
        self.fail_kind = "unavailable"
        self._seen_fail_once = set()

    def __call__(self, system, user, temperature=0.0):
        name = NAMES[system]
        self.prompts.append((name, system, user))
        if self.latency:
            time.sleep(self.latency)
        out = self._respond(system, user, temperature)
        return self.tweak[name](out) if name in self.tweak else out

    def _respond(self, system, user, temperature):
        name = NAMES[system]
        self.calls.append(name)
        if self.fail > 0:
            self.fail -= 1
            raise AIError(self.fail_kind, "test failure")
        if name == "extract" and "[fail-once]" in user and user not in self._seen_fail_once:
            self._seen_fail_once.add(user)
            raise AIError("busy", "test failure once")
        low = user.lower()
        if name == "extract" and "[cold]" in low:   # a weak lead: no budget, no timeline, no urgency
            return {"is_spam": False, "primary_location": "Bangalore", "multiple_locations": False, "property_type": None,
                    "budget_lakhs": None, "timeline_months": None, "urgency_reason": None, "loan_status": "unknown",
                    "visit_or_call_requested": False, "already_visited": False, "ready_to_book": False,
                    "decision_depends_on_others": False, "decision_maker": "unknown", "concrete_details_count": 0,
                    "specific_questions": [], "message_detail": "minimal", "objections": []}
        if name == "extract":
            spam = "lead generation" in low or "[spam]" in low
            return {"is_spam": spam, "primary_location": "Mumbai" if "mumbai" in low else "Whitefield",
                    "multiple_locations": False, "property_type": "3BHK", "budget_lakhs": 160, "timeline_months": 1,
                    "urgency_reason": "job relocation", "loan_status": "sanctioned", "visit_or_call_requested": True,
                    "already_visited": False, "ready_to_book": False, "decision_depends_on_others": False,
                    "decision_maker": "self", "concrete_details_count": 6, "specific_questions": ["OC"],
                    "message_detail": "detailed", "objections": [], "unknown_key_from_ai": "must be dropped"}
        if name == "analyze":
            return {"summary": "Wants a 3BHK fast.", "intent": "Relocating for work.", "key_requirements": ["3BHK"],
                    "objections": [{"concern": "OC status", "how_to_handle": "Share the OC copy."}], "missing_info": ["floor"],
                    "next_action": {"action": "Call and confirm the visit", "when": "now", "why": "urgent"},
                    "call_talking_points": ["Ready units"], "suggested_response": {"channel": "WhatsApp", "text": "Hi, we have ready 3BHKs."}}
        if name == "chat":
            return {"answer": "Lead with the ready units. Hi, let's lock Saturday.", "is_reply": "assertive" in low}
        return {"call_summary": "Wife must agree", "signal_updates": {"decision_maker": "needs_others", "budget_lakhs": "bad"},
                "follow_up": {"channel": "WhatsApp", "text": "Following up on our call."},
                "next_action": {"action": "Video call with wife", "when": "tomorrow", "why": "co-decider"}}


def sample_forms():
    """The rows of static/sample_leads.csv as lead forms."""
    import csv
    with open(ROOT / "static" / "sample_leads.csv", newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f)]


def fresh():
    """New empty app + fake AI. Returns (flask test client, fake)."""
    appmod.store = MemoryStore()
    appmod._storage_check["at"] = 0
    cache.clear()
    fake = FakeAI()
    service.call_json = fake
    return appmod.app.test_client(), fake


FORM = {"name": "Rohit Kulkarni", "location": "Whitefield", "requirement": "3BHK", "budget": "1.6 Cr",
        "timeline": "Within 1 month", "message": "Need a 3BHK near ITPL, loan sanctioned."}
