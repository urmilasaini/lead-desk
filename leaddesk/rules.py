"""Scoring rubric. AI extracts facts; this code turns them into a score.

Budget fit 20 | Financial 15 | Timeline 20 | Need 15 | Intent 15 | Authority 10 | Engagement 5 = 100
Tags: 70+ Hot | 40-69 Warm | <40 Cold | spam / out-of-area -> Disqualified

Design rules:
- "Not mentioned" is NOT "no": unknown facts get a neutral middle score, never 0.
  Only an explicit negative (e.g. "haven't applied for a loan") scores 0.
- Objections are never scored (they are often buying signals); they go to the analysis.
- All weights live in the tables below so they can be tuned in one place.
- Prices live in market.json so they can be updated without touching code.
"""

import json
from pathlib import Path

# ---------------- tunable tables ----------------
FINANCIAL = {"sanctioned": 15, "pre_approved": 15, "own_funds": 15,
             "in_process": 9, "down_payment_ready": 9,
             "unknown": 5, "none": 0}
TIMELINE = [(1, 20), (3, 16), (6, 10), (12, 5)]      # (<= months, points); beyond 12 -> 2
TIMELINE_UNKNOWN, TIMELINE_BEYOND = 2, 2
AUTHORITY = {"self": 10, "joint_present": 8, "unknown": 5, "needs_others": 2}
BUDGET_RATIO = [(1.0, 20), (0.85, 14), (0.7, 8)]     # budget / market_min -> points; below -> 2
BUDGET_UNKNOWN, BUDGET_NO_REFERENCE = 4, 12
HOT, WARM = 70, 40
BORDERLINE_MARGIN = 3                                # within 3 points of a cutoff -> "borderline" flag
HIGH_VALUE_LAKHS = 300                               # 3 Cr+ never drops below Warm

# Re-scoring after the first inquiry (activity over time)
SILENCE_PENALTY = [(14, -25), (7, -15), (3, -5)]     # days since customer last replied -> points
EVENT_POINTS = {
    "connected": 3,
    "visit_booked": 5,
    "site_visit_done": 10,
    "negotiating": 10,
    "requested_documents": 5,
}
# -------------------------------------------------

_MARKET = json.loads((Path(__file__).parent / "market.json").read_text(encoding="utf-8"))
SERVICE_AREAS = list(_MARKET["service_areas"])
CITY_NAMES = ("bangalore", "bengaluru")


def market_range(location, prop_type):
    return _MARKET["service_areas"].get(location, {}).get(prop_type) or _MARKET["type_fallback"].get(prop_type)


def budget_fit(s):
    budget = s.get("budget_lakhs")
    if budget is None:
        return BUDGET_UNKNOWN, "no budget given"
    rng = market_range(s.get("primary_location"), s.get("property_type"))
    if rng is None:
        return BUDGET_NO_REFERENCE, f"{budget}L, no market reference"
    low = rng[0]
    for ratio, pts in BUDGET_RATIO:
        if budget / low >= ratio:
            label = "fits market" if ratio == 1.0 else "below market"
            return pts, f"{budget}L {label} ({low}L+)"
    return 2, f"{budget}L unrealistic (market {low}L+)"


def financial(s):
    loan = s.get("loan_status") or "unknown"
    return FINANCIAL.get(loan, FINANCIAL["unknown"]), f"funds: {loan.replace('_', ' ')}"


def timeline(s):
    m = s.get("timeline_months")
    if m is None:
        return TIMELINE_UNKNOWN, "not given"
    for limit, pts in TIMELINE:
        if m <= limit:
            return pts, f"~{m} month(s)"
    return TIMELINE_BEYOND, "12+ months"


def need(s):
    pts, notes = 0, []
    if s.get("property_type"):
        pts += 4
        notes.append(s["property_type"])
    if s.get("primary_location") in SERVICE_AREAS and not s.get("multiple_locations"):
        pts += 3
        notes.append(s["primary_location"])
    if s.get("urgency_reason"):
        pts += 5
        notes.append(f"reason: {s['urgency_reason']}")
    if (s.get("concrete_details_count") or 0) >= 3:
        pts += 3
        notes.append("specific preferences")
    return min(pts, 15), ", ".join(notes) or "unclear need"


def intent(s):
    pts, notes = 0, []
    if s.get("already_visited"):
        pts += 8
        notes.append("already visited")
    elif s.get("visit_or_call_requested"):
        pts += 6
        notes.append("asked for visit/call")
    if s.get("ready_to_book"):
        pts += 5
        notes.append("ready to book")
    q = len(s.get("specific_questions") or [])
    if q:
        pts += min(2 * q, 4)
        notes.append(f"{q} question(s)")
    return min(pts, 15), ", ".join(notes) or "no action signals"


def authority(s):
    dm = s.get("decision_maker") or "unknown"
    return AUTHORITY.get(dm, AUTHORITY["unknown"]), dm.replace("_", " ")


def engagement(s):
    n = s.get("concrete_details_count") or 0
    return (5 if n >= 4 else 3 if n >= 2 else 1), f"{n} concrete details"


def _tag(score):
    return "Hot" if score >= HOT else "Warm" if score >= WARM else "Cold"


def _disqualified(reason):
    return {"score": 0, "tag": "Disqualified", "breakdown": {}, "reasons": [reason], "flags": []}


def _finish(score, breakdown, reasons, flags, budget_lakhs):
    if (budget_lakhs or 0) >= HIGH_VALUE_LAKHS:
        flags.append("high_value")
        score = max(score, WARM)
    score = max(0, min(100, score))
    if any(abs(score - cut) < BORDERLINE_MARGIN for cut in (HOT, WARM)):
        flags.append("borderline")
    return {"score": score, "tag": _tag(score), "breakdown": breakdown, "reasons": reasons, "flags": flags}


def score_lead(signals):
    """Score a new inquiry from extracted signals."""
    if signals.get("is_spam"):
        return _disqualified("Spam / not a buyer")
    loc = signals.get("primary_location")
    city_only = loc is None or loc.lower() in CITY_NAMES
    if loc not in SERVICE_AREAS and not city_only and not signals.get("multiple_locations"):
        return _disqualified(f"Outside service area: {loc}")

    parts = {"budget_fit": budget_fit(signals), "financial": financial(signals), "timeline": timeline(signals),
             "need": need(signals), "intent": intent(signals), "authority": authority(signals),
             "engagement": engagement(signals)}
    score = sum(p for p, _ in parts.values())
    return _finish(score, {k: p for k, (p, _) in parts.items()},
                   [f"{k}: {why}" for k, (_, why) in parts.items()], [], signals.get("budget_lakhs"))


def rescore(signals, activity):
    """Update a lead's score after the first inquiry.

    activity = {"days_since_customer_reply": int, "events": ["site_visit_done", ...],
                "not_interested": bool}
    """
    base = score_lead(signals)
    if base["tag"] == "Disqualified":
        return base
    if activity.get("not_interested"):
        return _disqualified("Customer said not interested")
    adj, reasons = 0, list(base["reasons"])
    days = activity.get("days_since_customer_reply") or 0
    for limit, pts in SILENCE_PENALTY:
        if days >= limit:
            adj += pts
            reasons.append(f"activity: silent {days} days ({pts})")
            break
    for ev in activity.get("events") or []:
        if ev in EVENT_POINTS:
            adj += EVENT_POINTS[ev]
            reasons.append(f"activity: {ev.replace('_', ' ')} (+{EVENT_POINTS[ev]})")
    breakdown = {**base["breakdown"], "activity": adj}
    flags = [f for f in base["flags"] if f not in ("borderline", "high_value")]
    raw = sum(base["breakdown"].values()) + adj  # raw sum, before the high-value floor
    return _finish(raw,
                   breakdown, reasons, flags, signals.get("budget_lakhs"))
