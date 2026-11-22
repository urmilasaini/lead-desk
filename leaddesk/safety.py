"""Treat everything a customer wrote as data, never as instructions (prompt-injection defence).

What a customer message can try, and what stops it:
  - closing our <customer_data> tags to escape the data block   -> neutralize() removes tag look-alikes
  - telling the AI to ignore its rules / score the lead high     -> prompts say the data is untrusted (prompts.SECURITY),
                                                                    the score is computed in code from validated facts,
                                                                    and looks_like_injection() flags the lead for the salesperson
  - putting a link or number in the AI's reply (e.g. a payment link) -> strip_foreign_urls() removes links the customer didn't write
  - making the AI return absurd facts (budget of 500 crore)      -> service.clean_signals() validates enums and ranges
This cannot make a language model perfectly immune, so the aim is to contain the damage: a message can't change
the score by instruction, can't smuggle in links, and the salesperson is warned when one tries.
"""

import re

_TAG_RE = re.compile(r"<\s*/?\s*(?:customer[_\s-]*data|lead[_\s-]*context|call[_\s-]*notes|system|assistant|instructions?)\s*/?\s*>", re.I)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")  # control and bidi-override characters
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s)>\]\"']+", re.I)

_INJECTION = [re.compile(p, re.I | re.M) for p in (
    r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b(?:instructions?|prompts?|rules?|guidelines?|directives?)\b",
    r"\b(?:reveal|show|print|repeat|output|leak|tell)\b[^.\n]{0,20}\b(?:your|the (?:system|hidden|initial|original)|system)\s+(?:prompt|instructions?)\b",
    r"\bsystem\s*prompt\b",
    r"\byou are now\b[^.\n]{0,25}\b(?:mode|assistant|ai|bot|chatgpt|gpt|persona|character)\b",
    r"\bnew instructions?\s*[:\-]",
    r"\b(?:developer|admin|god|dan)\s+mode\b",
    r"\bjailbreak",
    r"</?\s*(?:system|assistant|instructions?|customer[_\s-]*data|lead[_\s-]*context|call[_\s-]*notes)\b",
    r"^\s*(?:system|assistant)\s*:",
    r"\b(?:mark|rate|score|classify|label|set|treat)\b[^.\n]{0,30}\b(?:this|the|my)\s+lead\b[^.\n]{0,30}\b(?:hot|100|top|highest|priority)\b",
)]


def clean(text):
    """Make untrusted text safe to place inside a data block."""
    return _TAG_RE.sub("", _CTRL_RE.sub("", str(text if text is not None else "")))


def wrap(tag, text):
    """Put already-cleaned text in a named data block."""
    return f"<{tag}>\n{text}\n</{tag}>"


def looks_like_injection(text):
    """True if the text contains phrases aimed at the AI rather than at a salesperson."""
    text = str(text or "")
    return any(p.search(text) for p in _INJECTION)


def strip_foreign_urls(text, allowed_text=""):
    """Replace links that don't appear in allowed_text (what the customer actually wrote)."""
    allowed = {u.lower().rstrip(".,;:!?") for u in _URL_RE.findall(str(allowed_text or ""))}

    def sub(m):
        return m.group(0) if m.group(0).lower().rstrip(".,;:!?") in allowed else "[link removed]"
    return _URL_RE.sub(sub, str(text or ""))
