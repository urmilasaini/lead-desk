"""Fact-first synthetic leads: pick the facts, then write the message. Gold labels are true by construction.

Deterministic for a given seed. Every phrase that carries a fact is recorded in `evidence`.
"""
import random

AREAS = {
    "Whitefield": ["Whitefield", "whitefield", "ITPL side, Whitefield"],
    "Sarjapur Road": ["Sarjapur Road", "Sarjapur", "sarjapur rd"],
    "Hebbal": ["Hebbal", "hebbal"],
    "Electronic City": ["Electronic City", "E-City", "electronic city"],
    "Bannerghatta Road": ["Bannerghatta Road", "Bannerghatta rd"],
    "Devanahalli": ["Devanahalli", "devanahalli"],
}
TYPES = {"1BHK": ["1BHK", "1 bhk", "one bhk"], "2BHK": ["2BHK", "2 bhk", "two bhk"], "3BHK": ["3BHK", "3 bhk", "three bhk"],
         "4BHK": ["4BHK", "4 bhk"], "villa": ["villa"], "plot": ["plot"]}
BUDGET_BY_TYPE = {"1BHK": (35, 60), "2BHK": (60, 120), "3BHK": (100, 220), "4BHK": (250, 500), "villa": (200, 450), "plot": (40, 150)}
TIMELINES = [(1, ["within a month", "next month", "in 4 weeks"]), (2, ["in 2 months", "within two months"]),
             (3, ["in 3 months", "within 3 months"]), (6, ["in 6 months", "within six months"]), (12, ["next year", "within a year"])]
LOANS = {"sanctioned": ["loan is sanctioned", "loan already approved"], "pre_approved": ["I have a pre-approved loan"],
         "in_process": ["loan application is in process"], "own_funds": ["paying from my own savings, no loan"],
         "none": ["haven't applied for a loan yet"]}
REASONS = ["my lease ends soon", "I got transferred to Bangalore", "my kid's school is nearby", "we are expecting a baby", "I am relocating for a new job"]
CALL = ["Please call me tomorrow.", "Can we do a site visit this Saturday?", "Call me after 6 pm."]
BOOK = ["I am ready to book this week.", "Send the booking form, we want to finalise."]
VISITED = ["We visited the site last weekend and liked it."]
OTHERS = ["I need to ask my wife before deciding.", "I will check with my father first."]
JOINT = ["My wife and I are both decided."]


def _budget_text(lakhs, rng):
    if lakhs >= 100 and rng.random() < 0.7:
        cr = lakhs / 100
        s = f"{cr:g}"
        return rng.choice([f"{s} Cr", f"{s} crore", f"Rs {s}cr"]), s
    return rng.choice([f"{lakhs} lakhs", f"{lakhs}L", f"{lakhs} lakh"]), str(lakhs)


def make(i, rng, difficulty="medium"):
    area = rng.choice(list(AREAS))
    ptype = rng.choice(list(TYPES))
    lo, hi = BUDGET_BY_TYPE[ptype]
    lakhs = rng.randrange(lo, hi + 1, 5)
    gold = dict(is_spam=False, primary_location=area, multiple_locations=False, property_type=ptype, budget_lakhs=lakhs, budget_range=None,
                timeline_months=None, has_reason=False, loan_status="unknown", visit_or_call_requested=False, already_visited=False,
                ready_to_book=False, decision_maker="unknown", n_questions=0, concrete_details_count=0)
    ev = {"primary_location": rng.choice(AREAS[area]), "property_type": rng.choice(TYPES[ptype])}
    btxt, _ = _budget_text(lakhs, rng)
    ev["budget_lakhs"] = btxt
    parts = [f"Looking for a {ev['property_type']} in {ev['primary_location']}.", f"Budget {btxt}."]
    if rng.random() < 0.7:
        months, phr = rng.choice(TIMELINES)
        gold["timeline_months"] = months
        ev["timeline_months"] = rng.choice(phr)
        parts.append(f"Want to buy {ev['timeline_months']}.")
    if rng.random() < 0.5:
        ls = rng.choice(list(LOANS))
        gold["loan_status"] = ls
        ev["loan_status"] = rng.choice(LOANS[ls])
        parts.append(ev["loan_status"][0].upper() + ev["loan_status"][1:] + ".")
    if rng.random() < 0.35:
        gold["has_reason"] = True
        ev["has_reason"] = rng.choice(REASONS)
        parts.append(ev["has_reason"][0].upper() + ev["has_reason"][1:] + ".")
    if rng.random() < 0.4:
        gold["visit_or_call_requested"] = True
        ev["visit_or_call_requested"] = rng.choice(CALL)
        parts.append(ev["visit_or_call_requested"])
    if rng.random() < 0.15:
        gold["ready_to_book"] = True
        ev["ready_to_book"] = rng.choice(BOOK)
        parts.append(ev["ready_to_book"])
    if rng.random() < 0.15:
        gold["already_visited"] = True
        ev["already_visited"] = rng.choice(VISITED)
        parts.append(ev["already_visited"])
    r = rng.random()
    if r < 0.2:
        gold["decision_maker"] = "needs_others"
        ev["decision_maker"] = rng.choice(OTHERS)
        parts.append(ev["decision_maker"])
    elif r < 0.3:
        gold["decision_maker"] = "joint_present"
        ev["decision_maker"] = rng.choice(JOINT)
        parts.append(ev["decision_maker"])
    # Distractors: numbers and names that are NOT facts.
    if difficulty == "hard":
        parts.insert(1, rng.choice(["My number is 98450 12345.", "Ref: ad #4471.", "Saw your 1200 sqft listing."]))
    msg = " ".join(parts)
    gold["concrete_details_count"] = sum(bool(gold[k]) for k in ("primary_location", "property_type", "budget_lakhs", "timeline_months")) \
        + (gold["loan_status"] in ("sanctioned", "pre_approved", "in_process")) + gold["has_reason"]
    return {"id": f"S{i:03d}", "source": "synthetic", "category": "synthetic", "lang": "en", "format": "plain",
            "form": {"name": f"Synthetic {i}", "phone": "", "location": "", "requirement": "", "budget": "", "timeline": "", "message": msg},
            "gold": gold, "evidence": ev, "ambiguous_fields": [], "notes": f"difficulty={difficulty}"}


def generate(n=70, seed=7):
    rng = random.Random(seed)
    return [make(i + 1, rng, "hard" if i % 4 == 3 else "medium") for i in range(n)]
