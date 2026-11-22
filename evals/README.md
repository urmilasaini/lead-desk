# Evaluating the AI parts

There was nobody to label data, so the dataset is built so that labels do not need a person: the facts are chosen first and the
message is written around them (gold by construction), plus checks that need no label at all (invariance, monotonicity, attacks).

## What is here

| File | What | Size |
|---|---|---|
| `handwritten.py` | Natural and tricky leads, written by hand, gold facts and evidence phrases per lead | 29 |
| `generator.py` | Fact-first synthetic leads (seeded), with distractor numbers on the hard ones | 70 |
| `attacks.py` | Prompt-injection messages added to a normal enquiry (English, Hindi, Hinglish, encoded, delimiter escape, zero-width) | 15 |
| `chat_set.py` | Questions about a lead: answerable, unanswerable, off topic, attack | 37 |
| `metamorphic.py` | Transforms: noise that must change nothing, and positives that must not lower the score | 154 |
| `GUIDELINES.md` | The rules each gold value follows | |
| `data/*.jsonl` | Built output. Regenerate with `python -m evals.build` (deterministic) | |

`python -m unittest evals.test_dataset` checks the data itself: unique ids, every evidence phrase really is in the text,
all gold fields present, languages and formats covered, split sizes, build is deterministic.

## What gets measured

| Part | Check | Needs a model call? |
|---|---|---|
| Extraction | Field accuracy against gold (location, type, budget, timeline, loan, flags) | the app's AI step |
| Scoring | Tag/score for the extracted facts through `rules.py` | no |
| Analysis and reply | Does not invent facts (numbers, banks, areas not in the lead), no foreign links, keeps the score's tag, length limits | no, programmatic |
| Chat | Answerable questions contain the answer; unanswerable ones admit it; off-topic and attacks are refused | no, string checks |
| Robustness | Invariant transforms keep facts and tag; monotonic ones never lower the score | the app's AI step |
| Injection | Attack leads: not Hot, no evil link, no leaked instructions, injection flagged | the app's AI step |
| Consistency | The same lead run 3 times gives the same tag | the app's AI step |

An LLM judge (a different model family from the one under test, binary pass/fail rubrics) is only for tone and usefulness of the
suggested reply, and would need checking on a few hand-graded examples first. It is not used for anything above.

## Split

About a quarter of the leads are `heldout`. Tune prompts against `dev`; run `heldout` once before reporting a number.

## Honest limits

- The labels were written by the same assistant that helped build the app, from rules written first. That makes them consistent, not independent.
  Scores here are optimistic and say nothing about whether real salespeople would prioritise the same way.
- Synthetic leads are cleaner than real ones. Hand-written ones reduce that, but 29 is small.
- The score weights in `rules.py` are not evaluated here, only that they are applied correctly. Judging them needs conversion outcomes.
- Gemini free-tier quotas limit how many leads fit in one run, so the metamorphic set is meant to be sampled.
