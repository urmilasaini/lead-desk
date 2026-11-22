// Pure helpers in static/lib.js: CSV parsing, phone cleanup, WhatsApp links.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Lib = require("../../static/lib.js");

test("parseCSV handles quotes, commas, escaped quotes and newlines inside fields", () => {
  const rows = Lib.parseCSV('a,b,c\r\n"x, y","say ""hi""","line1\nline2"\r\n');
  assert.deepEqual(rows, [["a", "b", "c"], ["x, y", 'say "hi"', "line1\nline2"]]);
});

test("parseCSV ignores a BOM and empty lines, and detects semicolons and tabs", () => {
  assert.deepEqual(Lib.parseCSV("﻿name;message\n\nAna;Hello\n"), [["name", "message"], ["Ana", "Hello"]]);
  assert.deepEqual(Lib.parseCSV("name\tmessage\nAna\tHi"), [["name", "message"], ["Ana", "Hi"]]);
});

test("sample CSV imports all 12 leads with nothing skipped", () => {
  const text = fs.readFileSync(path.join(__dirname, "../../static/sample_leads.csv"), "utf8");
  const res = Lib.leadsFromCSV(text);
  assert.equal(res.error, "");
  assert.equal(res.leads.length, 12);
  assert.equal(res.skipped.length, 0);
  const chat = res.leads.find(l => l.name === "Deepika Rao");
  assert.ok(chat.message.includes("\n"), "multi-line chat kept");
  assert.equal(res.leads.find(l => l.name === "Aarav Menon").requirement, "3BHK, ready to move");
});

test("column names are matched flexibly", () => {
  const res = Lib.leadsFromCSV("Customer Name,Mobile,Property Requirement,Buying Timeline,Customer Message\nRaj,98765 43210,2BHK,Soon,Need a flat");
  assert.equal(res.leads.length, 1);
  assert.deepEqual(res.leads[0], { name: "Raj", phone: "98765 43210", location: "", requirement: "2BHK", budget: "", timeline: "Soon", message: "Need a flat" });
});

test("missing required columns gives a helpful error", () => {
  const res = Lib.leadsFromCSV("name,location\nRaj,Whitefield");
  assert.match(res.error, /name.*message/i);
  assert.equal(res.leads.length, 0);
  assert.match(Lib.leadsFromCSV("").error, /empty/i);
});

test("rows without a name or message are skipped with a reason", () => {
  const res = Lib.leadsFromCSV("name,message\nAna,Hi\n,No name\nBo,\n");
  assert.equal(res.leads.length, 1);
  assert.deepEqual(res.skipped.map(s => s.reason), ["missing name", "missing message"]);
  assert.deepEqual(res.skipped.map(s => s.line), [3, 4]);
});

test("imports are capped at 50 leads", () => {
  const body = Array.from({ length: 60 }, (_, i) => `Lead ${i},hello`).join("\n");
  const res = Lib.leadsFromCSV("name,message\n" + body);
  assert.equal(res.leads.length, 50);
  assert.equal(res.truncated, 10);
});

test("normalizePhone", () => {
  assert.equal(Lib.normalizePhone("98765 43210"), "919876543210");
  assert.equal(Lib.normalizePhone("098765-43210"), "919876543210");
  assert.equal(Lib.normalizePhone("+91 98765 43210"), "919876543210");
  assert.equal(Lib.normalizePhone("+971 50 123 4567"), "971501234567");
  assert.equal(Lib.normalizePhone("919876543210"), "919876543210");
  assert.equal(Lib.normalizePhone("12345"), "");
  assert.equal(Lib.normalizePhone(""), "");
  assert.equal(Lib.normalizePhone("abc"), "");
});

test("waLink pre-fills the message and works without a number", () => {
  const withNum = Lib.waLink("98765 43210", "Hi Rohit, let's meet & talk?");
  assert.equal(withNum, "https://wa.me/919876543210?text=Hi%20Rohit%2C%20let's%20meet%20%26%20talk%3F");
  assert.equal(Lib.waLink("", "Hello"), "https://wa.me/?text=Hello");
  assert.equal(Lib.waLink("nonsense", "Hello"), "https://wa.me/?text=Hello");
  assert.ok(decodeURIComponent(Lib.waLink("", "x".repeat(5000))).length < 1600, "very long text is trimmed");
});
