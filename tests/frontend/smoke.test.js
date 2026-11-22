// Browser-level test: the real app (with a fake AI) loaded in jsdom, clicking through the main flows.
// Starts tests/fake_server.py itself. Run: npm test   (inside tests/frontend)
const test = require("node:test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const PORT = 5056, BASE = `http://127.0.0.1:${PORT}`;
const FORBIDDEN = /gemini|genai|firebase|firestore|openrouter|api[_ ]?key/i;
const wait = ms => new Promise(r => setTimeout(r, ms));
let server, w, d, errors = [];
const $ = s => d.querySelector(s);
const $$ = s => [...d.querySelectorAll(s)];
const click = el => { assert.ok(el, "element to click exists"); el.dispatchEvent(new w.MouseEvent("click", { bubbles: true })); };
const submit = form => form.dispatchEvent(new w.Event("submit", { cancelable: true, bubbles: true }));
const visible = sel => !$(sel).classList.contains("hidden");
async function until(fn, what, ms = 4000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (fn()) return; await wait(40); }
  assert.fail("timed out waiting for: " + what);
}
const nav = view => click($(`#nav [data-view="${view}"]`));

const Lib = require("../../static/lib.js");
const fs = require("node:fs");
const JSON_HEADERS = { "Content-Type": "application/json" };

async function loadPage() {
  if (w) w.close();
  const html = await (await fetch(BASE + "/")).text();
  const dom = new JSDOM(html.replace(/<link[^>]+fonts[^>]+>/g, "").replace(/<script src="[^"]+"><\/script>/g, ""),
    { url: BASE + "/", runScripts: "dangerously", pretendToBeVisual: true });
  w = dom.window; d = w.document;
  w.fetch = (u, o = {}) => { const { signal, ...rest } = o; return fetch(new URL(u, BASE), rest); };  // jsdom's AbortSignal isn't Node's
  w.scrollTo = () => {}; w.confirm = () => true;
  w.addEventListener("error", e => errors.push(e.message));
  w.addEventListener("unhandledrejection", e => errors.push(String(e.reason)));
  for (const f of ["lib.js", "app.js"]) {
    const s = d.createElement("script"); s.textContent = await (await fetch(`${BASE}/static/${f}`)).text(); d.body.appendChild(s);
  }
  await until(() => $$("#tiles .tile").length === 4, "overview to load");
}

/** Add the 12 sample leads through the API (fake AI), spread across a few pipeline stages. */
async function seed() {
  const text = fs.readFileSync(path.join(__dirname, "../../static/sample_leads.csv"), "utf8");
  const ids = [];
  for (const form of Lib.leadsFromCSV(text).leads) {
    const r = await fetch(BASE + "/api/leads", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(form) });
    ids.push((await r.json()).lead.id);
  }
  for (const [i, stage] of [[0, "negotiation"], [1, "contacted"], [2, "site_visit"]])
    await fetch(`${BASE}/api/leads/${ids[i]}/stage`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ stage }) });
}

test.before(async () => {
  server = spawn("python", [path.join(__dirname, "..", "fake_server.py"), String(PORT)], { stdio: "ignore" });
  for (let i = 0; i < 100; i++) { try { if ((await fetch(BASE + "/api/health")).ok) break; } catch {} await wait(100); }
  await loadPage();
});
test.after(() => { server && server.kill(); w && w.close(); });

test("a fresh app is empty: nothing pre-filled, with a clear way to start", () => {
  assert.match($("#emptyNote").textContent, /No leads yet/);
  assert.match($("#emptyNote").textContent, /Nothing is pre-filled/);
  assert.ok($("#emptyNote [data-action=add]") && $("#emptyNote [data-action=import]") && $("#emptyNote [data-sample-import]"));
  assert.match($("#hero").textContent, /first lead/i, "the call-next card invites the first lead");
  assert.doesNotMatch(d.body.textContent, /demo/i);
});

test("'Try the sample leads' opens the import with all 12 sample leads ready (they are analysed for real)", async () => {
  click($("#emptyNote [data-sample-import]"));
  await until(() => visible("#impPreview"), "sample preview");
  assert.match($("#impPreview").textContent, /12 leads found/);
  d.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.ok(!$("#importModal").classList.contains("open"), "Escape closes the dialog");
  await seed();
  await loadPage();
});

test("overview: Urmila Saini, no old names, nothing about the AI provider", () => {
  assert.match($(".user").textContent, /Urmila Saini/);
  assert.equal($(".user .avatar").textContent, "US");
  assert.doesNotMatch(d.body.textContent, /Sales Person/);
  assert.match($("#greet").textContent, /Urmila/);
  assert.doesNotMatch(d.body.textContent, FORBIDDEN);
});

test("overview: summary, call-next card, up next, needs attention, lead mix and pipeline strip", () => {
  nav("overview");
  assert.match($("#summary").textContent, /hot lead/);
  const tiles = $$("#tiles .tile");
  assert.equal(tiles.length, 4);
  assert.ok(tiles.every(x => x.dataset.view), "every tile is a shortcut");
  const hero = $("#hero");
  assert.ok(hero.textContent.includes("Call next") && hero.querySelector(".ring"), "hero with a score ring");
  assert.ok(hero.querySelector("[data-open]") && hero.querySelector("[data-call]"), "open and log-call buttons");
  assert.match(hero.querySelector("a[data-wa]").href, /^https:\/\/wa\.me\//, "WhatsApp button");
  const rows = $$("#upnext .mini");
  assert.ok(rows.length >= 1 && rows.length <= 4, "up to four more calls");
  assert.ok(!$("#upnext").textContent.includes(hero.querySelector("h3").textContent), "the hero lead is not repeated");
  assert.ok(rows.every(r => r.querySelector(".slot-t")), "each has a time slot");
  assert.equal($$("#mix .mix-legend b").reduce((a, b) => a + Number(b.textContent), 0), 12, "lead mix adds up to every lead");
  assert.equal($$("#funnel .stage-cell").length, 6);
  assert.equal($$("#funnel .stage-cell b").reduce((a, b) => a + Number(b.textContent), 0), 12, "pipeline strip adds up too");
  assert.ok($("#attention").textContent.length > 0);
  click(hero.querySelector("[data-open]"));
  assert.ok(visible("#v-lead"), "Open lead goes to the lead page");
});

test("sidebar has Overview, Leads, Pipeline, Today's calls, How it works", () => {
  const labels = $$("#nav button").map(b => b.textContent.trim().replace(/\d+$/, ""));
  assert.deepEqual(labels, ["Overview", "Leads", "Pipeline", "Today's calls", "How it works"]);
});

test("how it works page explains the flow and score", () => {
  nav("how");
  assert.ok(visible("#v-how"));
  const t = $("#v-how").textContent;
  for (const s of ["Budget fit", "Funds ready", "Hot · 70+", "Log call", "Import CSV", "Pipeline"]) assert.ok(t.includes(s), s);
  assert.doesNotMatch(t, FORBIDDEN);
  assert.ok($("#v-how a[href='/static/sample_leads.csv']"), "sample CSV link");
});

test("leads board: 4 priority columns, 12 cards, import and add buttons", () => {
  nav("leads");
  assert.equal($$("#board .colm").length, 4);
  assert.equal($$("#board .lc").length, 12);
  assert.ok($('[data-action="import"]') && $$('#v-leads [data-action="add"]').length === 1);
  click($('[data-mode="list"]'));
  assert.equal($$("#table tbody tr").length, 12);
  click($('[data-mode="board"]'));
});

test("lead page: assignment sections, floating chat widget, WhatsApp, stage", async () => {
  click($("#board .lc"));
  assert.ok(visible("#v-lead"));
  const secs = $$("#detail .sec-t").map(e => e.textContent);
  for (const s of ["Recommended next action", "Lead summary", "Customer intent", "Key requirements", "Objections / concerns", "Suggested response"])
    assert.ok(secs.some(x => x.includes(s)), s);
  // side-by-side layout: next action on top, then "understand + prepare" | "reply + score"
  const cols = $$("#detail .lead-cols > .col");
  assert.equal(cols.length, 2, "two columns");
  for (const s of ["Lead summary", "Customer intent", "Key requirements", "Objections / concerns", "On the call"])
    assert.ok(cols[0].textContent.includes(s), "left column has " + s);
  for (const s of ["Suggested response", "Why this score"]) assert.ok(cols[1].textContent.includes(s), "right column has " + s);
  assert.equal($("#detail .next").closest(".lead-cols"), null, "the next action sits above both columns");
  // chat is a widget, not part of the page layout
  assert.ok(visible("#chatFab"), "chat button is visible on a lead page");
  assert.ok(!visible("#chatPanel"), "panel starts closed");
  assert.equal($("#detail #chatPanel"), null, "chat is not part of the page layout");
  click($("#chatFab"));
  assert.ok(visible("#chatPanel") && !visible("#chatFab"), "button opens the panel");
  click($('#chatPanel [data-ask]'));
  await until(() => $$("#chatPanel .m.bot").length === 1, "chat answer");
  assert.doesNotMatch($("#chatPanel").textContent, FORBIDDEN);
  click($("[data-chat-close]"));
  assert.ok(!visible("#chatPanel"));

  // WhatsApp: opens with the reply pre-filled; adding a phone makes it a direct chat
  const wa = $("#waBtn");
  assert.match(wa.href, /^https:\/\/wa\.me\/\?text=/);
  assert.equal(wa.target, "_blank");
  click($("[data-phone]"));
  $("#phoneForm [name=phone]").value = "98765 43210";
  submit($("#phoneForm"));
  await until(() => $("#waBtn").href.startsWith("https://wa.me/919876543210?text="), "phone saved");
  const text = decodeURIComponent($("#waBtn").href.split("text=")[1]);
  assert.equal(text, $("#detail .reply").textContent);

  // stage select (single-lead stage change)
  const id = $("#detail [data-call]").dataset.call;
  $("#stageSel").value = "contacted";
  $("#stageSel").dispatchEvent(new w.Event("change", { bubbles: true }));
  await until(() => $("#stageSel").value === "contacted", "stage saved");
  assert.equal((await (await fetch(`${BASE}/api/leads/${id}`)).json()).lead.stage, "contacted");
});

test("pipeline: 6 stage columns, drag and drop, next-stage button", async () => {
  nav("pipeline");
  assert.equal($$("#kanban .pcol").length, 6);
  assert.deepEqual($$("#kanban .colm-h b").map(e => e.textContent), ["New", "Contacted", "Site visit", "Negotiation", "Booked", "Lost"]);
  const card = $('#kanban .pcol[data-stage="new"] .pc');
  const id = card.dataset.id, before = $$("#kanban .pcol[data-stage=booked] .pc").length;
  const drop = new w.Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(drop, "dataTransfer", { value: { getData: () => id } });
  $('#kanban .pcol[data-stage="booked"] .cards').dispatchEvent(drop);
  await until(() => $$("#kanban .pcol[data-stage=booked] .pc").length === before + 1, "card dropped into Booked");
  assert.equal((await (await fetch(`${BASE}/api/leads/${id}`)).json()).lead.stage, "booked");
  // "next stage" button on a New card
  const adv = $('#kanban .pcol[data-stage="new"] [data-advance]');
  const advId = adv.dataset.advance;
  click(adv);
  await until(() => $(`#kanban .pcol[data-stage="contacted"] .pc[data-id="${advId}"]`), "advanced to Contacted");
});

test("add lead: analysis fails once, lead is kept, Re-analyze + Open appear and work in place", async () => {
  click($('#v-pipeline [data-action="add"]'));
  assert.ok($("#addModal").classList.contains("open"));
  const f = n => $(`#addForm [name="${n}"]`);
  f("name").value = "Failing Fiona"; f("message").value = "Need a 3BHK [fail-once]"; f("location").value = "Whitefield";
  submit($("#addForm"));
  await until(() => visible("#addResult"), "result panel");
  assert.match($("#addResult").textContent, /saved/i);
  assert.doesNotMatch($("#addResult").textContent, FORBIDDEN);
  assert.ok($("#addResult [data-add-reanalyze]") && $("#addResult [data-add-open]"), "Re-analyze and Open buttons");
  click($("#addResult [data-add-reanalyze]"));
  await until(() => /analysis is ready/i.test($("#addResult").textContent), "in-place success");
  assert.ok($("#addResult [data-add-open]"));
  click($("#addResult [data-add-open]"));
  assert.ok(visible("#v-lead") && $("#detail").textContent.includes("Failing Fiona"));
  assert.ok(!$("#addModal").classList.contains("open"));
});

test("import CSV: preview, progress, failed row shows Re-analyze + Open and updates in place", async () => {
  nav("leads");
  click($('#v-leads [data-action="import"]'));
  assert.ok($("#importModal").classList.contains("open"));
  const csv = "name,phone,location,requirement,budget,timeline,message\n" +
    'Ira One,,Whitefield,3BHK,1.5 Cr,Soon,"Need 3BHK, loan ready"\n' +
    "Ben Fail,,Sarjapur Road,2BHK,90 L,Soon,Looking for 2BHK [fail-once]\n" +
    "Cy Three,,Hebbal,3BHK,2 Cr,Later,Interested in Hebbal\n,,,,,,no name row\n";
  Object.defineProperty($("#csvFile"), "files", { value: [new w.File([csv], "leads.csv", { type: "text/csv" })] });
  $("#csvFile").dispatchEvent(new w.Event("change"));
  await until(() => visible("#impPreview"), "preview");
  assert.match($("#impPreview").textContent, /3 leads found/);
  assert.match($("#impPreview").textContent, /1 row skipped/);
  click($("[data-imp-start]"));
  await until(() => $$("#impRun .imp-row").length === 3 && !$$("#impRun .imp-row .spinner").length && !/Waiting/.test($("#impRun").textContent), "import to finish", 8000);
  const rows = $$("#impRun .imp-row");
  assert.equal(rows.filter(r => r.querySelector(".imp-ok")).length, 2, "two analysed");
  const failed = rows.find(r => r.textContent.includes("Ben Fail"));
  assert.ok(failed.querySelector("[data-imp-re]") && failed.querySelector("[data-imp-open]"), "failed row has Re-analyze and Open");
  assert.doesNotMatch($("#impRun").textContent, FORBIDDEN);
  assert.match($("#impSub").textContent, /1 need attention/);
  click(failed.querySelector("[data-imp-re]"));
  await until(() => $$("#impRun .imp-row").find(r => r.textContent.includes("Ben Fail")).querySelector(".imp-ok"), "row fixed in place");
  assert.match($("#impSub").textContent, /All 3 leads analysed/);
  click($("[data-imp-done]"));
  assert.ok(!$("#importModal").classList.contains("open") && visible("#v-leads"));
  assert.ok($$("#board .lc").some(c => c.textContent.includes("Cy Three")), "imported leads are on the board");
});

test("log call from the lead page shows the score change", async () => {
  nav("leads");
  click($("#board .lc"));
  click($("#detail [data-call]"));
  $("#callForm [name=outcome]").value = "visit_done"; $("#callForm [name=notes]").value = "wife must agree";
  submit($("#callForm"));
  await until(() => $("#detail .callres"), "call result");
  assert.match($("#detail").textContent, /Call log/);
});

test("today's calls page lists hot and warm leads", () => {
  nav("calls");
  assert.ok($$("#plan .slot").length > 0);
});

test("no JavaScript errors happened during the whole run", () => {
  assert.deepEqual(errors, []);
});
