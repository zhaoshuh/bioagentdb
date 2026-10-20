/* BioAgentDB v2 — scroll narrative interactions */
"use strict";

const PAGE = 12;
let offset = 0;
let total = 0;

const LABEL = {
  "alzheimers-disease": { n: "Alzheimer's disease", tag: "AD and related dementias" },
  "als": { n: "Amyotrophic lateral sclerosis", tag: "ALS and the ALS&ndash;FTD spectrum" },
  "parkinsons-disease": { n: "Parkinson's disease", tag: "PD, PD dementia and Lewy body disease" },
};

const MOD_LABEL = {
  transcriptomics: "transcriptomics", proteomics: "proteomics", epigenomics: "epigenomics",
  genomics: "genomics", scrna: "scRNA-seq", snrna: "snRNA-seq", scatac: "scATAC-seq",
  spatial_transcriptomics: "spatial transcriptomics",
  spatial_proteomics: "spatial proteomics", multi_omics: "multi-omics",
  microbiome: "microbiome", metagenomics: "metagenomics", metabolomics: "metabolomics",
  lipidomics: "lipidomics", phosphoproteomics: "phosphoproteomics",
};

/* ---------------- api ---------------- */
async function api(path, body) {
  const opt = { headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opt.method = "POST", opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error((await r.text()).slice(0, 300));
  return r.json();
}

/* ---------------- reveal on scroll ---------------- */
document.documentElement.classList.add("js");
document.querySelectorAll(".section").forEach((s) => s.classList.add("reveal"));
const io = new IntersectionObserver((entries) => {
  entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
}, { threshold: 0.08 });
document.querySelectorAll(".reveal").forEach((s) => io.observe(s));

/* ---------------- scroll spy ---------------- */
const spy = new IntersectionObserver((entries) => {
  entries.forEach((e) => {
    if (e.isIntersecting) {
      document.querySelectorAll(".navlinks a").forEach((a) => a.classList.remove("active"));
      const link = document.querySelector(`.navlinks a[href="#${e.target.id}"]`);
      if (link) link.classList.add("active");
    }
  });
}, { rootMargin: "-45% 0px -50% 0px" });
document.querySelectorAll("#diseases,#how,#ledger").forEach((s) => spy.observe(s));

/* ---------------- stats / diseases ---------------- */
async function refreshStats() {
  try {
    const s = await api("/api/stats", undefined);
    document.getElementById("hs-papers").textContent = s.papers_with_data ?? 0;
    document.getElementById("hs-scanned").textContent = s.papers_mined ?? s.papers_total ?? 0;
    const sp = document.getElementById("seg-papers");
    if (sp) sp.textContent = s.papers_with_data ?? 0;
    const sd = document.getElementById("seg-datasets");
    if (sd) sd.textContent = s.datasets_verified ?? 0;
  } catch (e) {}
  try {
    const t = await api("/api/themes", undefined);
    const items = t.items || [];
    items.forEach((x) => { if (x.slug) THEME_NAMES[x.slug] = x.name; });
    // L-0231/L-0232：F4 守卫**无条件消费**（列表未渲染只置位、不晚触发）；
    // 仅"已渲染且在第 1 页"时按模式重渲（绝不重置分页/滚动、不跨模式覆盖）
    if (!THEME_NAMES_HYDRATED) {
      THEME_NAMES_HYDRATED = true;
      if (document.querySelector("#rec-list .record") && offset === 0) {
        try { (ledgerMode === "datasets" ? loadDatasets(0) : loadRecords(0)); } catch (e) {}
      }
    }
    // hero 主题数 = 有记录的主题数（用户口径）
    const withRec = items.filter((x) => (x.records_count || 0) > 0);
    const el = document.getElementById("hs-themes");
    if (el) el.textContent = withRec.length;
    renderDiseases(items);
  } catch (e) {}
}

const DISEASE_ROWS = 6;   // 最多两行（3 列 × 2）
let diseasesExpanded = false;
let THEME_NAMES = {};     // slug → name（含 topic 主题；L-0225）
let THEME_NAMES_HYDRATED = false;   // F4 一次性重渲守卫（L-0231）

function renderDiseases(items) {
  const wrap = document.getElementById("diseases-list");
  if (!wrap) return;
  wrap.innerHTML = "";
  if (!items.length) {
    wrap.innerHTML = '<p class="muted" style="grid-column:1/-1">No topics yet — open ' +
      '<a href="/ask">Ming</a> and mine what you need.</p>';
    const more0 = document.getElementById("diseases-more");
    if (more0) more0.style.display = "none";
    return;
  }
  items.forEach((t, i) => {
    const d = document.createElement("article");
    d.className = "disease" + (i >= DISEASE_ROWS ? " extra" : "");
    const meta = LABEL[t.slug];
    const name = meta ? meta.n : t.name;
    const tag = meta ? meta.tag : esc(cleanQuote(t.description || "", 120));
    const count = t.records_count || 0;
    const target = t.target_records || 0;
    const pct = target ? Math.min(100, Math.round((count / target) * 100)) : (count > 0 ? 100 : 0);
    d.innerHTML = `
      <h3 class="dname">${esc(name)}${(t.kind || "disease") === "topic" ? ' <span class="chip">topic</span>' : ""}</h3>
      <p class="dd">${tag}</p>
      <div class="dcount">${count}<small> papers with data &middot; BioAgentDB</small></div>
      <div class="bar"><div style="width:${pct}%"></div></div>
      <span class="go">Browse records &rarr;</span>`;
    d.onclick = () => { setTheme(t.slug); document.getElementById("ledger").scrollIntoView({ behavior: "smooth" }); };
    wrap.appendChild(d);
  });
  wrap.classList.toggle("expanded", diseasesExpanded);
  const btn = document.getElementById("diseases-more");
  if (btn) {
    if (items.length > DISEASE_ROWS) {
      btn.style.display = "inline-block";
      btn.textContent = diseasesExpanded ? "Show less" : `Show all ${items.length} topics`;
    } else {
      btn.style.display = "none";
    }
  }
}

window.toggleDiseases = function () {
  diseasesExpanded = !diseasesExpanded;
  document.getElementById("diseases-list").classList.toggle("expanded", diseasesExpanded);
  const n = document.querySelectorAll("#diseases-list .disease").length;
  const btn = document.getElementById("diseases-more");
  if (btn) btn.textContent = diseasesExpanded ? "Show less" : `Show all ${n} topics`;
};

/* agent live line inside the illustration */
async function renderAgentStatus() {
  try {
    const [s, m] = await Promise.all([api("/api/stats", undefined), api("/api/miner/status", undefined)]);
    const running = (m.processes || []).some((p) => p.status === "running");
    const queued = Math.max(0, (s.papers_pending !== undefined)
      ? (s.papers_pending || 0)
      : (s.papers_total || 0) - (s.papers_mined || 0));
    const st = document.getElementById("live-status");
    if (st) st.textContent = running ? "the AI agent is mining" : "agent idle";
    const dot = document.getElementById("live-dot");
    if (dot) dot.className = "live-dot" + (running ? "" : " idle");
    const ldot = document.getElementById("ledger-dot");
    if (ldot) ldot.className = "live-dot" + (running ? "" : " idle");
    const q = document.getElementById("live-queued");
    if (q) q.textContent = queued.toLocaleString();
  } catch (e) { /* decorative */ }
}

function setTheme(slug) {
  document.getElementById("rec-theme").value = slug;
  loadLedger(0);   // L-0232：按当前模式（datasets 下不再被切回 papers）
}

/* 选出"引文里真的含有该登记号"的数据集-证据对（保证引文自洽） */
function pickEvidence(it) {
  const ds = it.datasets || [];
  for (const x of ds) {
    const links = x.links || [];
    const primary = links.find((z) => z.is_primary) || links[0] || {};
    const acc = primary.accession || "";
    if (!acc) continue;
    const ev = (it.evidence || []).find((e) => e.dataset_id === x.id
      && (e.quote || "").toUpperCase().includes(acc.toUpperCase()));
    if (ev) return { acc: acc, quote: ev.quote, n: ds.length };
  }
  const x = ds[0];
  const primary = x ? ((x.links || []).find((z) => z.is_primary) || (x.links || [])[0] || {}) : {};
  const ev = (it.evidence || []).find((e) => x && e.dataset_id === x.id) || (it.evidence || [])[0] || {};
  return { acc: primary.accession || "", quote: ev.quote || "", n: ds.length };
}

/* ---------------- hero evidence band ---------------- */
async function loadHeroSample() {
  try {
    const head = await api("/api/records", { q: "", theme: "", modality: "", organism: "", limit: 1, offset: 0, sort: "year" });
    const n = head.total || 0;
    if (!n) return;
    const o = Math.floor(Math.random() * Math.min(n, 50));
    const data = o ? await api("/api/records", { q: "", theme: "", modality: "", organism: "", limit: 1, offset: o, sort: "year" }) : head;
    const it = data.items[0];
    if (!it) return;
    const pick = pickEvidence(it);
    const more = pick.n > 1 ? `  +${pick.n - 1} more` : "";
    document.getElementById("hero-quote").textContent =
      pick.quote ? `\u201C${quoteAround(pick.quote, pick.acc, 170)}\u201D` : "\u2026";
    document.getElementById("hero-src").textContent =
      `${cleanQuote(it.title, 76)} \u00b7 ${pick.acc}${more}`;
  } catch (e) {}
}

/* ---------------- ledger ---------------- */
async function loadFacets() {
  try {
    const f = await api("/api/facets", undefined);
    fill("rec-modality", f.modalities || [], " modality", (x) => x.modality,
         (x) => MOD_LABEL[x.modality] || x.modality);
    fill("rec-organism", f.organisms || [], " organism", (x) => x.organism);
  } catch (e) {}
  const sel = document.getElementById("rec-theme");
  const cur = sel.value;
  sel.innerHTML = '<option value="">All topics</option>';
  try {
    const t = await api("/api/themes", undefined);
    (t.items || []).forEach((th) => {
      const o = document.createElement("option");
      o.value = th.slug;
      o.textContent = (LABEL[th.slug] || { n: th.name }).n
        + (th.records_count ? ` (${th.records_count})` : "");
      sel.appendChild(o);
    });
  } catch (e) {}
  if (cur) sel.value = cur;
}
function fill(id, rows, suffix, pick, labelFn) {
  const sel = document.getElementById(id);
  const cur = sel.value;
  const plural = suffix.endsWith("y") ? suffix.slice(0, -1) + "ies" : suffix + "s";
  sel.innerHTML = `<option value="">All${plural}</option>`;
  rows.forEach((r) => {
    const o = document.createElement("option");
    const label = labelFn ? labelFn(r) : pick(r);
    o.value = pick(r); o.textContent = `${label} (${r.n})`; sel.appendChild(o);
  });
  if (cur) sel.value = cur;
}

async function loadRecords(p) {
  const start = Math.max(0, p || 0);
  const body = {
    q: document.getElementById("rec-q").value,
    theme: document.getElementById("rec-theme").value,
    modality: document.getElementById("rec-modality").value,
    organism: document.getElementById("rec-organism").value,
    statuses: document.getElementById("rec-status").value,
    limit: PAGE, offset: start, sort: "year",
  };
  let data;
  try { data = await api("/api/records", body); }
  catch (e) { document.getElementById("rec-list").innerHTML = `<div class="record">Search failed: ${esc(e.message)}</div>`; return; }
  total = data.total;
  document.getElementById("rec-count").textContent = `${total} paper(s) with data`;
  const wrap = document.getElementById("rec-list");
  if (start === 0) wrap.innerHTML = "";                       // 首页清空，其后追加
  if (!data.items.length && start === 0) {
    wrap.innerHTML = '<div class="record">No papers with public data yet &mdash; the miner is growing the catalog.</div>';
  }
  data.items.forEach(renderRecord(wrap));
  offset = start + data.items.length;                          // 下一次追加的起点
  document.getElementById("load-more").style.display = offset < total ? "inline-block" : "none";
}

function renderRecord(wrap) {
  return (it) => {
    try {
      const d = document.createElement("div");
      d.className = "record";
      const quote = (it.evidence || [{}])[0].quote || "";
      const accs = (it.datasets || []).slice(0, 5).map((x) => {
        const l = (x.links || []).find((z) => z.is_primary) || (x.links || [])[0];
        const acc = l ? l.accession : x.id;
        return `<span class="chip acc-chip" data-id="${esc(x.id)}" title="open dataset">${esc(acc)}${x.status === "paper_verified" ? " ~" : ""}</span>`;
      }).join("");
      const mods = [...new Set((it.datasets || []).flatMap((x) => x.modalities || []))].slice(0, 3);
      const dzs = (it.themes && it.themes.length) ? it.themes : (it.theme ? [it.theme] : []);
      d.innerHTML = `
        <h4>${esc(it.title)}</h4>
        <div class="chips">
          <span class="chip ok">${it.n_datasets} dataset${it.n_datasets === 1 ? "" : "s"}</span>
          ${dzs.map((t) => `<span class="chip dz">${esc((LABEL[t] || { n: THEME_NAMES[t] || t }).n)}</span>`).join("")}
          ${accs}
          ${mods.map((m) => `<span class="chip">${esc(MOD_LABEL[m] || m)}</span>`).join("")}
          <span class="chip paper" title="${esc(it.journal || "")}">${it.year || ""} ${esc(it.journal || "")}</span>
        </div>
        ${quote ? `<div class="evid">\u201C${esc(cleanQuote(quote, 150))}\u201D</div>` : ""}`;
      d.onclick = () => showPaperDetail(it);
      d.onmouseenter = () => showLedgerBand(it);
      wrap.appendChild(d);
    } catch (e) {}
  };
}

function showLedgerBand(it) {
  const pick = pickEvidence(it);
  const more = pick.n > 1 ? `  +${pick.n - 1} more` : "";
  document.getElementById("ledger-quote").textContent =
    pick.quote ? `\u201C${quoteAround(pick.quote, pick.acc, 160)}\u201D` : "\u2026";
  document.getElementById("ledger-src").textContent =
    `${cleanQuote(it.title, 76)} \u00b7 ${pick.acc}${more}`;
}

/* Record / dataset detail modal moved to /record-modal.js (shared with Ask the agent). */

/* Dataset detail modal moved to /record-modal.js (shared with Ask the agent). */

async function exportCsv() {
  const body = {
    q: (document.getElementById("rec-q") || {}).value || "",
    theme: (document.getElementById("rec-theme") || {}).value || "",
    modality: (document.getElementById("rec-modality") || {}).value || "",
    organism: (document.getElementById("rec-organism") || {}).value || "",
    statuses: (document.getElementById("rec-status") || {}).value || "",
    limit: 1, offset: 0,
  };
  try {
    const r = await fetch("/api/datasets/export", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error("export failed (" + r.status + ")");
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "bioagentdb-datasets.csv";
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 400);
  } catch (e) { /* keep silent */ }
}
window.exportCsv = exportCsv;

/* ---------------- chat (modal, opened from the hero) ---------------- */
const chatLog = document.getElementById("chat-log");
let chatSession = localStorage.getItem("chatSession") || null;
function openChat() {
  document.getElementById("chat-modal").classList.remove("hidden");
  const inp = document.getElementById("chat-input");
  if (inp) setTimeout(() => inp.focus(), 30);
}
function closeChat() { document.getElementById("chat-modal").classList.add("hidden"); }
document.getElementById("chat-modal").addEventListener("click", (e) => {
  if (e.target.id === "chat-modal") closeChat();
});
window.openChat = openChat;
window.closeChat = closeChat;
function chatAppend(role, text) {
  const d = document.createElement("div");
  d.className = "msg " + role; d.textContent = text;
  chatLog.appendChild(d); chatLog.scrollTop = chatLog.scrollHeight; return d;
}
function chatBlock(html, cls) {
  const d = document.createElement("div");
  d.className = "msg assistant " + (cls || "");
  d.innerHTML = html;
  chatLog.appendChild(d); chatLog.scrollTop = chatLog.scrollHeight; return d;
}

function chatRecordCard(it) {
  const ds = it.datasets || [];
  const accs = ds.slice(0, 4).map((x) => ((x.links || [])[0] || {}).accession).filter(Boolean);
  const pick = pickEvidence(it);
  const dzs = (it.themes && it.themes.length) ? it.themes : (it.theme ? [it.theme] : []);
  return `<div class="chat-card" data-id="${esc(it.id)}">
    <h4>${esc(cleanQuote(it.title, 96))}</h4>
    <div class="chips">
      <span class="chip ok">${ds.length} dataset${ds.length === 1 ? "" : "s"}</span>
      ${dzs.map((t) => `<span class="chip dz">${esc((LABEL[t] || { n: THEME_NAMES[t] || t }).n)}</span>`).join("")}
      ${accs.map((a) => `<span class="chip">${esc(a)}</span>`).join("")}
      <span class="chip paper">${it.year || ""} ${esc(it.journal || "")}</span>
    </div>
    ${pick.quote ? `<div class="evid">\u201C${esc(quoteAround(pick.quote, pick.acc, 150))}\u201D</div>` : ""}
  </div>`;
}

function chatRecords(records) {
  const b = chatBlock(records.map(chatRecordCard).join(""), "cards-msg");
  b.querySelectorAll(".chat-card").forEach((el) => {
    el.onclick = () => {
      const rec = records.find((r) => r.id === el.dataset.id);
      if (rec) showPaperDetail(rec);
    };
  });
  return b;
}

function chatProposal(p) {
  const b = chatBlock(`
    <div class="proposal">
      <div class="p-line"><b>${esc(p.theme_label)}</b> \u2014 Ming task:
        ${p.papers_per_round || p.budget} papers per round \u00b7 patrol every ${p.cadence_hours || 24}h</div>
      <div class="p-note">Runs in the background; verified records are published to the public catalog.</div>
      ${p.is_new_theme ? '<div class="p-note">New topic \u2014 it will be created first.</div>' : ""}
      <div class="p-actions">
        <button class="primary" id="p-start">Start mining</button>
        <button class="ghost" id="p-later">Not now</button>
      </div>
    </div>`);
  b.querySelector("#p-start").onclick = async () => {
    b.querySelector(".p-actions").innerHTML = '<span class="muted">starting\u2026</span>';
    try {
      const r = await api("/api/chat/confirm", { session_id: chatSession });
      if (r.kind === "order") { chatAppend("assistant", r.text); trackOrder(r.order); }
      else chatAppend("assistant", r.text || "Could not start mining.");
    } catch (e) { chatAppend("assistant", "Failed to start: " + e.message); }
  };
  b.querySelector("#p-later").onclick = () => {
    b.querySelector(".p-actions").innerHTML = '<span class="muted">no mining started</span>';
  };
  return b;
}

function chatRefusal(r) {
  const clickable = !!r.clarify;
  const chips = (r.allowed || []).map((a) => clickable
    ? `<span class="chip pick" data-q="${esc(a.label)}">${esc(a.label)}</span>`
    : `<span class="chip">${esc(a.label)}</span>`).join("");
  const btn = r.nearest ? `<button class="ghost" id="r-nearest">Search ${esc(r.nearest)} instead</button>` : "";
  const b = chatBlock(`<div class="refusal"><div class="chips">${chips}</div>${btn ? `<div class="p-actions">${btn}</div>` : ""}</div>`);
  b.querySelectorAll(".chip.pick").forEach((el) => {
    el.onclick = () => {
      document.getElementById("chat-input").value = `Show me datasets for ${el.dataset.q}`;
      chatSend();
    };
  });
  const nb = b.querySelector("#r-nearest");
  if (nb) nb.onclick = () => { document.getElementById("chat-input").value = r.nearest; chatSend(); };
  return b;
}

async function trackOrder(order) {
  const id = order.order_id || order.task_id;
  chatBlock(`<div class="order-progress"><span class="live-dot"></span>
    <span id="op-text-${id}">task queued\u2026</span>
    <span class="muted">\u00b7 <a href="/mining.html">My mining</a></span></div>`);
  const LABEL = { queued: "queued\u2026", running: "mining\u2026", paused: "paused",
                  stopped: "stopped", idle: "exhausted \u2014 patrolling for new papers",
                  done: "done", error: "failed" };
  const tick = async () => {
    try {
      const st = await api("/api/mining_tasks/" + id, undefined);
      const el = document.getElementById("op-text-" + id);
      const tag = LABEL[st.status] || st.status;
      if (el) el.textContent =
        `${st.theme_label}: ${tag} \u00b7 ${st.papers_seen || 0} paper(s) read \u00b7 ` +
        `${st.records_added || 0} record(s) kept`;
      if (["idle", "stopped", "done", "error", "paused"].includes(st.status)) {
        if (st.status === "idle" && el) {
          el.textContent = `${st.theme_label}: exhausted \u2014 ${st.papers_seen || 0} paper(s) read, ` +
            `${st.records_added || 0} record(s); patrols for new papers` +
            `${st.next_check_at ? " (next check " + st.next_check_at + ")" : ""}. ` +
            "Open My mining for details.";
        }
        return;
      }
    } catch (e) { /* keep polling */ }
    setTimeout(tick, 5000);
  };
  setTimeout(tick, 2000);
}

window.chatSend = async function () {
  const input = document.getElementById("chat-input");
  const text = input.value.trim(); if (!text) return;
  input.value = "";
  chatAppend("user", text);
  const bubble = chatAppend("assistant", "\u2026");
  let reply = "";
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: chatSession }) });
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n"); buf = parts.pop();
      for (const part of parts) {
        if (!part.startsWith("data: ")) continue;
        const ev = JSON.parse(part.slice(6));
        if (ev.type === "token") { reply += ev.text; bubble.textContent = reply; chatLog.scrollTop = chatLog.scrollHeight; }
        else if (ev.type === "records") chatRecords(ev.records || []);
        else if (ev.type === "proposal") chatProposal(ev.proposal || {});
        else if (ev.type === "refusal") chatRefusal(ev.refusal || {});
        else if (ev.type === "order") trackOrder(ev.order || {});
        else if (ev.type === "meta") { chatSession = ev.session_id; localStorage.setItem("chatSession", chatSession); bubble.dataset.kind = ev.kind; }
        else if (ev.type === "done") bubble.textContent = reply || "\u2014";
      }
    }
  } catch (err) { bubble.textContent = "Error: " + err.message; }
};

/* ---------------- ledger: papers / datasets ---------------- */
let ledgerMode = "papers";

function initLedgerMode() {
  const hash = (location.hash || "").replace("#", "");
  const fromHash = hash.startsWith("ledger/") ? hash.split("/")[1] : "";
  const saved = localStorage.getItem("ledgerMode") || "";
  const ok = (m) => m === "papers" || m === "datasets";
  ledgerMode = ok(fromHash) ? fromHash : (ok(saved) ? saved : "papers");
  applyLedgerModeUI();
}

function setLedgerMode(mode) {
  if (mode !== "papers" && mode !== "datasets") return;
  ledgerMode = mode;
  localStorage.setItem("ledgerMode", mode);
  history.replaceState(null, "", "#ledger/" + mode);
  applyLedgerModeUI();
  loadLedger(0);
}
window.setLedgerMode = setLedgerMode;

function applyLedgerModeUI() {
  document.querySelectorAll(".seg-btn").forEach((b) => {
    const on = b.dataset.mode === ledgerMode;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  });
  const led = document.getElementById("ledger");
  if (led) led.classList.toggle("mode-datasets", ledgerMode === "datasets");
}

function ledgerSearch() { loadLedger(0); }
window.ledgerSearch = ledgerSearch;

function loadLedger(p) {
  return ledgerMode === "datasets" ? loadDatasets(p) : loadRecords(p);
}

function loadMore() { loadLedger(offset); }   // L-0232 修复跳页：offset 已是下一段起点，不再 +PAGE
window.loadMore = loadMore;

/* dataset-granularity listing */
async function loadDatasets(p) {
  const start = Math.max(0, p || 0);
  const body = {
    q: document.getElementById("rec-q").value,
    theme: document.getElementById("rec-theme").value,
    modality: document.getElementById("rec-modality").value,
    organism: document.getElementById("rec-organism").value,
    source: "", status: "",
    statuses: document.getElementById("rec-status").value,
    limit: PAGE, offset: start, sort: "confidence",
  };
  let data;
  try { data = await api("/api/datasets", body); }
  catch (e) {
    document.getElementById("rec-list").innerHTML = `<div class="record">Search failed: ${esc(e.message)}</div>`;
    return;
  }
  total = data.total;
  document.getElementById("rec-count").textContent = `${total} dataset record(s)`;
  const wrap = document.getElementById("rec-list");
  if (start === 0) wrap.innerHTML = "";                       // 首页清空，其后追加
  if (!data.items.length && start === 0) {
    wrap.innerHTML = '<div class="record">No dataset records match these filters.<br>' +
      '<span class="muted">Switch to Papers, or widen the status filter.</span></div>';
  }
  data.items.forEach(renderDatasetCard(wrap));
  offset = start + data.items.length;                          // 下一次追加的起点
  document.getElementById("load-more").style.display = offset < total ? "inline-block" : "none";
}

function renderDatasetCard(wrap) {
  return (it) => {
    try {
      const d = document.createElement("div");
      d.className = "record ds-card";
      const nd = it.normalized || {};
      const links = it.links || [];
      const primary = links.find((l) => l.is_primary) || links[0] || {};
      const acc = primary.accession || "";
      const paper = (it.papers || [])[0] || {};
      const ex = (it.evidence || []).find((e) => e.dataset_id === it.id) || (it.evidence || [])[0] || {};
      const st = it.status;
      const stClass = st === "verified" ? "ok" : st === "paper_verified" ? "warn" : "bad";
      const mods = Array.isArray(it.modalities) ? it.modalities.slice(0, 3) : [];
      // 标题若已含登记号，去掉尾缀；若标题本身就是登记号，回退到来源论文标题
      let title = it.title || "";
      if (acc) {
        const re = new RegExp("[\\s\\u2013\\u2014\\-()\\[\\]]*" + acc.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "[\\s)\\]]*", "gi");
        title = title.replace(re, " ").trim();
        if (title.length < 4 || title.toUpperCase().startsWith("DBGAP STUDY")) {
          title = paper.title ? cleanQuote(paper.title, 90) : `dataset record ${acc}`;
        }
      }
      d.innerHTML = `
        <div class="ds-head">
          <span class="ds-acc">${esc(acc || "n/a")}</span>
          <span class="chip ${stClass}">${esc(st)}</span>
          ${primary.repository ? `<span class="chip paper">${esc(primary.repository)}</span>` : ""}
        </div>
        <h4>${esc(title)}</h4>
        <div class="chips">
          ${(() => {
            const ndz = (nd.disease || []).map((x) => x.canonical);
            const raw = it.disease || "";
            const show = ndz.length ? ndz.join(" · ") : raw;
            if (!show) return "";
            const q = ((nd.disease || [])[0] || {}).quote || "";
            return `<span class="chip dz" title="${esc(raw + (q ? " | " + q : ""))}">${esc(show)}</span>`;
          })()}
          ${mods.map((m) => `<span class="chip">${esc(MOD_LABEL[m] || m)}</span>`).join("")}
          ${(() => {
            const no = (nd.organism || []).map((x) => x.canonical);
            const raw = it.organism || "";
            const show = no.length ? no.join(" · ") : raw;
            if (!show) return "";
            const q = ((nd.organism || [])[0] || {}).quote || "";
            return `<span class="chip" title="${esc(raw + (q ? " | " + q : ""))}">${esc(show)}</span>`;
          })()}
          ${it.tissue ? `<span class="chip">${esc(it.tissue)}</span>` : ""}
          ${it.sample_count ? `<span class="chip">${it.sample_count} samples</span>` : ""}
          <span class="chip">conf ${(it.confidence ?? 0).toFixed(2)}</span>
        </div>
        ${ex.quote ? `<div class="evid">\u201C${esc(quoteAround(ex.quote, acc, 150))}\u201D</div>` : ""}
        ${paper.title ? `<div class="ds-src">source paper: ${esc(cleanQuote(paper.title, 68))}` +
          `${paper.year ? " \u00b7 " + paper.year : ""}${paper.pmid ? " \u00b7 PMID " + esc(paper.pmid) : ""}</div>` : ""}`;
      d.onclick = () => showDetail(it.id);
      d.onmouseenter = () => {
        document.getElementById("ledger-quote").textContent =
          ex.quote ? `\u201C${quoteAround(ex.quote, acc, 160)}\u201D` : "\u2026";
        document.getElementById("ledger-src").textContent = `${cleanQuote(it.title, 76)} \u00b7 ${acc}`;
      };
      wrap.appendChild(d);
    } catch (e) { /* skip */ }
  };
}

/* ---------------- util ---------------- */
function cleanQuote(text, max) {
  let s = String(text || "").replace(/[\s]+$/, "");
  s = s.replace(/[.\u2026\s]+$/, "");
  s = s.replace(/\s+([,.;:!?])/g, "$1");   // 去掉标点前的多余空格
  if (s.length > max) {
    s = s.slice(0, max).replace(/\s+\S*$/, "");
    s = s.replace(/\s+(and|or|of|in|the|on|for|with|a|an|to|by|at|from|its|their)$/i, "");
  }
  return s;
}

/* 截取引文时以登记号为中心，确保证据里看得到那个号 */
function quoteAround(text, acc, max) {
  let s = String(text || "").replace(/\s+/g, " ").trim();
  s = s.replace(/\s+([,.;:!?])/g, "$1");
  const i = acc ? s.toUpperCase().indexOf(String(acc).toUpperCase()) : -1;
  if (i >= 0) {
    const lead = Math.floor(max * 0.55);
    let start = Math.max(0, i - lead);
    if (start > 0) {                       // 起点对齐到词边界，避免切在词中间
      const sp = s.indexOf(" ", start);
      if (sp > 0 && sp < i) start = sp + 1;
    }
    let end = Math.min(s.length, i + acc.length + Math.floor(max * 0.30));
    if (end < s.length) {
      const sp = s.lastIndexOf(" ", end);
      if (sp > start) end = sp;
    }
    let seg = s.slice(start, end);
    if (start > 0) seg = "\u2026" + seg;
    if (end < s.length) seg = seg + "\u2026";
    return seg;
  }
  return cleanQuote(s, max);
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* live counters inside the illustration */
const liveState = { read: 0, mined: 0 };
function animateCount(el, to, from) {
  if (!el) return;
  const start = performance.now(), dur = 900;
  function step(t) {
    const p = Math.min(1, (t - start) / dur);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(from + (to - from) * eased).toLocaleString();
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
async function updateLiveCounters(initial) {
  try {
    const s = await api("/api/stats", undefined);
    const read = s.papers_mined ?? 0, mined = s.papers_with_data ?? 0;
    animateCount(document.getElementById("live-read"), read, initial ? 0 : liveState.read);
    animateCount(document.getElementById("live-mined"), mined, initial ? 0 : liveState.mined);
    liveState.read = read;
    liveState.mined = mined;
  } catch (e) { /* decorative */ }
  try {
    const t = await api("/api/throughput", undefined);
    const el = document.getElementById("live-speed");
    if (el) el.textContent = `${t.per_hour} papers/hr · ${t.yield_pct}% hit rate · ${t.avg_seconds}s/paper`;
  } catch (e) { /* decorative */ }
}

/* ---------------- boot ---------------- */
let _silent = false;
async function silentRefresh() {
  if (_silent || document.hidden) return;
  _silent = true;
  try {
    await refreshStats();
    await loadFacets();
    const q = document.getElementById("rec-q");
    if (offset === 0 && document.activeElement !== q) await loadLedger(0);
  } catch (e) { /* keep the page as-is on error */ }
  _silent = false;
}

(() => {
  initLedgerMode();
  refreshStats();
  renderAgentStatus();
  updateLiveCounters(true);
  loadHeroSample();
  loadFacets().then(() => loadLedger(0)).catch(() => loadLedger(0));
  setInterval(() => { updateLiveCounters(false); renderAgentStatus(); }, 20000);
  setInterval(silentRefresh, 15000);
})();