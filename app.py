"""Lead Desk: AI lead prioritization for real-estate salespeople.

Flask app: serves the frontend (static/) and a small JSON API.
Run locally:  python app.py   ->  http://localhost:5000

Error handling rule: every error response is {"error": "<plain-language message>"}.
Technical details (provider, model, credentials, stack traces) are written to the server log only.
"""

import hashlib
import logging
import os
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

try:  # load .env for local dev; on Vercel env vars come from the dashboard
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from leaddesk import service
from leaddesk.ai import AIError, is_configured
from leaddesk.service import ValidationError
from leaddesk.store import LeadGone, StorageError, make_store

logging.basicConfig(level=logging.INFO)
STATIC = Path(__file__).parent / "static"
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 1_000_000
store = make_store()


def _asset_version():
    """Short hash of the scripts and styles. It goes in their URLs, so browsers can cache them for a
    year and still pick up a new deploy immediately."""
    h = hashlib.sha256()
    for f in sorted(STATIC.glob("*")):
        if f.suffix in (".js", ".css"):
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


ASSET_VERSION = _asset_version()
VERSIONED_ASSETS = ("/static/styles.css", "/static/lib.js", "/static/app.js")


def _index_html():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for url in VERSIONED_ASSETS:
        html = html.replace(f'"{url}"', f'"{url}?v={ASSET_VERSION}"')
    return html


_storage_check = {"at": 0.0, "ok": False}


def _storage_ready():
    """True if storage is configured and answers. The probe runs at most once every 30 seconds."""
    if not store.ready:
        return False
    if time.time() - _storage_check["at"] > 30:
        try:
            _storage_check["ok"] = bool(store.ping())
        except StorageError:
            _storage_check["ok"] = False
        _storage_check["at"] = time.time()
    return _storage_check["ok"]


HTTP_MESSAGES = {404: "We couldn't find that.", 405: "That action isn't supported.", 413: "That's too large to send."}


def _lead_or_404(lead_id):
    lead = store.get(lead_id)
    if not lead or not service.is_valid(lead):
        return None, (jsonify(error=HTTP_MESSAGES[404]), 404)
    return lead, None


def _body():
    return request.get_json(silent=True) or {}


# ---------------- errors ----------------
@app.errorhandler(ValidationError)
def _bad_request(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(AIError)
def _ai_error(e):
    app.logger.warning("AI error (%s): %s", e.kind, e.detail)
    return jsonify(error=str(e), retryable=True), 503


@app.errorhandler(LeadGone)
def _lead_gone(e):
    return jsonify(error=HTTP_MESSAGES[404]), 404


@app.errorhandler(StorageError)
def _storage_error(e):
    app.logger.error("Storage error: %s", e.detail)
    return jsonify(error=str(e), retryable=True), 503


@app.errorhandler(Exception)
def _unexpected(e):
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify(error=HTTP_MESSAGES.get(e.code, "That request didn't work.")), e.code
        return e
    app.logger.exception("Unhandled error")
    return jsonify(error="Something went wrong on our side. Please try again."), 500


# ---------------- pages ----------------
@app.get("/")
def index():
    resp = app.response_class(_index_html(), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache"   # always revalidate the page; its assets are versioned
    return resp


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(STATIC, name)


@app.after_request
def _cache_headers(resp):
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"   # lead data is private and changes constantly
    elif request.path.startswith("/static/") and request.args.get("v") and resp.status_code == 200:
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


# ---------------- API ----------------
@app.get("/api/health")
def health():
    return jsonify(ok=True, ai_ready=is_configured(), storage_ready=_storage_ready())


@app.get("/api/leads")
def list_leads():
    docs = store.all()
    leads = [service.view(l, summary=True) for l in docs if service.is_valid(l)]
    if len(leads) < len(docs):
        app.logger.warning("Ignored %d document(s) in the leads collection that this app didn't write", len(docs) - len(leads))
    order = {"Hot": 0, "Warm": 1, "Cold": 2, "Disqualified": 3, "Pending": 4}
    leads.sort(key=lambda l: (order[l["priority"]], -(l["score"] or 0)))
    return jsonify(leads=leads)


@app.post("/api/leads")
def create_lead():
    return jsonify(lead=service.create_lead(store, _body())), 201


@app.get("/api/leads/<lead_id>")
def get_lead(lead_id):
    lead, err = _lead_or_404(lead_id)
    return err or jsonify(lead=service.view(lead))


@app.patch("/api/leads/<lead_id>")
def update_lead(lead_id):
    lead, err = _lead_or_404(lead_id)
    return err or jsonify(lead=service.set_phone(store, lead, _body().get("phone")))


@app.delete("/api/leads/<lead_id>")
def delete_lead(lead_id):
    store.delete(lead_id)
    return jsonify(ok=True)


@app.post("/api/leads/<lead_id>/reanalyze")
def reanalyze(lead_id):
    lead, err = _lead_or_404(lead_id)
    return err or jsonify(lead=service.reanalyze(store, lead))


@app.post("/api/leads/<lead_id>/chat")
def chat(lead_id):
    lead, err = _lead_or_404(lead_id)
    return err or jsonify(reply=service.chat(store, lead, _body().get("message")))


@app.post("/api/leads/<lead_id>/calls")
def log_call(lead_id):
    lead, err = _lead_or_404(lead_id)
    body = _body()
    return err or jsonify(service.log_call(store, lead, body.get("outcome"), body.get("notes")))


@app.post("/api/leads/<lead_id>/override")
def override(lead_id):
    lead, err = _lead_or_404(lead_id)
    body = _body()
    return err or jsonify(lead=service.set_override(store, lead, body.get("to"), body.get("reason")))


@app.post("/api/leads/<lead_id>/stage")
def stage(lead_id):
    lead, err = _lead_or_404(lead_id)
    return err or jsonify(lead=service.set_stage(store, lead, _body().get("stage")))


@app.post("/api/leads/<lead_id>/reply")
def reply(lead_id):
    lead, err = _lead_or_404(lead_id)
    return err or jsonify(lead=service.set_reply(store, lead, _body().get("text")))


@app.post("/api/leads/<lead_id>/responded")
def responded(lead_id):
    lead, err = _lead_or_404(lead_id)
    return err or jsonify(lead=service.mark_responded(store, lead))


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
