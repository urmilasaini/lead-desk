/* Pure helpers (no DOM): CSV parsing, phone cleanup, WhatsApp links.
   Loaded by the page as window.Lib and by the Node tests with require(). */
(function (root) {
  "use strict";

  const MAX_IMPORT_ROWS = 50;

  /** Parse CSV text into rows of strings. Handles quotes, "" escapes, commas/newlines inside quotes,
      a BOM, and comma / semicolon / tab delimiters (Excel exports vary by region). */
  function parseCSV(text) {
    text = String(text || "").replace(/^﻿/, "");
    const firstLine = text.split(/\r?\n/, 1)[0] || "";
    const count = ch => firstLine.split(ch).length - 1;
    const delim = [["\t", count("\t")], [";", count(";")], [",", count(",")]].sort((a, b) => b[1] - a[1])[0][0];
    const rows = []; let row = [], field = "", quoted = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (quoted) {
        if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false; }
        else field += c;
      } else if (c === '"') quoted = true;
      else if (c === delim) { row.push(field); field = ""; }
      else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(field); field = "";
        if (row.some(x => x.trim() !== "")) rows.push(row);
        row = [];
      } else field += c;
    }
    row.push(field);
    if (row.some(x => x.trim() !== "")) rows.push(row);
    return rows;
  }

  const ALIASES = {
    name: ["name", "customername", "leadname", "fullname"],
    phone: ["phone", "mobile", "phonenumber", "mobilenumber", "whatsapp", "contact"],
    location: ["location", "area", "locality", "city"],
    requirement: ["requirement", "propertyrequirement", "property", "need", "propertytype", "bhk"],
    budget: ["budget", "pricerange"],
    timeline: ["timeline", "buyingtimeline", "timeframe", "when"],
    message: ["message", "customermessage", "inquiry", "enquiry", "notes", "chat", "transcript", "remarks", "comments"],
  };
  const norm = s => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");

  /** CSV text -> {leads, skipped, truncated, error}. Needs at least name and message columns. */
  function leadsFromCSV(text) {
    const rows = parseCSV(text);
    if (rows.length < 2) return { leads: [], skipped: [], truncated: 0, error: "The file looks empty. Add a header row and at least one lead." };
    const header = rows[0].map(norm), col = {};
    for (const [field, names] of Object.entries(ALIASES)) col[field] = header.findIndex(h => names.includes(h));
    if (col.name < 0 || col.message < 0)
      return { leads: [], skipped: [], truncated: 0, error: "We need at least a “name” and a “message” column. Download the sample file to see the format." };
    const leads = [], skipped = [];
    rows.slice(1).forEach((r, i) => {
      const get = f => (col[f] >= 0 ? (r[col[f]] || "").trim() : "");
      const lead = { name: get("name"), phone: get("phone"), location: get("location"), requirement: get("requirement"),
                     budget: get("budget"), timeline: get("timeline"), message: get("message") };
      const why = !lead.name ? "missing name" : !lead.message ? "missing message" : "";
      why ? skipped.push({ line: i + 2, reason: why }) : leads.push(lead);
    });
    const truncated = Math.max(0, leads.length - MAX_IMPORT_ROWS);
    return { leads: leads.slice(0, MAX_IMPORT_ROWS), skipped, truncated, error: "" };
  }

  /** Digits for a wa.me link, or "" if it doesn't look like a phone number. 10-digit numbers get +91. */
  function normalizePhone(p) {
    const raw = String(p || "").trim(), digits = raw.replace(/\D/g, "");
    if (raw.startsWith("+")) return digits.length >= 10 && digits.length <= 15 ? digits : "";
    if (digits.length === 10) return "91" + digits;
    if (digits.length === 11 && digits.startsWith("0")) return "91" + digits.slice(1);
    return digits.length >= 11 && digits.length <= 15 ? digits : "";
  }

  /** WhatsApp link with the message pre-filled. Without a number, WhatsApp lets you pick the contact. */
  function waLink(phone, text) {
    const num = normalizePhone(phone);
    return `https://wa.me/${num}?text=${encodeURIComponent(String(text || "").slice(0, 1500))}`;
  }

  root.Lib = { parseCSV, leadsFromCSV, normalizePhone, waLink, MAX_IMPORT_ROWS };
  if (typeof module !== "undefined" && module.exports) module.exports = root.Lib;
})(typeof window !== "undefined" ? window : globalThis);
