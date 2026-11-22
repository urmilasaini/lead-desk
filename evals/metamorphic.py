"""Metamorphic transforms: change a lead in a way that must (or must not) change the AI output.

invariant  - facts and tag must stay the same (case, whitespace, emoji, polite filler, greeting)
monotonic  - the score must not go down when a positive fact is added (verified loan, shorter timeline, visit request)
"""

FILLER = "Hello sir, hope you are doing well. Thank you for your time. "


def upper(m): return m.upper()
def spaced(m): return "  ".join(m.split()) + "\n\n"
def polite(m): return FILLER + m + " Regards."
def emoji(m): return m + " 🙏🏠😊"
def typo_free_lower(m): return m.lower()


INVARIANT = {"upper": upper, "extra_whitespace": spaced, "polite_filler": polite, "emoji": emoji, "lowercase": typo_free_lower}

# Appending a clear positive fact. Applied only to leads that do not already state it (checked via gold).
MONOTONIC = [
    ("add_sanctioned_loan", " My home loan is already sanctioned.", lambda g: g["loan_status"] in ("unknown", "none", "in_process")),
    ("add_site_visit", " I can visit the site this Saturday.", lambda g: not g["visit_or_call_requested"]),
    ("add_short_timeline", " I need to move in within 1 month.", lambda g: g["timeline_months"] in (None, 6, 12)),
]


def invariants(lead):
    for name, fn in INVARIANT.items():
        f = dict(lead["form"])
        f["message"] = fn(f["message"])
        yield {"id": f"{lead['id']}~{name}", "parent": lead["id"], "kind": "invariant", "transform": name, "form": f}


def monotonic(lead):
    for name, add, applies in MONOTONIC:
        if applies(lead["gold"]):
            f = dict(lead["form"])
            f["message"] = f["message"] + add
            yield {"id": f"{lead['id']}~{name}", "parent": lead["id"], "kind": "monotonic", "transform": name, "form": f}
