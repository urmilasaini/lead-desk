"""Build the evaluation dataset files in evals/data/ (deterministic).  python -m evals.build"""
import json
import random
from pathlib import Path

from evals import attacks, chat_set, generator, metamorphic
from evals.handwritten import HAND

DATA = Path(__file__).parent / "data"
SEED = 7


def write(name, rows):
    DATA.mkdir(exist_ok=True)
    with (DATA / name).open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build():
    synth = generator.generate(70, SEED)
    leads = HAND + synth
    # Fixed split: roughly every 4th lead is held out. Look at dev while tuning; run held-out only before reporting.
    rng = random.Random(SEED)
    held = set(rng.sample([l["id"] for l in leads], k=len(leads) // 4))
    for l in leads:
        l["split"] = "heldout" if l["id"] in held else "dev"
    meta = [m for l in leads if l["gold"].get("primary_location") or l["category"] == "natural"
            for m in list(metamorphic.invariants(l)) + list(metamorphic.monotonic(l))
            if l["id"].startswith(("H", "S0")) and int(l["id"][1:]) <= 12]
    write("leads.jsonl", leads)
    write("attacks.jsonl", attacks.attack_leads())
    write("chat.jsonl", chat_set.chat_cases())
    write("metamorphic.jsonl", meta)
    return dict(leads=len(leads), heldout=len(held), attacks=len(attacks.ATTACKS), chat=len(chat_set.CHAT), metamorphic=len(meta))


if __name__ == "__main__":
    print(build())
