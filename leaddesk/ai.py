"""AI wrapper. Every AI call in the app goes through call_json().

The model is asked for JSON only, so the app gets structured data it can store, score and render.

Error handling rule: what the user sees is always a short, plain message ("The AI assistant is busy,
try again in a few seconds"). Provider names, model names, keys and raw exception text go to the
server log only, never into an API response.
"""

import json
import logging
import os
import re
import time

log = logging.getLogger("leaddesk.ai")

DEFAULT_MODEL = "gemini-3.5-flash-lite"   # fast, and its answers matched the larger model in testing
MODEL = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
DEFAULT_FALLBACK = "gemini-3.5-flash"   # used only if the main model's quota runs out; its quota is separate

USER_MESSAGES = {
    "busy": "The AI assistant is busy right now. Please wait a few seconds and try again.",
    "limit": "The AI assistant has reached its usage limit for now. Your work is saved, so please try again later.",
    "unavailable": "The AI assistant isn't available right now. Please try again in a moment.",
    "bad_output": "The AI sent back something we couldn't use. Please try again.",
}
RETRY_WAIT = {"busy": (3, 6), "unavailable": (1.5, 3), "bad_output": (1, 1), "limit": (0, 0)}
MAX_RETRIES = 2
TIME_BUDGET_S = 28      # stop retrying after this, so a request stays inside serverless time limits
CALL_TIMEOUT_MS = 20000

_client = None


def _human(seconds):
    if seconds >= 5400:
        return f"about {round(seconds / 3600)} hours"
    if seconds >= 90:
        return f"about {round(seconds / 60)} minutes"
    return ""


class AIError(Exception):
    """AI call failed. str(e) is always safe to show to the user; .detail is for logs only."""

    def __init__(self, kind="unavailable", detail="", wait_s=None):
        self.kind = kind if kind in USER_MESSAGES else "unavailable"
        self.detail = detail
        msg = USER_MESSAGES[self.kind]
        if self.kind == "limit" and wait_s and _human(wait_s):
            msg = msg.replace("please try again later.", f"please try again in {_human(wait_s)}.")
        super().__init__(msg)


def is_configured():
    return bool(os.environ.get("GEMINI_API_KEY"))


def _get_client():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                               http_options=types.HttpOptions(timeout=CALL_TIMEOUT_MS))
    return _client


def _retry_seconds(text):
    """Seconds the provider asks us to wait, parsed from its error text (0 if absent)."""
    m = re.search(r"retrydelay'?\"?:\s*'?\"?(\d+(?:\.\d+)?)s", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", text, re.I)
    if m and any(m.groups()):
        h, mi, sec = (float(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + sec
    return 0


def _classify(exc):
    """Map any exception to one of: limit (daily quota), busy (short rate limit), bad_output, unavailable."""
    if isinstance(exc, ValueError):  # includes json.JSONDecodeError
        return "bad_output"
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = str(exc)
    low = text.lower()
    if code == 429 or "resource_exhausted" in low or "rate limit" in low or "quota" in low:
        # a daily quota won't recover in seconds, so don't retry it; a per-minute limit will
        if "perday" in low.replace(" ", "").replace("_", "") or "per day" in low or _retry_seconds(text) > 120:
            return "limit"
        return "busy"
    return "unavailable"


def _parse(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)  # tolerate ```json fences
        if match:
            return json.loads(match.group(0))
        raise


def _thinking(types, model):
    """Keep reasoning short so replies are fast. Gemini 2.5 takes a token budget; Gemini 3+ takes a level."""
    if model.startswith("gemini-2.5"):
        return types.ThinkingConfig(thinking_budget=0)
    return types.ThinkingConfig(thinking_level="LOW")


def _models():
    """The configured model first, then fallbacks (env GEMINI_FALLBACK_MODELS, comma separated; set it empty to disable)."""
    extra = [m.strip() for m in os.environ.get("GEMINI_FALLBACK_MODELS", DEFAULT_FALLBACK).split(",") if m.strip()]
    return [os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)] + extra


def primary_model():
    return _models()[0]


def call_json(system, user, temperature=0.0):
    """Send one prompt and return the parsed JSON object, or raise AIError."""
    if not is_configured():
        log.error("AI is not configured on the server (missing credentials)")
        raise AIError("unavailable", "not configured")

    start, last = time.monotonic(), None
    for model in _models():
        attempt = 0
        while True:
            try:
                from google.genai import types
                config = types.GenerateContentConfig(
                    system_instruction=system, temperature=temperature,
                    response_mime_type="application/json",
                    thinking_config=_thinking(types, model),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                )
                resp = _get_client().models.generate_content(model=model, contents=user, config=config)
                data = _parse(resp.text)
                if not isinstance(data, dict):
                    raise ValueError("not a JSON object")
                return data
            except Exception as e:
                kind = _classify(e)
                last = AIError(kind, f"{type(e).__name__}: {e}", _retry_seconds(str(e)))
                log.warning("AI call failed (%s, model %s, attempt %d): %s", kind, model, attempt + 1, str(e)[:300])
                code = getattr(e, "code", None)
                if kind == "limit" or code == 404:
                    break                      # quota used up / model not available: try the next model, if any
                if code in (400, 401, 403):
                    raise last                 # a configuration problem: retrying can't fix it
                wait = RETRY_WAIT[kind][min(attempt, 1)]
                attempt += 1
                if attempt > MAX_RETRIES or time.monotonic() - start + wait > TIME_BUDGET_S:
                    break                      # give up on this model; the next one (if any) gets a turn
                time.sleep(wait)
        if time.monotonic() - start > TIME_BUDGET_S:
            break                              # out of time: don't start another model
    raise last
