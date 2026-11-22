/* Lead Desk frontend. Talks to the Flask API in app.py; all AI work happens on the server.
   Messages shown to the user are always plain language (see api() for how errors are handled). */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const ORDER = ["Hot","Warm","Cold","Disqualified"];
const COLUMN_TITLE = {Pending:"Needs analysis"};
const COLOR = {Hot:"var(--hot)",Warm:"var(--warm)",Cold:"var(--cold)",Disqualified:"var(--dq)",Pending:"var(--dq)"};
const STAGES = [["new","New"],["contacted","Contacted"],["site_visit","Site visit"],["negotiation","Negotiation"],["booked","Booked"],["lost","Lost"]];
const STAGE_COLOR = {new:"var(--dq)",contacted:"var(--cold)",site_visit:"#8B5CF6",negotiation:"var(--warm)",booked:"var(--ok)",lost:"var(--hot)"};
const MAX = {budget_fit:20,financial:15,timeline:20,need:15,intent:15,authority:10,engagement:5};
const LABEL = {budget_fit:"Budget fit",financial:"Funds ready",timeline:"Timeline",need:"Clear need",intent:"Buying intent",authority:"Decision maker",engagement:"Detail given",activity:"Recent activity"};
const OUTCOME = {connected:"Talked",no_answer:"No answer",visit_booked:"Visit booked",visit_done:"Visit done",negotiating:"Negotiating",not_interested:"Not interested"};
const LATE = {Hot:30, Warm:240, Cold:1e9, Disqualified:1e9, Pending:1e9};
const AVC = ["#0EA5E9","#F59E0B","#10B981","#8B5CF6","#EC4899","#14B8A6","#6366F1","#EF4444"];
const WA_ICON = '<svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.4A8 8 0 1 1 21 12z"/></svg>';

let LEADS = [], SAMPLES = [], sampleIdx = 0;
let view = "overview", origin = "leads", mode = "board", query = "";
let current = null, ovId = null, callId = null, phoneId = null, dragId = null;
let chatOpen = false, chatBusy = null, addBusy = false, addResult = null, imp = null;
const analyzing = new Set(), lastCall = {};

/* ---------- API (errors are always plain language) ---------- */
async function api(path, opts = {}) {
  const {timeout = 70000, body, ...rest} = opts;
  const ctrl = new AbortController(), timer = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(path, {headers: {"Content-Type": "application/json"}, signal: ctrl.signal, ...rest,
      body: body ? JSON.stringify(body) : undefined});
  } catch (e) {
    throw new Error(e.name === "AbortError" ? "This is taking longer than expected. Please try again."
                                            : "Can't reach the server. Check your connection and try again.");
  } finally { clearTimeout(timer); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || ([502, 503, 504].includes(res.status)
    ? "This is taking longer than expected. Please try again." : "Something went wrong. Please try again."));
  return data;
}
const upsert = lead => { const i = LEADS.findIndex(l => l.id === lead.id); i < 0 ? LEADS.push(lead) : LEADS[i] = lead; };
const byId = id => LEADS.find(l => l.id === id);

/* ---------- helpers ---------- */
const isReady = l => l.ai_status === "ready";
const isAnalyzing = l => l.ai_status === "analyzing";   // someone is adding or re-analysing it right now
const ago = m => m == null ? "" : m < 1 ? "just now" : m < 60 ? `${m}m` : m < 1440 ? `${Math.floor(m/60)}h${m%60 ? " "+m%60+"m" : ""}` : `${Math.floor(m/1440)}d`;
const isLate = l => l.waiting_minutes != null && l.waiting_minutes > LATE[l.priority];
const initials = n => n.split(/[\s&,]+/).filter(Boolean).slice(0,2).map(w=>w[0]).join("").toUpperCase();
const hash = s => [...s].reduce((a,c) => a + c.charCodeAt(0), 0);
const avatar = l => `<span class="av" style="background:${AVC[hash(l.id) % AVC.length]}">${esc(initials(l.form.name))}</span>`;
const tagPill = l => `<span class="pill"><span class="dot d-${l.priority === "Pending" ? "Disqualified" : l.priority}"></span>${l.priority === "Pending" ? (isAnalyzing(l) ? "Analyzing…" : "Needs analysis") : l.priority}</span>`;
const lakhs = b => { const m = String(b).match(/([\d.]+)\s*(cr|l)/i); return m ? parseFloat(m[1]) * (/cr/i.test(m[2]) ? 100 : 1) : 0; };
const money = v => v >= 100 ? `₹${(v/100).toFixed(1)} Cr` : `₹${Math.round(v)} L`;
const toast = t => { const e = $("#toast"); e.textContent = t; e.style.display = "block"; clearTimeout(e._t); e._t = setTimeout(() => e.style.display = "none", 3200); };
const sortLeads = arr => arr.sort((a,b) => (isLate(b) - isLate(a)) || (b.score ?? 0) - (a.score ?? 0));
const first = l => l.form.name.split(" ")[0];
const stageLabel = k => (STAGES.find(s => s[0] === k) || STAGES[0])[1];
const fmtTime = iso => new Date(iso).toLocaleString("en-IN", {day:"numeric", month:"short", hour:"numeric", minute:"2-digit"});
const metaLine = l => [l.form.location, l.form.requirement, l.form.budget].filter(Boolean).map(esc).join(" · ");

// Turn rubric reasons into short "why" lines: strengths (+) and gaps (−)
const SHORT = {
  budget_fit: (v,w) => w.startsWith("no budget") ? "No budget given" : v >= 16 ? "Budget fits the market" : v <= 2 ? "Budget too low for this property" : "Budget a bit tight",
  financial:  (v,w) => ({sanctioned:"Loan sanctioned","pre approved":"Loan pre-approved","own funds":"Paying from own funds","in process":"Loan in process","down payment ready":"Down payment ready"})[w.replace("funds: ","")] || (v === 5 ? "Funds / loan not mentioned" : "Needs a loan, not applied"),
  timeline:   (v,w) => v <= 2 ? "No timeline given" : `Buying in ${w.replace("~","").replace(/^1 month\(s\)/,"1 month").replace("month(s)","months")}`,
  need:       (v,w) => w.includes("reason:") ? w.split("reason: ")[1].split(",")[0].replace(/^./,c=>c.toUpperCase()) : v <= 4 ? "Unclear requirement" : "Clear requirement",
  intent:     (v,w) => v < 6 ? "No visit or call asked yet" : w.split(",")[0].replace(/^./,c=>c.toUpperCase()),
  authority:  (v,w) => ({self:"Decides alone","joint present":"Couple deciding together",unknown:"Decision maker unknown","needs others":"Needs someone else's approval"})[w] || w,
  engagement: (v) => v <= 1 ? "Very short message" : "Detailed message",
};
function whyLines(l){
  if (l.tag === "Disqualified") return [{t:"minus", s:(l.reasons[0]||"").replace(/^.*?: /,"")}];
  const keys = Object.keys(l.breakdown).filter(k => MAX[k]);
  const items = keys.map((k,i) => ({k, v:l.breakdown[k], r:l.breakdown[k]/MAX[k], w:(l.reasons[i]||"").split(": ").slice(1).join(": ")}));
  const plus = items.filter(x => x.r >= .75 && x.k !== "engagement").sort((a,b)=>b.v-a.v).slice(0,3).map(x => ({t:"plus", s:SHORT[x.k](x.v,x.w)}));
  const minus = items.filter(x => x.r <= .35).sort((a,b)=>MAX[b.k]-MAX[a.k]).slice(0,2).map(x => ({t:"minus", s:SHORT[x.k](x.v,x.w)}));
  const act = l.breakdown.activity;
  if (act) (act > 0 ? plus : minus).unshift({t: act > 0 ? "plus" : "minus", s: (l.reasons.find(r => r.startsWith("activity")) || "").replace("activity: ","")});
  return [...plus, ...minus];
}
const whyHTML = l => `<ul class="why">${whyLines(l).map(x=>`<li><span class="${x.t}">${x.t==="plus"?"+":"−"}</span><span class="${x.t==="minus"?"muted":""}">${esc(x.s)}</span></li>`).join("")}</ul>`;

/* ---------- needs-analysis leads: Re-analyze + Open, updating in place ---------- */
const pendingActions = l => (analyzing.has(l.id) || isAnalyzing(l))
  ? `<span class="busy-text"><i class="spinner"></i>Analyzing…</span>`
  : `<button class="btn sm" data-reanalyze="${l.id}">Re-analyze</button><button class="btn sm" data-open="${l.id}">Open</button>`;
const pendingCard = l => `<div class="lc pending" data-open="${l.id}">
  <div class="lc-top">${avatar(l)}<b>${esc(l.form.name)}</b></div>
  <div class="muted" style="font-size:12px;margin-top:6px">${metaLine(l) || "No details yet"}</div>
  <div class="pend-msg">${analyzing.has(l.id) || isAnalyzing(l) ? "" : esc(l.ai_error || "The analysis didn't finish.")}</div>
  <div class="pend-actions">${pendingActions(l)}</div></div>`;

async function reanalyze(id, quiet = false){
  if (analyzing.has(id)) return;
  analyzing.add(id); render();
  try {
    upsert((await api(`/api/leads/${id}/reanalyze`, {method:"POST", timeout:90000})).lead);
    if (!quiet) toast("Analysis ready");
  } catch (e) { if (!quiet) toast(e.message); }
  analyzing.delete(id); render();
  return byId(id);
}
async function retryAll(){
  const ids = LEADS.filter(l => !isReady(l) && !isAnalyzing(l)).map(l => l.id);
  let ok = 0;
  for (const id of ids) { const l = await reanalyze(id, true); if (l && isReady(l)) ok++; }
  toast(ok === ids.length ? `All ${ok} leads analysed` : `${ok} of ${ids.length} analysed. Try the rest again in a moment.`);
}
function pendingNote(){
  const n = LEADS.filter(l => !isReady(l) && !isAnalyzing(l)).length;
  if (!n) return "";
  const running = analyzing.size > 0;
  return `<div class="note-card in-bar"><span><b>${n} lead${n > 1 ? "s" : ""}</b> waiting for analysis.</span>${running ? `<span class="busy-text"><i class="spinner"></i>Analyzing…</span>` : `<button class="btn sm" data-retryall>Analyze now</button>`}</div>`;
}

/* ---------- empty state ---------- */
const emptyCard = () => LEADS.length ? "" : `<div class="card empty-card"><div class="card-b">
  <h3 style="margin:0 0 4px">No leads yet</h3>
  <p class="muted" style="margin:0 0 14px;font-size:13px">Nothing is pre-filled. Add a lead or import a CSV, and the AI analyses each one for real.</p>
  <div class="row-actions" style="justify-content:flex-start;margin:0"><button class="btn primary" data-action="add">Add a lead</button><button class="btn" data-action="import">Import CSV</button><button class="btn" data-sample-import>Try the 12 sample leads</button></div></div></div>`;
async function importSample(){
  try { const text = await (await fetch("/static/sample_leads.csv")).text(); openImport(); previewImport(text); }
  catch { toast("Couldn't load the sample file. Please try again."); }
}

/* ---------- navigation ---------- */
const TITLES = {overview:"Overview", leads:"Leads", pipeline:"Pipeline", lead:"Lead details", calls:"Today's calls", how:"How it works"};
const BACK = {overview:"Back to overview", leads:"Back to leads", pipeline:"Back to pipeline", calls:"Back to today's calls"};
function go(v, id){
  if (v === "lead" && view !== "lead") origin = view;
  view = v; if (id) current = id;
  Object.keys(TITLES).forEach(x => $("#v-"+x).classList.toggle("hidden", x !== v));
  document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("on", b.dataset.view === (v === "lead" ? origin : v)));
  $("#title").textContent = TITLES[v];
  $("#back").lastChild.textContent = BACK[origin] || "Back";
  $("#side").classList.remove("open");
  try { history.replaceState(null, "", "#" + (v === "lead" ? "lead/" + current : v)); } catch {}  // refresh keeps your place
  render(); window.scrollTo(0,0);
}
function routeFromHash(){
  const [v, id] = location.hash.replace("#", "").split("/");
  if (v === "lead" && id && byId(id)) return go("lead", id);
  return go(TITLES[v] && v !== "lead" ? v : "overview");
}
function render(){
  const n = LEADS.filter(l => l.priority === "Hot" && l.waiting_minutes != null).length;
  $("#callBadge").textContent = n; $("#callBadge").classList.toggle("hidden", !n);
  if (view === "overview") renderOverview();
  if (view === "leads") renderLeads();
  if (view === "pipeline") renderPipeline();
  if (view === "lead") { if (!byId(current)) return go("leads"); renderDetail(); }
  if (view === "calls") renderCalls();
  syncChat();
  watchAnalyzing();
}
let watchTimer = null;
function watchAnalyzing(){   // leads being analysed right now fill in by themselves
  if (watchTimer || !LEADS.some(isAnalyzing)) return;
  watchTimer = setTimeout(async () => { watchTimer = null; try { await load(); } catch {} }, 3000);
}

/* ---------- today's plan (shared by the overview and the call plan page) ---------- */
const fmtSlot = m => { const h = Math.floor(m/60), mm = String(m%60).padStart(2,"0"); return `${h>12?h-12:h}:${mm} ${h>=12?"pm":"am"}`; };
function buildPlan(){
  const list = LEADS.filter(l => l.priority==="Hot" || l.priority==="Warm")
    .map(l => ({l, u: l.score + Math.min(l.waiting_minutes ?? 0, 300)/10})).sort((a,b)=>b.u-a.u);
  const now = new Date(); let t = Math.max(10*60, Math.ceil((now.getHours()*60 + now.getMinutes())/15)*15);
  const today = now.toDateString();
  return list.map(({l}) => {
    const fixed = /after 7 ?PM/i.test(l.analysis.next_action.when);                 // the customer asked for a time
    const done = l.calls.some(c => new Date(c.at).toDateString() === today);
    const time = fixed ? "7:00 pm" : done ? "done" : fmtSlot(t);
    if (!fixed && !done) t += 15;
    return {l, time, fixed, done};
  });
}

/* ---------- overview ---------- */
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
function heroHTML(item){
  const title = '<div class="sec-t">Call next</div>';
  if (!item) return `<div class="card-b">${title}<p class="txt" style="margin:0">${LEADS.length ? "Nothing needs a call right now. Nice." : "Your first lead will show up here."}</p></div>`;
  const l = item.l, a = l.analysis, why = whyLines(l).filter(x => x.t === "plus").slice(0, 3);
  const wa = Lib.waLink(l.form.phone, a.suggested_response.text);
  return `<div class="card-b hero-b">${title}
    <div class="hero-main">
      <div class="ring" style="--p:${l.score};--c:${COLOR[l.priority]}"><span>${l.score}</span></div>
      <div class="hero-t"><h3>${esc(l.form.name)}</h3>
        <div class="muted" style="font-size:13px">${[l.form.location, l.form.requirement, l.form.budget, l.form.timeline].filter(Boolean).map(esc).join(" · ")}</div>
        <div class="hero-chips">${tagPill(l)}${l.waiting_minutes == null ? "" : `<span class="pill ${isLate(l) ? "late" : ""}">waiting ${ago(l.waiting_minutes)}</span>`}${item.fixed ? `<span class="pill">customer asked for ${item.time}</span>` : ""}</div></div>
    </div>
    <div class="next"><div><b>${esc(a.next_action.action)}</b><span>${esc(a.next_action.when)}${a.next_action.why ? " · " + esc(a.next_action.why) : ""}</span></div></div>
    ${why.length ? `<div class="chips hero-why">${why.map(x => `<span class="chip">${esc(x.s)}</span>`).join("")}</div>` : ""}
    <div class="row-actions" style="justify-content:flex-start">
      <button class="btn primary" data-open="${l.id}">Open lead</button>
      <a class="btn wa" data-wa="${l.id}" href="${esc(wa)}" target="_blank" rel="noopener">${WA_ICON}WhatsApp</a>
      <button class="btn" data-call="${l.id}">Log call</button>
    </div></div>`;
}
const alertRow = (icon, text, label, attr) =>
  `<div class="alert-row"><span class="alert-ic">${icon}</span><span class="alert-t">${text}</span><button class="btn sm" ${attr}>${label}</button></div>`;

function renderOverview(){
  const h = new Date().getHours();
  $("#greet").textContent = (h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening") + ", Urmila";
  $("#date").textContent = new Date().toLocaleDateString("en-GB",{weekday:"long",day:"numeric",month:"long",year:"numeric"});
  const n = t => LEADS.filter(l => l.priority === t).length, pend = n("Pending");
  const plan = buildPlan(), open = plan.filter(x => !x.done);
  const hotToCall = open.filter(x => x.l.priority === "Hot").length, late = LEADS.filter(isLate).length;

  // one line that says how the day looks
  const parts = [hotToCall && `${plural(hotToCall, "hot lead needs", "hot leads need")} a call today`, late && `${late} waited too long`].filter(Boolean);
  $("#summary").textContent = !LEADS.length ? "Add your first lead and the AI will tell you who to call." : parts.length ? parts.join("  ·  ") : "You're all caught up.";

  // tiles: each one is a shortcut
  const openDeals = LEADS.filter(l => l.priority === "Hot" || l.priority === "Warm").reduce((a, l) => a + lakhs(l.form.budget), 0);
  $("#tiles").innerHTML = [
    ["Leads", LEADS.length, pend ? `${pend} need analysis` : `${n("Disqualified")} disqualified`, "leads"],
    ["Hot", n("Hot"), "call today", "leads"],
    ["Waiting too long", late, "reply now", "calls"],
    ["Open deals", money(openDeals), "hot and warm leads", "pipeline"],
  ].map(([k, v, s, to]) => `<button class="tile" data-view="${to}"><span>${k}</span><b>${v}</b><small>${s}</small></button>`).join("");
  $("#emptyNote").innerHTML = emptyCard();

  // the single most useful thing to do right now
  const heroItem = open.find(x => !x.fixed) || open[0];
  $("#hero").innerHTML = heroHTML(heroItem);

  // lead mix
  const mix = [["Hot","Hot"],["Warm","Warm"],["Cold","Cold"],["Disqualified","Disqualified"],["Pending","Needs analysis"]];
  $("#mix").innerHTML = LEADS.length
    ? `<div class="mix-bar">${mix.map(([k, label]) => n(k) ? `<i style="width:${n(k) / LEADS.length * 100}%;background:${COLOR[k]}" title="${label}: ${n(k)}"></i>` : "").join("")}</div>
       <div class="mix-legend">${mix.map(([k, label]) => `<button data-view="leads"><span class="dot" style="background:${COLOR[k]}"></span>${label}<b>${n(k)}</b></button>`).join("")}</div>`
    : `<p class="muted" style="margin:0;font-size:13px">No leads yet.</p>`;

  // the next few calls, in order, with their slot
  const rest = open.filter(x => x !== heroItem).slice(0, 4);
  $("#upnext").innerHTML = rest.map(({l, time, fixed}) =>
    `<div class="mini" data-open="${l.id}">${avatar(l)}<div class="t"><b>${esc(l.form.name)}</b><span>→ ${esc(l.analysis.next_action.action)}</span></div><span class="slot-t ${fixed ? "fixed" : ""}">${time}</span><span class="sc">${l.score}</span></div>`).join("")
    || `<div class="card-b muted">Nothing else in line.</div>`;

  // things that need a human
  const stuck = LEADS.filter(l => !isReady(l) && !isAnalyzing(l)), flagged = LEADS.filter(l => l.injection_suspected);
  const rows = [];
  if (late) rows.push(alertRow("⏱", `${plural(late, "lead has", "leads have")} waited too long for a reply`, "See call plan", 'data-view="calls"'));
  if (stuck.length) rows.push(alertRow("!", `${plural(stuck.length, "lead is", "leads are")} waiting for analysis`, "Analyze now", "data-retryall"));
  if (flagged.length) rows.push(alertRow("⚠", `${plural(flagged.length, "message tried", "messages tried")} to instruct the AI`, "Review", `data-open="${flagged[0].id}"`));
  $("#attention").innerHTML = rows.join("") || `<div class="card-b muted">All clear. Nothing needs attention.</div>`;

  // pipeline strip
  $("#funnel").innerHTML = STAGES.map(([key, label]) => {
    const g = LEADS.filter(l => (l.stage || "new") === key), value = g.reduce((a, l) => a + lakhs(l.form.budget), 0);
    return `<button class="stage-cell" data-view="pipeline" style="--c:${STAGE_COLOR[key]}"><span>${label}</span><b>${g.length}</b><small>${g.length ? money(value) : "none"}</small></button>`;
  }).join("");
}

/* ---------- leads (board / list) ---------- */
function renderLeads(){
  document.querySelectorAll("#seg button").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  $("#board").classList.toggle("hidden", mode !== "board");
  $("#table").classList.toggle("hidden", mode !== "list");
  $("#leadsNote").innerHTML = emptyCard() + pendingNote();
  const match = l => (l.form.name + l.form.location + l.form.requirement + l.analysis.summary).toLowerCase().includes(query);
  const cols = [...ORDER, ...(LEADS.some(l => !isReady(l)) ? ["Pending"] : [])];
  if (mode === "board"){
    $("#board").style.gridTemplateColumns = `repeat(${cols.length},minmax(250px,1fr))`;
    $("#board").innerHTML = cols.map(t => {
      const g = sortLeads(LEADS.filter(l => l.priority === t && match(l)));
      const total = g.reduce((a,l) => a + lakhs(l.form.budget), 0);
      return `<div class="colm"><div class="stripe" style="background:${COLOR[t]}"></div>
        <div class="colm-h"><div><b>${COLUMN_TITLE[t] || t}</b><p>${g.length ? money(total) + " total budget" : "—"}</p></div><span class="n">${g.length}</span></div>
        <div class="cards">${g.map(card).join("") || '<div class="empty">No leads here</div>'}</div></div>`;
    }).join("");
  } else {
    const rank = l => cols.indexOf(l.priority);
    const rows = LEADS.filter(match).sort((a,b) => rank(a) - rank(b) || (b.score ?? 0) - (a.score ?? 0));
    $("#table").innerHTML = `<table class="lt"><thead><tr><th>Score</th><th>Lead</th><th>Priority</th><th>Why</th><th>Recommended next action</th><th>Waiting</th></tr></thead><tbody>` +
      rows.map(l => isReady(l) ? `<tr data-open="${l.id}"><td><b style="font-size:16px">${l.score}</b></td>
        <td><b>${esc(l.form.name)}</b><div class="muted" style="font-size:12px">${metaLine(l)}</div></td>
        <td>${tagPill(l)}${l.override?' <span class="manual">changed</span>':""}</td>
        <td style="min-width:200px">${whyHTML(l)}</td>
        <td style="font-size:12px;font-weight:600">${esc(l.analysis.next_action.action)}</td>
        <td class="${isLate(l)?"late":""}" style="font-size:12px;white-space:nowrap">${l.waiting_minutes == null ? "replied" : ago(l.waiting_minutes)}</td></tr>`
      : `<tr data-open="${l.id}"><td><b style="font-size:16px" class="muted">—</b></td>
        <td><b>${esc(l.form.name)}</b><div class="muted" style="font-size:12px">${metaLine(l)}</div></td>
        <td>${tagPill(l)}</td><td class="muted" style="font-size:12px;min-width:200px">${analyzing.has(l.id) || isAnalyzing(l) ? "" : esc(l.ai_error || "")}</td>
        <td><div class="pend-actions">${pendingActions(l)}</div></td><td style="font-size:12px">${ago(l.waiting_minutes)}</td></tr>`).join("") + `</tbody></table>`;
  }
}
const card = l => !isReady(l) ? pendingCard(l) : `<div class="lc" data-open="${l.id}">
  <div class="lc-top">${avatar(l)}<b>${esc(l.form.name)}</b><span class="lc-score">${l.score}</span></div>
  <div class="meter"><i style="width:${l.score}%;background:${COLOR[l.priority]}"></i></div>
  ${whyHTML(l)}
  <div class="lc-next">→ ${esc(l.analysis.next_action.action)}</div>
  <div class="lc-foot">${l.override?'<span class="manual">changed</span>':""}${l.flags.includes("high_value")?'<span class="chip">high value</span>':""}${l.injection_suspected?'<span class="chip warn" title="This message tries to instruct the AI">⚠ check</span>':""}<span class="chip">${esc(l.form.budget || "no budget")}</span><span class="sp"></span>
    <span class="${isLate(l)?"late":"muted"}">${l.waiting_minutes == null ? "replied" : (isLate(l)?"⏱ ":"") + ago(l.waiting_minutes)}</span></div>
</div>`;

/* ---------- pipeline (kanban by stage) ---------- */
function renderPipeline(){
  $("#pipeNote").innerHTML = emptyCard();
  const nextOf = k => { const i = STAGES.findIndex(s => s[0] === k); return k === "booked" || k === "lost" ? null : STAGES[i+1][0]; };
  $("#kanban").innerHTML = STAGES.map(([key,label]) => {
    const g = LEADS.filter(l => (l.stage || "new") === key).sort((a,b) => (b.score ?? -1) - (a.score ?? -1));
    const total = g.reduce((a,l) => a + lakhs(l.form.budget), 0);
    const pc = l => `<div class="pc" draggable="true" data-id="${l.id}" data-open="${l.id}">
      <div class="pc-top">${avatar(l)}<b>${esc(l.form.name)}</b>
        <span class="sc">${isReady(l) ? `<span class="dot d-${l.priority}"></span>${l.score}` : '<span class="muted">—</span>'}</span></div>
      <div class="pc-next">${isReady(l) ? "→ " + esc(l.analysis.next_action.action) : "Needs analysis"}</div>
      <div class="pc-foot"><span class="chip">${esc(l.form.budget || "no budget")}</span><span class="sp"></span>
        <span class="${isLate(l)?"late":"muted"}">${l.waiting_minutes == null ? "" : (isLate(l)?"⏱ ":"") + ago(l.waiting_minutes)}</span>
        ${nextOf(key) ? `<button class="adv" data-advance="${l.id}" title="Move to ${stageLabel(nextOf(key))}">${stageLabel(nextOf(key))} ›</button>` : ""}</div></div>`;
    return `<div class="pcol" data-stage="${key}"><div class="stripe" style="background:${STAGE_COLOR[key]}"></div>
      <div class="colm-h"><div><b>${label}</b><p>${g.length ? money(total) : "—"}</p></div><span class="n">${g.length}</span></div>
      <div class="cards">${g.map(pc).join("") || '<div class="empty">Drop leads here</div>'}</div></div>`;
  }).join("");
}
async function moveStage(id, stage){
  const l = byId(id);
  if (!l || (l.stage || "new") === stage) return;
  const prev = l.stage || "new";
  l.stage = stage; render();
  try {
    upsert((await api(`/api/leads/${id}/stage`, {method:"POST", body:{stage}})).lead); render();
    toast(`${first(l)} moved to ${stageLabel(stage)}`);
  } catch (e) { l.stage = prev; render(); toast(e.message); }
}
(() => {
  const kb = $("#kanban");
  kb.addEventListener("dragstart", e => {
    const c = e.target.closest(".pc"); if (!c) return;
    dragId = c.dataset.id; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", dragId); c.classList.add("dragging");
  });
  kb.addEventListener("dragend", () => { dragId = null; kb.querySelectorAll(".dragging,.over").forEach(x => x.classList.remove("dragging","over")); });
  kb.addEventListener("dragover", e => {
    const col = e.target.closest(".pcol"); if (!col) return;
    e.preventDefault(); kb.querySelectorAll(".over").forEach(x => x !== col && x.classList.remove("over")); col.classList.add("over");
  });
  kb.addEventListener("drop", e => {
    const col = e.target.closest(".pcol"); if (!col) return;
    e.preventDefault(); col.classList.remove("over");
    moveStage((e.dataTransfer && e.dataTransfer.getData("text/plain")) || dragId, col.dataset.stage);
  });
})();

/* ---------- lead page ---------- */
function renderDetail(){
  const l = byId(current), a = l.analysis, ready = isReady(l);
  const keys = Object.keys(l.breakdown).filter(k => MAX[k]);
  const rows = keys.map(k => `<tr><td style="width:110px">${LABEL[k]}</td><td><div class="track"><div class="fill" style="width:${l.breakdown[k]/MAX[k]*100}%"></div></div></td><td class="v">${l.breakdown[k]}/${MAX[k]}</td></tr>`).join("")
    + (l.breakdown.activity ? `<tr><td>Recent activity</td><td></td><td class="v ${l.breakdown.activity>0?"up":"down"}">${l.breakdown.activity>0?"+":""}${l.breakdown.activity}</td></tr>` : "");
  const lc = lastCall[l.id], hasScores = lc && lc.score_before != null;
  const phone = l.form.phone
    ? `<span class="phone-line">${esc(l.form.phone)} · <button class="linkbtn" data-phone="${l.id}">Edit</button></span>`
    : `<button class="linkbtn" data-phone="${l.id}">Add phone number</button>`;
  const stageSel = `<label class="muted" style="font-size:12px">Stage <select class="stage-sel" id="stageSel">${STAGES.map(([k,t]) => `<option value="${k}" ${k === (l.stage||"new") ? "selected" : ""}>${t}</option>`).join("")}</select></label>`;
  const reply = a.suggested_response.text;
  const ai = '<span class="ai">AI</span>';

  // ---- top: who, how hot, what to do next ----
  const top = `<div class="card"><div class="card-b">
      <div class="lead-head">
        <div style="display:flex;gap:12px;align-items:center">${avatar(l)}<div><h2>${esc(l.form.name)}</h2><div class="muted" style="font-size:13px">${[l.form.location,l.form.requirement,l.form.budget,l.form.timeline].filter(Boolean).map(esc).join(" · ")}</div><div>${phone}</div></div></div>
        ${ready ? `<div class="score">${l.score}<small>/100</small></div>` : ""}
      </div>
      <div style="display:flex;gap:8px;margin:12px 0 ${ready ? 16 : 0}px;flex-wrap:wrap;align-items:center">${tagPill(l)}
        ${l.override ? `<span class="manual">changed by you</span><span class="muted" style="font-size:12px">AI said ${esc(l.override.from)}: "${esc(l.override.reason)}"</span>` : ""}
        ${l.flags.includes("high_value") ? '<span class="pill">high value</span>' : ""}
        ${l.flags.includes("borderline") ? '<span class="pill" title="Within 3 points of a cutoff">borderline</span>' : ""}
        ${l.injection_suspected ? '<span class="pill warn" title="This message contains text aimed at the AI. Its instructions were ignored, but please check the details yourself.">message tries to instruct the AI</span>' : ""}
        <span class="pill ${isLate(l)?"late":""}">${l.waiting_minutes == null ? "replied" : "waiting " + ago(l.waiting_minutes)}</span>
        ${stageSel}
        <span style="margin-left:auto;display:flex;gap:6px">
          <button class="btn primary sm" data-call="${l.id}">Log call</button>
          ${ready ? `<button class="btn sm" data-override="${l.id}">Change priority</button>` : ""}
        </span>
      </div>
      ${lc ? `<div class="callres" style="margin:0 0 16px">${hasScores ? `<span class="delta ${lc.score_after>=lc.score_before?"up":"down"}">${lc.score_before} → ${lc.score_after}</span>` : ""}<span><b>${OUTCOME[lc.outcome]}</b>${hasScores && lc.tag_before!==lc.tag_after?` · moved ${lc.tag_before} → ${lc.tag_after}`:""}${lc.summary ? ". " + esc(lc.summary) : ". Call saved."}</span></div>` : ""}
      ${ready ? `<div class="sec-t">Recommended next action ${ai}</div>
      <div class="next"><div><b>${esc(a.next_action.action)}</b><span>${esc(a.next_action.when)}${a.next_action.why ? " · " + esc(a.next_action.why) : ""}</span></div></div>` : ""}
    </div></div>`;

  // ---- left: understand the customer and prepare ----
  const understand = `
    <div class="card pane"><div class="sec-t">Lead summary ${ai}</div><p class="txt">${esc(a.summary)}</p>
      <div class="sec-t" style="margin-top:16px">Customer intent ${ai}</div><p class="txt">${esc(a.intent)}</p></div>
    <div class="card pane"><div class="sec-t">Key requirements ${ai}</div>
      <div class="chips">${a.key_requirements.map(r=>`<span class="chip">${esc(r)}</span>`).join("") || '<span class="muted">None stated</span>'}</div>
      ${a.missing_info.length ? `<div class="sec-t" style="margin-top:16px">Still to find out</div><div class="chips">${a.missing_info.map(r=>`<span class="chip miss">${esc(r)}</span>`).join("")}</div>` : ""}</div>
    <div class="card pane"><div class="sec-t">Objections / concerns ${ai}</div>
      ${a.objections.length ? a.objections.map(o=>`<div class="obj"><b>${esc(o.concern)}</b><span>${esc(o.how_to_handle)}</span></div>`).join("") : '<span class="muted">No concerns raised.</span>'}</div>
    ${a.call_talking_points.length ? `<div class="card pane"><div class="sec-t">On the call</div><ul class="tp">${a.call_talking_points.map(p=>`<li>${esc(p)}</li>`).join("")}</ul></div>` : ""}
    ${l.calls.length ? `<div class="card pane"><div class="sec-t">Call log</div><ul class="hist">${[...l.calls].reverse().map(c=>`<li><span class="muted">${fmtTime(c.at)}</span><b class="${c.score_after >= c.score_before ? "up" : "down"}">${c.score_before == null ? "" : c.score_before + "→" + c.score_after}</b><span><b>${OUTCOME[c.outcome]}</b>${c.summary ? " · " + esc(c.summary) : c.notes ? " · " + esc(c.notes) : ""}</span></li>`).join("")}</ul></div>` : ""}`;

  // ---- right: reply and score ----
  const act = `
    <div class="card pane"><div class="sec-t">Suggested response · ${esc(a.suggested_response.channel)} ${ai}</div>
      <div class="reply">${esc(reply)}</div>
      <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
        <button class="btn primary sm" id="copy"><svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>Copy reply</button>
        <a class="btn wa sm" id="waBtn" data-wa="${l.id}" href="${esc(Lib.waLink(l.form.phone, reply))}" target="_blank" rel="noopener" title="${l.form.phone ? "Open WhatsApp with this reply" : "Open WhatsApp and choose the contact"}">${WA_ICON}WhatsApp</a>
        ${l.waiting_minutes != null ? `<button class="btn sm" data-responded="${l.id}">Mark as replied</button>` : ""}
      </div>
      <div class="rewrite"><span class="muted">Rewrite:</span>
        <button class="btn sm" data-ask="Make my reply more assertive">More assertive</button>
        <button class="btn sm" data-ask="Make my reply shorter">Shorter</button>
        <button class="btn sm" data-ask="Rewrite my reply in Hinglish">In Hinglish</button></div></div>
    <div class="card pane"><div class="sec-t">Why this score<span class="help" title="The AI finds the facts; a fixed rubric turns them into points. The same facts always give the same score.">?</span></div>
      ${rows ? `<table class="sb">${rows}</table>` : `<span class="muted">${esc((l.reasons[0]||"").replace(/^.*?: /,""))}</span>`}</div>`;

  // ---- not analysed yet: keep it simple ----
  const pending = `<div class="card pane"><div class="note-card" style="margin:0">${isAnalyzing(l)
      ? `<span><b>This lead is being analysed.</b> It will fill in here in a few seconds.</span><span class="busy-text"><i class="spinner"></i>Analyzing…</span>`
      : `<span><b>Your lead is saved, but the analysis didn't finish.</b> ${esc(l.ai_error || "")}</span>
      ${analyzing.has(l.id) ? `<span class="busy-text"><i class="spinner"></i>Analyzing…</span>` : `<button class="btn primary sm" data-reanalyze="${l.id}">Re-analyze</button>`}`}</div></div>
    <div class="card pane"><div class="sec-t">Customer message</div><div class="msgbox" style="margin:0">${esc(l.form.message)}</div></div>`;

  const foot = `<div class="card pane foot">${ready ? `<details><summary>Original customer message</summary><div class="msgbox">${esc(l.form.message)}</div></details>` : ""}
      <div style="display:flex;gap:16px;margin-top:${ready ? 12 : 0}px">
        ${ready ? (analyzing.has(l.id) ? `<span class="busy-text"><i class="spinner"></i>Re-analyzing…</span>` : `<button class="linkbtn" data-reanalyze="${l.id}">Re-analyze with AI</button>`) : ""}
        <button class="linkbtn danger" data-delete="${l.id}">Delete lead</button></div></div>`;

  $("#detail").innerHTML = top + (ready ? `<div class="lead-cols"><div class="col">${understand}</div><div class="col">${act}</div></div>` : pending) + foot;
  const copy = $("#copy"); if (copy) copy.onclick = () => { navigator.clipboard?.writeText(reply); toast("Reply copied. Paste it in WhatsApp, then tap “Mark as replied”."); };
}

/* ---------- chat widget (floating, lead page only) ---------- */
function syncChat(){
  const l = view === "lead" ? byId(current) : null, show = !!l && isReady(l);
  $("#chatFab").classList.toggle("hidden", !show || chatOpen);
  $("#chatPanel").classList.toggle("hidden", !show || !chatOpen);
  if (show && chatOpen) renderChat();
}
function renderChat(){
  const l = byId(current), draft = $("#ask") ? $("#ask").value : "";
  const msgs = l.chat.map((m,i) => m.role === "user" ? `<div class="m me">${esc(m.text)}</div>`
    : m.role === "error" ? `<div class="m err">${esc(m.text)}</div>`
    : `<div class="m bot">${esc(m.text)}${m.is_reply ? `<button class="btn sm use" data-use="${i}">Use as suggested response</button>` : ""}</div>`).join("");
  $("#chatPanel").innerHTML = `
    <div class="cw-head">${avatar(l)}<div class="t"><b>Ask about ${esc(first(l))}</b><span>Answers use only this lead's details</span></div>
      <button class="cw-x" data-chat-close aria-label="Close chat"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
    <div class="ctx">AI knows: <b>${esc(l.form.requirement || "requirement")}</b>, <b>${esc(l.form.location || "location")}</b>, <b>${esc(l.form.budget || "budget")}</b>, the message, the analysis and the call log.</div>
    <div class="msgs" id="msgs">${msgs || `<div class="muted" style="font-size:13px;text-align:center;margin:auto 0">Ask anything about this lead,<br>or tap a question below.</div>`}
      ${chatBusy === l.id ? `<div class="typing">Thinking about ${esc(first(l))}'s lead…</div>` : ""}</div>
    <div class="quick">${["What should I emphasize on the call?","Make my reply more assertive","How do I handle their concerns?","What should I ask them next?"].map(q=>`<button data-ask="${esc(q)}">${esc(q)}</button>`).join("")}</div>
    <form class="composer" id="composer"><input id="ask" maxlength="2000" placeholder="Ask about ${esc(first(l))}…" autocomplete="off"><button class="btn primary sm">Send</button></form>`;
  const box = $("#msgs"); box.scrollTop = box.scrollHeight;
  if (draft) $("#ask").value = draft;
  $("#composer").onsubmit = e => { e.preventDefault(); const v = $("#ask").value.trim(); if (v) { $("#ask").value = ""; ask(v); } };
}
async function ask(q){
  const l = byId(current); if (!l || chatBusy) return;
  chatOpen = true; chatBusy = l.id; l.chat.push({role:"user", text:q}); syncChat();
  try { l.chat.push((await api(`/api/leads/${l.id}/chat`, {method:"POST", body:{message:q}})).reply); }
  catch (e) { l.chat.push({role:"error", text:e.message}); }
  chatBusy = null; syncChat();
}

/* ---------- today's calls ---------- */
function renderCalls(){
  const plan = buildPlan();
  $("#planCount").textContent = `${plan.filter(x => !x.done).length} left`;
  $("#plan").innerHTML = plan.map(({l, time, fixed, done}) =>
    `<div class="slot ${done?"done":""}"><div class="time">${time}</div>
      <div><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>${esc(l.form.name)}</b>${tagPill(l)}<span class="muted" style="font-size:13px">${l.score}</span>${fixed?'<span class="pill">time asked by customer</span>':""}${isLate(l)&&!done?`<span class="late" style="font-size:12px">⏱ waiting ${ago(l.waiting_minutes)}</span>`:""}</div>
        <div class="w">→ ${esc(l.analysis.next_action.action)}</div></div>
      <div style="display:flex;gap:6px"><button class="btn sm" data-open="${l.id}">Open</button><button class="btn sm ${done?"":"primary"}" data-call="${l.id}">${done?"Log again":"Log call"}</button></div></div>`
  ).join("") || `<div class="card-b muted">No hot or warm leads yet. Add a lead to get started.</div>`;
}

/* ---------- add lead ---------- */
const addShow = which => ["addForm","progressBody","addResult"].forEach(id => $("#"+id).classList.toggle("hidden", id !== which));
function openAdd(){
  $("#importModal").classList.remove("open");
  $("#addForm").reset(); $("#addErr").classList.add("hidden"); addShow("addForm"); addResult = null;
  $("#sample").classList.remove("hidden");
  $("#mTitle").textContent = "Add a lead"; $("#mSub").textContent = "Fill in what you know. The message matters most.";
  $("#addModal").classList.add("open");
}
$("#sample").onclick = () => {
  if (!SAMPLES.length) return;
  const s = SAMPLES[sampleIdx++ % SAMPLES.length], field = k => $(`#addForm [name="${k}"]`);
  ["name","location","requirement","budget","message"].forEach(k => field(k).value = s[k] || "");
  const opt = [...field("timeline").options].find(o => (s.timeline || "").toLowerCase().startsWith(o.text.slice(0,3).toLowerCase()));
  field("timeline").value = opt ? opt.text : "Not decided";
};
$("#addForm").onsubmit = async e => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  addBusy = true; addShow("progressBody"); $("#sample").classList.add("hidden");
  $("#mTitle").textContent = `Analyzing ${body.name}…`; $("#mSub").textContent = "Usually takes 5–10 seconds";
  const items = [...document.querySelectorAll("#progress li")]; items.forEach(li => { li.className = ""; li.querySelector("i").textContent = ""; });
  let step = 0;
  const tick = () => { if (step) { items[step-1].className = "done"; items[step-1].querySelector("i").textContent = "✓"; } if (step < items.length) items[step].className = "doing"; step++; };
  tick(); const timer = setInterval(() => { if (step < items.length) tick(); }, 2200);
  try {
    const {lead} = await api("/api/leads", {method:"POST", body, timeout:90000});
    clearInterval(timer); upsert(lead); addBusy = false;
    if (isReady(lead)) { $("#addModal").classList.remove("open"); go("lead", lead.id); toast(`${lead.form.name} added as ${lead.tag} (${lead.score})`); }
    else { addResult = {id: lead.id}; renderAddResult(); render(); }
  } catch (err) {
    clearInterval(timer); addBusy = false; addShow("addForm"); $("#sample").classList.remove("hidden");
    $("#mTitle").textContent = "Add a lead"; $("#mSub").textContent = "Your details are still here.";
    $("#addErr").textContent = err.message; $("#addErr").classList.remove("hidden");
  }
};
function renderAddResult(){
  const l = byId(addResult.id), box = $("#addResult"); addShow("addResult");
  $("#sample").classList.add("hidden");
  $("#mTitle").textContent = isReady(l) ? "Analysis ready" : "Lead saved"; $("#mSub").textContent = esc(l.form.name);
  if (analyzing.has(l.id)) box.innerHTML = `<p class="busy-text" style="font-size:14px"><i class="spinner"></i>Analyzing ${esc(l.form.name)}…</p>`;
  else if (isReady(l)) box.innerHTML = `<p><span class="imp-ok">✓</span> ${tagPill(l)} <b>${l.score}</b> / 100. The analysis is ready.</p>
    <div class="row-actions"><button class="btn primary" data-add-open="${l.id}">Open lead</button><button class="btn" data-action="add">Add another</button></div>`;
  else box.innerHTML = `<p><b>${esc(l.form.name)} is saved</b>, but the analysis didn't finish.</p><p class="muted" style="font-size:13px">${esc(l.ai_error || "")}</p>
    <div class="row-actions"><button class="btn primary" data-add-reanalyze="${l.id}">Re-analyze</button><button class="btn" data-add-open="${l.id}">Open lead</button></div>`;
}

/* ---------- import from CSV ---------- */
const impShow = which => ["impStart","impPreview","impRun"].forEach(id => $("#"+id).classList.toggle("hidden", id !== which));
function openImport(){
  $("#addModal").classList.remove("open");
  $("#importModal").classList.add("open");
  if (imp && imp.running) { impShow("impRun"); renderImport(); return; }
  imp = null; impShow("impStart"); $("#impErr").classList.add("hidden"); $("#csvFile").value = "";
  $("#impSub").textContent = "Add many leads at once. Each one is analysed by the AI.";
}
function impError(msg){ $("#impErr").textContent = msg; $("#impErr").classList.remove("hidden"); }
function readCSV(file){
  if (!file) return;
  if (file.size > 1_000_000) return impError("That file is too large. Keep it under 1 MB.");
  const r = new FileReader();
  r.onload = () => previewImport(String(r.result));
  r.onerror = () => impError("We couldn't read that file. Please try another one.");
  r.readAsText(file);
}
function previewImport(text){
  const res = Lib.leadsFromCSV(text);
  if (res.error) return impError(res.error);
  if (!res.leads.length) return impError("No usable leads found. Each lead needs a name and a message.");
  imp = {rows: res.leads.map((f, i) => ({i, form: f, state: "queued"})), running: false};
  const sk = res.skipped.length ? `<p class="muted" style="font-size:13px">${res.skipped.length} row${res.skipped.length > 1 ? "s" : ""} skipped (${[...new Set(res.skipped.map(s => s.reason))].join(", ")}).</p>` : "";
  const tr = res.truncated ? `<p class="muted" style="font-size:13px">Only the first ${Lib.MAX_IMPORT_ROWS} leads will be imported. ${res.truncated} more were left out.</p>` : "";
  $("#impPreview").innerHTML = `<p style="margin-top:0"><b>${res.leads.length} lead${res.leads.length > 1 ? "s" : ""}</b> found.</p>${sk}${tr}
    <div class="imp-list">${res.leads.slice(0,5).map(f => `<div class="imp-row"><span class="ic">•</span><div><b>${esc(f.name)}</b><div class="sub">${[f.location, f.requirement, f.budget].filter(Boolean).map(esc).join(" · ") || "No details"}</div></div></div>`).join("")}
      ${res.leads.length > 5 ? `<div class="imp-row"><span class="ic"></span><div class="sub">…and ${res.leads.length - 5} more</div></div>` : ""}</div>
    <div class="row-actions"><button class="btn" data-imp-again>Choose another file</button><button class="btn primary" data-imp-start>Import ${res.leads.length} lead${res.leads.length > 1 ? "s" : ""}</button></div>`;
  impShow("impPreview");
}
function impRowHTML(r){
  const f = r.form, base = `<div><b>${esc(f.name)}</b>`;
  if (r.state === "queued") return `<div class="imp-row"><span class="ic imp-q">○</span>${base}<div class="sub">Waiting</div></div><span></span></div>`;
  if (r.state === "working") return `<div class="imp-row"><span class="ic"><i class="spinner"></i></span>${base}<div class="sub">Analyzing…</div></div><span></span></div>`;
  if (r.state === "done") { const l = byId(r.leadId); return `<div class="imp-row"><span class="ic imp-ok">✓</span>${base}<div class="sub">${l ? `${tagPill(l)} ${l.score}` : "Analysed"}</div></div><div class="acts"><button class="btn sm" data-imp-open="${r.i}">Open</button></div></div>`; }
  if (r.state === "failed") return `<div class="imp-row"><span class="ic" style="color:var(--warm)">!</span>${base}<div class="err">Saved. ${esc(r.error || "The analysis didn't finish.")}</div></div><div class="acts"><button class="btn sm" data-imp-re="${r.i}">Re-analyze</button><button class="btn sm" data-imp-open="${r.i}">Open</button></div></div>`;
  return `<div class="imp-row"><span class="ic" style="color:var(--hot)">!</span>${base}<div class="err">Not saved. ${esc(r.error || "Something went wrong.")}</div></div><div class="acts"><button class="btn sm" data-imp-retry="${r.i}">Try again</button></div></div>`;
}
function renderImport(){
  if (!imp) return;
  const rows = imp.rows, done = rows.filter(r => r.state === "done").length;
  const settled = rows.filter(r => !["queued","working"].includes(r.state)).length;
  const bad = rows.filter(r => r.state === "failed" || r.state === "error").length;
  $("#impSub").textContent = imp.running ? `Importing ${settled} of ${rows.length}…` : bad ? `${done} analysed, ${bad} need attention` : `All ${rows.length} leads analysed`;
  $("#impRun").innerHTML = `<div class="bar-track"><i style="width:${settled / rows.length * 100}%"></i></div>
    <div class="imp-list">${rows.map(impRowHTML).join("")}</div>
    <div class="row-actions">${!imp.running && bad ? `<button class="btn" data-imp-retry-all>Try the ${bad} unfinished again</button>` : ""}
      <button class="btn ${imp.running ? "" : "primary"}" data-imp-done>${imp.running ? "Keep working in background" : "View leads"}</button></div>`;
}
async function importOne(row){
  row.state = "working"; renderImport();
  try {
    const {lead} = await api("/api/leads", {method:"POST", body:row.form, timeout:90000});
    upsert(lead); row.leadId = lead.id; row.state = isReady(lead) ? "done" : "failed"; row.error = lead.ai_error;
  } catch (e) { row.state = "error"; row.error = e.message; }
  renderImport(); render();
}
async function impReanalyze(row){
  row.state = "working"; renderImport();
  try { upsert((await api(`/api/leads/${row.leadId}/reanalyze`, {method:"POST", timeout:90000})).lead); row.state = "done"; }
  catch (e) { row.state = "failed"; row.error = e.message; }
  renderImport(); render();
}
async function runImport(only){
  imp.running = true; impShow("impRun"); renderImport();
  for (const row of imp.rows) {
    if (only ? !only.includes(row) : row.state !== "queued") continue;
    row.state === "failed" ? await impReanalyze(row) : await importOne(row);
  }
  imp.running = false; renderImport();
  const bad = imp.rows.filter(r => r.state === "failed" || r.state === "error").length;
  toast(bad ? `Import finished. ${bad} need attention.` : `Imported ${imp.rows.length} leads`);
}

/* ---------- log call ---------- */
function openCall(id){
  const l = byId(id); callId = id;
  $("#callForm").reset();
  $("#callSub").textContent = isReady(l) ? `${l.form.name} · currently ${l.priority} (${l.score})` : l.form.name;
  $("#callModal").classList.add("open");
}
$("#callForm").onsubmit = async e => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target));
  const btn = $("#callSave"); btn.disabled = true; btn.textContent = "Updating…";
  try {
    const res = await api(`/api/leads/${callId}/calls`, {method:"POST", body, timeout:90000});
    upsert(res.lead); lastCall[callId] = res.call;
    $("#callModal").classList.remove("open");
    const c = res.call;
    toast(res.ai_failed ? "Call saved. AI suggestions aren't available right now."
      : c.score_before == null ? "Call saved"
      : `Score ${c.score_before} → ${c.score_after}${c.tag_before !== c.tag_after ? ` · now ${c.tag_after}` : ""}`);
    if (view === "lead") render(); else go("lead", callId);
  } catch (err) { toast(err.message); }
  btn.disabled = false; btn.textContent = "Save and update score";
};

/* ---------- change priority ---------- */
function openOverride(id){
  const l = byId(id); ovId = id;
  $("#ovSub").textContent = `AI placed ${l.form.name} in ${l.tag} (score ${l.score}).`;
  $("#ovTo").value = l.override ? l.override.to : (l.tag === "Hot" ? "Warm" : "Hot");
  $("#ovReason").value = l.override?.reason || "";
  $("#ovModal").classList.add("open");
}
async function saveOverride(to, reason){
  try {
    upsert((await api(`/api/leads/${ovId}/override`, {method:"POST", body:{to, reason}})).lead);
    $("#ovModal").classList.remove("open"); render();
    toast(to ? `Moved to ${to}` : "Back to AI priority");
  } catch (e) { toast(e.message); }
}
$("#ovSave").onclick = () => { const l = byId(ovId), to = $("#ovTo").value; saveOverride(to === l.tag ? null : to, $("#ovReason").value.trim()); };
$("#ovReset").onclick = () => saveOverride(null, "");

/* ---------- phone ---------- */
function openPhone(id){
  const l = byId(id); phoneId = id;
  $("#phoneSub").textContent = l.form.name;
  $("#phoneForm [name=phone]").value = l.form.phone || "";
  $("#phoneModal").classList.add("open");
}
$("#phoneForm").onsubmit = async e => {
  e.preventDefault();
  try {
    upsert((await api(`/api/leads/${phoneId}`, {method:"PATCH", body:{phone: $("#phoneForm [name=phone]").value}})).lead);
    $("#phoneModal").classList.remove("open"); render(); toast("Phone number saved");
  } catch (err) { toast(err.message); }
};

/* ---------- events ---------- */
const CLICKABLE = "[data-view],[data-open],[data-mode],[data-action],[data-override],[data-ask],[data-use],[data-call],[data-close],[data-responded],[data-reanalyze],[data-delete],[data-phone],[data-wa],[data-advance],[data-retryall],[data-chat-close],[data-add-open],[data-add-reanalyze],[data-imp-open],[data-imp-re],[data-imp-retry],[data-imp-retry-all],[data-imp-start],[data-imp-again],[data-imp-done],[data-sample-import]";
document.addEventListener("click", async e => {
  const b = e.target.closest(CLICKABLE);
  if (!b) return;
  const d = b.dataset;
  if (d.advance) { e.stopPropagation(); const l = byId(d.advance), i = STAGES.findIndex(s => s[0] === (l.stage || "new")); return moveStage(d.advance, STAGES[i+1][0]); }
  if (d.wa !== undefined) return toast((byId(d.wa) || byId(current))?.form.phone ? "WhatsApp opened with your reply. After you send it, tap “Mark as replied”." : "WhatsApp opened. Choose the contact, send, then tap “Mark as replied”.");
  if (d.view) go(d.view);
  if (d.open) { $("#importModal").classList.remove("open"); go("lead", d.open); }
  if (d.mode) { mode = d.mode; renderLeads(); }
  if (d.action === "add") openAdd();
  if (d.action === "import") openImport();
  if (d.sampleImport !== undefined) importSample();
  if (d.override) openOverride(d.override);
  if (d.call) openCall(d.call);
  if (d.phone) openPhone(d.phone);
  if (d.ask) ask(d.ask);
  if (d.chatClose !== undefined) { chatOpen = false; syncChat(); }
  if (d.close !== undefined && !addBusy) b.closest(".modal").classList.remove("open");
  if (d.use) {
    const l = byId(current), text = l.chat[d.use].text;
    try { upsert((await api(`/api/leads/${l.id}/reply`, {method:"POST", body:{text}})).lead); render(); toast("Suggested response updated"); } catch (err) { toast(err.message); }
  }
  if (d.responded) { try { upsert((await api(`/api/leads/${d.responded}/responded`, {method:"POST"})).lead); render(); toast("Marked as replied"); } catch (err) { toast(err.message); } }
  if (d.reanalyze) reanalyze(d.reanalyze);
  if (d.retryall !== undefined) retryAll();
  if (d.delete && confirm("Delete this lead?")) {
    try { await api(`/api/leads/${d.delete}`, {method:"DELETE"}); LEADS = LEADS.filter(l => l.id !== d.delete); go(origin === "lead" ? "leads" : origin); toast("Lead deleted"); } catch (err) { toast(err.message); }
  }
  // add-lead result panel (updates in place)
  if (d.addOpen) { $("#addModal").classList.remove("open"); go("lead", d.addOpen); }
  if (d.addReanalyze) { const done = reanalyze(d.addReanalyze, true); renderAddResult(); await done; if (addResult) renderAddResult(); }
  // import panel (updates in place)
  if (d.impOpen) { $("#importModal").classList.remove("open"); go("lead", imp.rows[d.impOpen].leadId); }
  if (d.impRe) impReanalyze(imp.rows[d.impRe]);
  if (d.impRetry) importOne(imp.rows[d.impRetry]);
  if (d.impRetryAll !== undefined) runImport(imp.rows.filter(r => r.state === "failed" || r.state === "error"));
  if (d.impStart !== undefined) runImport();
  if (d.impAgain !== undefined) { imp = null; impShow("impStart"); $("#csvFile").value = ""; }
  if (d.impDone !== undefined) { $("#importModal").classList.remove("open"); go("leads"); }
});
document.addEventListener("change", e => { if (e.target.id === "stageSel") moveStage(current, e.target.value); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && !addBusy) document.querySelectorAll(".modal.open").forEach(m => m.classList.remove("open")); });
document.querySelectorAll(".modal").forEach(m => m.addEventListener("click", e => { if (e.target === m && !addBusy) m.classList.remove("open"); }));
$("#chatFab").onclick = () => { chatOpen = true; syncChat(); setTimeout(() => $("#ask")?.focus(), 50); };
$("#csvFile").onchange = e => readCSV(e.target.files[0]);
const drop = $("#drop");
["dragenter","dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave","drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => readCSV(e.dataTransfer.files[0]));
$("#back").onclick = () => go(origin === "lead" ? "leads" : origin);
$("#q").oninput = e => { query = e.target.value.toLowerCase(); renderLeads(); };
$("#menu").onclick = () => $("#side").classList.toggle("open");
$("#theme").onclick = () => { const r = document.documentElement; r.dataset.theme = r.dataset.theme === "dark" ? "light" : "dark"; try { localStorage.setItem("theme", r.dataset.theme); } catch {} };

/* ---------- start ---------- */
async function load(){ LEADS = (await api("/api/leads")).leads; render(); }
(async () => {
  try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch {}
  const notes = [];
  try {
    const [health] = await Promise.all([api("/api/health"), fetch("/static/samples.json").then(r => r.json()).then(s => SAMPLES = s).catch(() => {})]);
    if (!health.ai_ready) notes.push(`<div class="banner warn"><b>AI analysis is unavailable right now.</b> You can still browse and manage your leads. New leads are saved and can be analysed later.</div>`);
    if (!health.storage_ready) notes.push(`<div class="banner warn"><b>Saving is unavailable right now.</b> Leads can't be saved or loaded until this is fixed. Please try again in a moment.</div>`);
    await load();
  } catch (e) { if (!notes.some(n => n.includes("Saving"))) notes.push(`<div class="banner warn">${esc(e.message)}</div>`); }
  $("#banners").innerHTML = notes.join("");
  routeFromHash();
})();
window.addEventListener("hashchange", () => { const want = location.hash.replace("#", ""); if (want !== (view === "lead" ? "lead/" + current : view)) routeFromHash(); });
// keep waiting timers fresh on the list pages, without interrupting typing, chats or running imports
setInterval(() => {
  const typing = ["INPUT","TEXTAREA","SELECT"].includes(document.activeElement?.tagName);
  const idle = !chatBusy && !addBusy && !analyzing.size && !(imp && imp.running) && !typing && !document.querySelector(".modal.open");
  if (idle && !document.hidden && ["overview","leads","pipeline","calls"].includes(view)) load().catch(() => {});
}, 60000);
