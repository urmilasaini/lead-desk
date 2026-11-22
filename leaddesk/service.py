"""Business logic: create, score, analyse, chat, log calls, stages. No canned or demo analysis exists anywhere: every analysis comes from a live AI call. Flask routes in app.py stay thin.

Pipeline for a new lead:
  form -> AI EXTRACT (facts JSON) -> rules.score_lead (code) -> AI ANALYZE (advice JSON) -> store
The lead is saved BEFORE the AI runs. If the AI fails, the lead stays in the list as "needs analysis"
and can be re-analysed later, so nothing a salesperson typed or imported is ever lost.
Scores are recomputed on every read with rules.rescore(), so silence over time and call outcomes
change the priority without another AI call.
"""

import copy
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from . import cache, prompts, safety
from .ai import AIError, call_json, primary_model
from .rules import rescore
from .store import Append, LeadGone

log = logging.getLogger("leaddesk.service")

FORM_FIELDS = ["name", "phone", "location", "requirement", "budget", "timeline", "message"]
FIELD_LIMITS = {"name": 120, "phone": 20, "location": 120, "requirement": 200, "budget": 60, "timeline": 60, "message": 6000}
STAGES = ["new", "contacted", "site_visit", "negotiation", "booked", "lost"]
STAGE_LABEL = {"new": "New", "contacted": "Contacted", "site_visit": "Site visit", "negotiation": "Negotiation",
               "booked": "Booked", "lost": "Lost"}
PRIORITIES = ("Hot", "Warm", "Cold", "Disqualified")
NEEDS_ANALYSIS_MSG = "Run the analysis first, then you can use this."

SIGNAL_KEYS = {
    "is_spam": bool, "primary_location": (str, type(None)), "multiple_locations": bool,
    "property_type": (str, type(None)), "budget_lakhs": (int, float, type(None)),
    "timeline_months": (int, float, type(None)), "urgency_reason": (str, type(None)), "loan_status": str,
    "visit_or_call_requested": bool, "already_visited": bool, "ready_to_book": bool,
    "decision_depends_on_others": bool, "decision_maker": str, "concrete_details_count": int,
    "specific_questions": list, "message_detail": str, "objections": list,
}
ANALYSIS_KEYS = ["summary", "intent", "key_requirements", "objections", "missing_info",
                 "next_action", "call_talking_points", "suggested_response"]
# Call outcome -> fact changes, rescore events and pipeline stage
OUTCOMES = {
    "connected":      {"stage": "contacted"},
    "no_answer":      {},
    "visit_booked":   {"signals": {"visit_or_call_requested": True}, "stage": "site_visit"},
    "visit_done":     {"signals": {"already_visited": True}, "event": "site_visit_done", "stage": "site_visit"},
    "negotiating":    {"event": "negotiating", "stage": "negotiation"},
    "not_interested": {"not_interested": True, "stage": "lost"},
}


class ValidationError(Exception):
    pass


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now()).isoformat()


def _parse_iso(s):
    return datetime.fromisoformat(s) if s else None


REQUIRED_KEYS = ("id", "created_at", "form", "signals", "analysis")


def is_valid(lead):
    """True for documents this app wrote. Anything else in the collection is ignored, never allowed to break the list."""
    return (isinstance(lead, dict) and all(k in lead for k in REQUIRED_KEYS) and isinstance(lead["form"], dict)
            and "name" in lead["form"] and "message" in lead["form"])


def is_ready(lead):
    return lead.get("ai_status", "ready") == "ready"


FAILED_MSG = "The analysis didn't finish. You can try again."
BEING_ANALYSED_MSG = "This lead is being analysed right now. Please try again in a few seconds."
ANALYZING_TTL_S = 120   # an analysis that has been "running" longer than this is treated as failed (the server must have died)


def _status(lead):
    """ready | analyzing | failed, as other people should see it."""
    status = lead.get("ai_status", "ready")
    if status == "analyzing":
        since = _parse_iso(lead.get("analyzing_since") or lead["created_at"])
        if (now() - since).total_seconds() > ANALYZING_TTL_S:
            return "failed"
    return status


def _not_ready_error(lead):
    return ValidationError(BEING_ANALYSED_MSG if _status(lead) == "analyzing" else NEEDS_ANALYSIS_MSG)


def _commit(store, lead_id, apply, current=None):
    """Apply a change to a FRESH copy of the lead and write back only what changed.

    Actions that wait on the AI take seconds. Saving the copy read before the wait would silently undo anything
    someone else changed in the meantime (a stage move, a phone number, a chat message). So: re-read, apply the
    change, and send only the changed fields. `apply(lead)` may return {"chat": [...], "calls": [...]} for lists
    that should be appended to atomically. Quick actions pass `current` (read a moment ago) to skip the re-read.
    Returns the updated lead."""
    fresh = copy.deepcopy(current) if current is not None else store.get(lead_id)
    if fresh is None:
        raise LeadGone()
    before = copy.deepcopy(fresh)
    appends = apply(fresh) or {}
    changed = {k: v for k, v in fresh.items() if k not in appends and v != before.get(k)}
    for key, items in appends.items():
        fresh[key] = list(before.get(key, [])) + list(items)
        changed[key] = Append(items)
    if changed:
        store.update(lead_id, changed)
    return fresh


# ---------------- validation / cleaning ----------------
def clean_phone(v):
    return re.sub(r"[^\d+ ()-]", "", str(v or "")).strip()[:FIELD_LIMITS["phone"]]


def clean_form(data):
    form = {}
    for k in FORM_FIELDS:
        v = str((data or {}).get(k, "") or "").strip()
        form[k] = clean_phone(v) if k == "phone" else v[:FIELD_LIMITS[k]]
    if not form["name"] or not form["message"]:
        raise ValidationError("Name and customer message are required.")
    return form


ENUMS = {  # field -> (allowed values, value used when the AI sends anything else)
    "loan_status": ({"sanctioned", "pre_approved", "in_process", "own_funds", "down_payment_ready", "none", "unknown"}, "unknown"),
    "decision_maker": ({"self", "joint_present", "needs_others", "unknown"}, "unknown"),
    "property_type": ({"1BHK", "2BHK", "3BHK", "4BHK", "villa", "plot"}, None),
    "message_detail": ({"minimal", "basic", "detailed"}, "minimal"),
}
RANGES = {"budget_lakhs": (0, 20000), "timeline_months": (0, 120)}   # 20000 lakhs = 200 Cr; 10 years
LIST_LIMIT, ITEM_LIMIT = 8, 120


def clean_signals(raw):
    """Keep only known keys, with the right type, an allowed value and a sane range, so a bad or manipulated
    AI answer (a budget of 500 crore, an invented loan status) can't distort scoring."""
    out = {}
    for k, t in SIGNAL_KEYS.items():
        v = raw.get(k)
        if isinstance(v, bool) and t in (int, (int, float, type(None))):
            v = None  # bool is an int in Python; reject it for numeric fields
        if isinstance(v, t):
            out[k] = v
    for k, (allowed, fallback) in ENUMS.items():
        if k in out and out[k] not in allowed:
            out[k] = fallback
    for k, (lo, hi) in RANGES.items():
        if isinstance(out.get(k), (int, float)) and not (lo < out[k] <= hi if k == "budget_lakhs" else lo <= out[k] <= hi):
            out[k] = None
    if "concrete_details_count" in out:
        out["concrete_details_count"] = max(0, min(12, int(out["concrete_details_count"])))
    for k in ("specific_questions", "objections"):
        if k in out:
            out[k] = [safety.clean(x)[:ITEM_LIMIT] for x in out[k] if isinstance(x, str)][:LIST_LIMIT]
    for k, n in (("primary_location", 80), ("urgency_reason", 160)):
        if isinstance(out.get(k), str):
            out[k] = safety.clean(out[k])[:n]
    return out


def clean_analysis(raw, allowed=""):
    """Shape and size-limit the AI's analysis, and remove any link the customer didn't write themselves
    (`allowed` is the text of everything the customer wrote)."""
    def t(v, n):
        return safety.strip_foreign_urls(str(v or "").strip()[:n], allowed)
    a = {k: raw.get(k) for k in ANALYSIS_KEYS}
    a["summary"], a["intent"] = t(a["summary"], 600), t(a["intent"], 400)
    for k, n in (("key_requirements", 80), ("missing_info", 100), ("call_talking_points", 250)):
        a[k] = [t(x, n) for x in a[k] if x][:8] if isinstance(a[k], list) else []
    objs = a["objections"] if isinstance(a["objections"], list) else []
    a["objections"] = [{"concern": t(o["concern"], 120), "how_to_handle": t(o.get("how_to_handle", ""), 400)}
                       for o in objs if isinstance(o, dict) and o.get("concern")][:6]
    na = a["next_action"] if isinstance(a["next_action"], dict) else {}
    a["next_action"] = {"action": t(na.get("action"), 200), "when": t(na.get("when"), 100), "why": t(na.get("why"), 300)}
    sr = a["suggested_response"] if isinstance(a["suggested_response"], dict) else {}
    a["suggested_response"] = {"channel": t(sr.get("channel", "WhatsApp"), 20) or "WhatsApp", "text": t(sr.get("text"), 1500)}
    return a


# ---------------- context text for prompts ----------------
def raw_text(form):
    """Everything the customer wrote, as plain text. Used to decide which links in AI output are legitimate."""
    return "\n".join(str(form.get(k, "")) for k in ("name", "location", "requirement", "budget", "timeline", "message"))


def lead_text(form):
    c = safety.clean
    return safety.wrap("customer_data",
                       f"Name: {c(form['name'])}\nLocation: {c(form['location'])}\n"
                       f"Property requirement: {c(form['requirement'])}\nBudget: {c(form['budget'])}\n"
                       f"Buying timeline: {c(form['timeline'])}\nCustomer message:\n{c(form['message'])}")


def full_context(lead, scored):
    c = safety.clean
    calls = "\n".join(f"- {cl['at'][:16]} {cl['outcome']}: {c(cl.get('summary') or cl.get('notes', ''))}" for cl in lead.get("calls", [])) or "none"
    return (f"{lead_text(lead['form'])}\n\nExtracted facts:\n{c(json.dumps(lead['signals'], ensure_ascii=False))}\n\n"
            f"Priority: {scored['tag']} (score {scored['score']}/100)\nScore reasons:\n- " + c("\n- ".join(scored["reasons"])) +
            f"\n\nAnalysis:\n{c(json.dumps(lead['analysis'], ensure_ascii=False))}\n\nCall history:\n{safety.wrap('call_notes', calls)}")


def _allowed(lead):
    """All text the customer or salesperson wrote for this lead: the only places a link may legitimately come from."""
    notes = "\n".join(str(c.get("notes", "")) for c in lead.get("calls", []))
    asked = "\n".join(m["text"] for m in lead.get("chat", []) if m.get("role") == "user")
    return f"{raw_text(lead['form'])}\n{notes}\n{asked}"


# ---------------- scoring view ----------------
def _activity(lead):
    act = dict(lead.get("activity") or {})
    last = _parse_iso(act.get("last_customer_reply_at")) or _parse_iso(lead["created_at"])
    act["days_since_customer_reply"] = (now() - last).days
    return act


def score_of(lead):
    return rescore(lead["signals"], _activity(lead))


def view(lead, summary=False):
    """Lead as the frontend sees it: stored data + live score + priority + waiting time.
    summary=True leaves out what the list screens never show (history, extracted facts) to keep it small."""
    created = _parse_iso(lead["created_at"])
    waiting = None if lead.get("responded_at") else int((now() - created).total_seconds() // 60)
    out = {k: v for k, v in lead.items() if k != "activity"}
    out.setdefault("stage", "new")
    out["ai_status"] = _status(lead)
    if out["ai_status"] == "failed" and not out.get("ai_error"):
        out["ai_error"] = FAILED_MSG
    out["waiting_minutes"] = waiting
    out["injection_suspected"] = bool((lead.get("safety") or {}).get("injection_suspected"))
    if is_ready(lead):
        s = score_of(lead)
        out.update(score=s["score"], tag=s["tag"], breakdown=s["breakdown"], reasons=s["reasons"], flags=s["flags"],
                   priority=(lead.get("override") or {}).get("to") or s["tag"])
    else:  # analysis didn't finish: no score yet
        out.update(score=None, tag="Pending", breakdown={}, reasons=[], flags=[], priority="Pending",
                   analysis={"summary": "", "intent": "", "key_requirements": [], "objections": [], "missing_info": [],
                             "next_action": {"action": "Run the analysis", "when": "", "why": ""},
                             "call_talking_points": [], "suggested_response": {"channel": "WhatsApp", "text": ""}})
    if summary:   # after scoring, which needs the extracted facts
        out.pop("history", None)
        out.pop("signals", None)
    return out


# ---------------- stages ----------------
def _advance(lead, to):
    """Move forward through the pipeline automatically; never backwards, and never out of Lost/Booked."""
    cur = lead.get("stage", "new")
    if to == "lost":
        lead["stage"] = "lost"
    elif cur not in ("lost", "booked") and STAGES.index(to) > STAGES.index(cur):
        lead["stage"] = to


def set_stage(store, lead, stage):
    if stage not in STAGES:
        raise ValidationError("Unknown stage.")

    def apply(l):
        l["stage"] = stage
        return {"history": [{"at": iso(), "why": f"Stage: {STAGE_LABEL[stage]}"}]}
    return view(_commit(store, lead["id"], apply, current=lead))


def set_phone(store, lead, phone):
    phone = clean_phone(phone)
    return view(_commit(store, lead["id"], lambda l: l["form"].update(phone=phone), current=lead))


# ---------------- AI analysis ----------------
def _cached_ai(store, system, user, temperature=0.0, refresh=False):
    """A real AI call, reusing a previous answer for identical input (see cache.py)."""
    return cache.cached(store, primary_model(), system, user, temperature,
                        lambda: call_json(system, user, temperature), refresh=refresh)


def _analyze(store, lead, refresh=False):
    """Extract facts -> score -> write analysis. Changes nothing; returns (fields to store, history entry)."""
    raw = _cached_ai(store, prompts.EXTRACT, lead_text(lead["form"]), refresh=refresh)
    signals = clean_signals(raw)
    suspect = raw.get("injection_attempt") is True or safety.looks_like_injection(raw_text(lead["form"]))
    probe = {**lead, "signals": signals}
    scored = score_of(probe)
    analysis = clean_analysis(_cached_ai(store, prompts.ANALYZE, full_context({**probe, "analysis": {}}, scored), 0.3, refresh=refresh),
                              allowed=raw_text(lead["form"]))
    if not analysis["summary"] or not analysis["next_action"]["action"]:
        raise AIError("bad_output", "analysis missing required fields")
    fields = {"signals": signals, "analysis": analysis, "ai_status": "ready", "ai_error": None,
              "safety": {"injection_suspected": suspect}}
    return fields, {"at": iso(), "score": scored["score"], "tag": scored["tag"], "why": "Analysed"}


def _store_analysis(store, lead_id, fields, entry):
    def apply(l):
        l.update(fields)
        return {"history": [entry]}
    return _commit(store, lead_id, apply)


def _fail(store, lead_id, message):
    """Mark an unanalysed lead as failed. A lead that already has an analysis is left alone."""
    try:
        _commit(store, lead_id, lambda l: None if is_ready(l) else l.update(ai_status="failed", ai_error=message))
    except LeadGone:
        pass


def create_lead(store, data):
    form = clean_form(data)
    lead = {"id": uuid.uuid4().hex[:12], "created_at": iso(), "form": form, "stage": "new",
            "ai_status": "analyzing", "analyzing_since": iso(), "ai_error": None,
            "safety": {"injection_suspected": safety.looks_like_injection(raw_text(form))},
            "signals": {}, "analysis": {}, "activity": {}, "responded_at": None, "override": None,
            "calls": [], "chat": [], "history": []}
    store.save(lead)  # saved first: a failure below never loses the lead. Others see it as "analysing".
    try:
        fields, entry = _analyze(store, lead)
    except AIError as e:
        _fail(store, lead["id"], str(e))
    except Exception:
        _fail(store, lead["id"], FAILED_MSG)
        raise
    else:
        store.update(lead["id"], {**fields, "history": Append([entry])})
        lead.update(fields)
        lead["history"] = [entry]
        return view(lead)
    fresh = store.get(lead["id"])
    if fresh is None:
        raise LeadGone()
    return view(fresh)


def reanalyze(store, lead):
    if _status(lead) == "analyzing":
        raise ValidationError(BEING_ANALYSED_MSG)
    was_ready = is_ready(lead)
    if not was_ready:   # let everyone else see that it is being worked on
        _commit(store, lead["id"], lambda l: l.update(ai_status="analyzing", analyzing_since=iso(), ai_error=None))
    try:
        fields, entry = _analyze(store, lead, refresh=True)   # an explicit retry always asks the AI again
    except Exception as e:
        _fail(store, lead["id"], str(e) if isinstance(e, AIError) else FAILED_MSG)
        raise
    return view(_store_analysis(store, lead["id"], fields, entry))


# ---------------- chat ----------------
def chat(store, lead, message):
    if not is_ready(lead):
        raise _not_ready_error(lead)
    message = str(message or "").strip()[:2000]
    if not message:
        raise ValidationError("Message is empty.")
    scored = score_of(lead)
    history = safety.clean("\n".join(f"{'Salesperson' if m['role'] == 'user' else 'Assistant'}: {m['text']}" for m in lead["chat"][-12:]))
    prompt = (f"{safety.wrap('lead_context', full_context(lead, scored))}\n\nCONVERSATION SO FAR\n{history or '(none)'}\n\n"
              f"Salesperson: {safety.clean(message)}")
    res = call_json(prompts.CHAT, prompt, temperature=0.4)
    answer = safety.strip_foreign_urls(str(res.get("answer", "")).strip()[:3000], _allowed(lead) + "\n" + message)
    reply = {"role": "assistant", "text": answer, "is_reply": bool(res.get("is_reply")), "at": iso()}
    if not reply["text"]:
        raise AIError("bad_output", "empty chat answer")
    _commit(store, lead["id"], lambda l: {"chat": [{"role": "user", "text": message, "at": iso()}, reply]})
    return reply


# ---------------- call log ----------------
def log_call(store, lead, outcome, notes):
    """Record a call. The AI adds a summary, fact updates and a follow-up, but if it's unavailable the
    call is still saved and the rule-based effects (stage, score events) still apply."""
    if outcome not in OUTCOMES:
        raise ValidationError("Unknown call outcome.")
    notes = str(notes or "").strip()[:3000]
    rule = OUTCOMES[outcome]
    ready = is_ready(lead)
    before = score_of(lead) if ready else None
    ai, ai_failed = {}, False
    if ready and (notes or outcome != "no_answer"):
        try:
            prompt = (f"{safety.wrap('lead_context', full_context(lead, before))}\n\nCALL OUTCOME: {outcome}\n"
                      f"{safety.wrap('call_notes', safety.clean(notes) or '(no notes)')}")
            ai = call_json(prompts.CALL_LOG, prompt, temperature=0.3)
        except AIError as e:
            log.warning("call log AI unavailable: %s", e.detail)
            ai_failed = True
    box = {}

    def apply(l):   # runs on a fresh copy of the lead, after the AI has answered
        l["responded_at"] = l.get("responded_at") or iso()
        if rule.get("stage"):
            _advance(l, rule["stage"])
        call = {"at": iso(), "outcome": outcome, "notes": notes, "summary": "", "score_before": None,
                "tag_before": None, "score_after": None, "tag_after": None}
        entries = []
        if ready and is_ready(l):
            act = l.setdefault("activity", {})
            su = ai.get("signal_updates") if isinstance(ai.get("signal_updates"), dict) else {}
            cleaned = clean_signals({**l["signals"], **su})
            l["signals"].update({k: v for k, v in cleaned.items() if k in su})
            l["signals"].update(rule.get("signals", {}))
            if rule.get("event"):
                act["events"] = sorted(set(act.get("events", []) + [rule["event"]]))
            if rule.get("not_interested"):
                act["not_interested"] = True
            if outcome != "no_answer":
                act["last_customer_reply_at"] = iso()
            if isinstance(ai.get("next_action"), dict):
                l["analysis"]["next_action"] = clean_analysis({"next_action": ai["next_action"]}, _allowed(l) + "\n" + notes)["next_action"]
            if isinstance(ai.get("follow_up"), dict):
                l["analysis"]["suggested_response"] = clean_analysis({"suggested_response": ai["follow_up"]}, _allowed(l) + "\n" + notes)["suggested_response"]
            after = score_of(l)
            call.update(summary=str(ai.get("call_summary", "")), score_before=before["score"], tag_before=before["tag"],
                        score_after=after["score"], tag_after=after["tag"])
            entries = [{"at": iso(), "score": after["score"], "tag": after["tag"], "why": f"Call: {outcome.replace('_', ' ')}"}]
        box["call"] = call
        return {"calls": [call], "history": entries} if entries else {"calls": [call]}

    fresh = _commit(store, lead["id"], apply)
    return {"call": box["call"], "lead": view(fresh), "ai_failed": ai_failed}


# ---------------- overrides / replies ----------------
def set_override(store, lead, to, reason):
    if not to:
        value = None
    else:
        if not is_ready(lead):
            raise _not_ready_error(lead)
        if to not in PRIORITIES:
            raise ValidationError("Priority must be Hot, Warm, Cold or Disqualified.")
        reason = str(reason or "").strip()[:300]
        if not reason:
            raise ValidationError("Please give a reason.")
        value = {"to": to, "from": score_of(lead)["tag"], "reason": reason, "at": iso()}
    return view(_commit(store, lead["id"], lambda l: l.update(override=value), current=lead))


def set_reply(store, lead, text):
    """Replace the suggested response (e.g. with a version rewritten in chat)."""
    if not is_ready(lead):
        raise _not_ready_error(lead)
    text = str(text or "").strip()[:3000]
    if not text:
        raise ValidationError("The reply is empty.")
    return view(_commit(store, lead["id"], lambda l: l["analysis"]["suggested_response"].update(text=text), current=lead))


def mark_responded(store, lead):
    def apply(l):
        l["responded_at"] = l.get("responded_at") or iso()
        _advance(l, "contacted")
    return view(_commit(store, lead["id"], apply, current=lead))
