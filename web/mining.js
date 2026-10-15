/* My mining — 用户矿机任务页（L-0209）。
   只读 + 操作（pause/resume/stop）与导出；任务来自 /api/mining_tasks。 */
"use strict";

const $ = (s) => document.querySelector(s);
let tasks = [];
let current = null;
let pollTimer = null;
let currentRecs = [];
let recOffset = 0;
let recTotal = 0;
let lastRecId = "";
const REC_PAGE = 50;

function esc(s) {
  return (s ?? "").toString().replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  return r.json();
}

function mySession() { return localStorage.getItem("chatSession") || ""; }

async function loadList() {
  try {
    const data = await api("/api/mining_tasks?limit=50");
    tasks = data.items || [];
    renderList();
  } catch (e) {
    $("#mn-list").innerHTML = `<div class="mn-empty">Could not load tasks: ${esc(e.message)}</div>`;
  }
}

function renderList() {
  const el = $("#mn-list");
  if (!tasks.length) {
    el.innerHTML = '<div class="mn-empty">No tasks yet. Ask the agent on the home page and confirm a mining task.</div>';
    return;
  }
  const mine = mySession();
  el.innerHTML = tasks.map((t) => `
    <div class="mn-card ${current && current.id === t.id ? "active" : ""}" data-id="${t.id}">
      <div class="t">${esc(t.title || t.theme_label || t.theme)}</div>
      <div class="s"><span class="pill ${esc(t.status)}">${esc(t.status)}</span>
        ${t.records_added || 0} record(s) · ${t.papers_seen || 0} paper(s) read${t.cost_estimate_usd ? ` · ≈$${Number(t.cost_estimate_usd).toFixed(2)}` : ""}</div>
      <div class="s">${esc(t.status_text || "")}</div>
      ${mine && t.session_id === mine ? '<div class="s"><b>yours</b></div>' : ""}
    </div>`).join("");
  el.querySelectorAll(".mn-card").forEach((c) => {
    c.onclick = () => select(c.dataset.id);
  });
}

function statusActions(t) {
  const b = [];
  if (["queued", "running", "idle"].includes(t.status)) {
    b.push(`<button class="mn-btn" data-act="pause">Pause</button>`);
  }
  if (t.status === "paused") {
    b.push(`<button class="mn-btn primary" data-act="resume">Resume</button>`);
  }
  if (["queued", "running", "idle", "paused"].includes(t.status)) {
    b.push(`<button class="mn-btn" data-act="stop">Stop</button>`);
  }
  if (["stopped", "error"].includes(t.status)) {
    b.push(`<button class="mn-btn primary" data-act="restart"
      title="Re-queue this task (it resumes when the scheduler is running)">Restart</button>`);
  }
  return b.join("");
}

function renderPanel(recs) {
  const t = current;
  if (!t) return;
  const p = $("#mn-panel");
  const next = t.next_check_at ? ` · next check ${esc(t.next_check_at)}` : "";
  const rows = recs.map((r) => `
    <div class="mn-rec">
      <div><b>${esc(r.repository)} ${esc(r.accession)}</b>
        <span class="badge ${esc(r.status)}">${esc(r.status)}</span>
        ${r.confidence ? `<span class="muted"> conf ${Number(r.confidence).toFixed(2)}</span>` : ""}</div>
      <div>${esc(r.title || "")}</div>
      ${r.evidence_quote ? `<div class="q">“${esc(String(r.evidence_quote).slice(0, 420))}”</div>` : ""}
      <div class="s">${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.url)}</a>` : ""}</div>
    </div>`).join("");
  p.innerHTML = `
    <h2 style="margin:2px 0 6px;font-size:18px;color:#111827">${esc(t.title || t.theme_label || t.theme)}</h2>
    <div class="s"><span class="pill ${esc(t.status)}">${esc(t.status)}</span>
      ${esc(t.status_text || "")}${next}</div>
    ${t.note ? `<div class="s"><b>Intent:</b> ${esc(t.note)}</div>` : ""}
    <div class="mn-stats">
      <span>Theme <b>${esc(t.theme_label || t.theme)}</b></span>
      <span>Phase <b>${esc(t.phase || "exhaust")}</b></span>
      <span>Per round <b>${t.papers_per_round || t.budget}</b> papers</span>
      <span>Cadence <b>${t.cadence_hours}h</b></span>
      <span>Rounds <b>${t.rounds_done || 0}</b></span>
      <span>Read <b>${t.papers_seen || 0}</b></span>
      <span>Records kept <b>${t.records_added || 0}</b></span>
      ${t.next_check_at ? `<span>Next check <b>${esc(t.next_check_at)}</b></span>` : ""}
      ${t.cost_estimate_usd ? `<span>Est. cost <b>≈$${Number(t.cost_estimate_usd).toFixed(2)}</b></span>` : ""}
    </div>
    <div class="mn-actions">
      ${statusActions(t)}
      <a class="mn-btn" href="/api/mining_tasks/${t.id}/export?fmt=jsonl">Export JSONL</a>
      <a class="mn-btn" href="/api/mining_tasks/${t.id}/export?fmt=csv">Export CSV</a>
    </div>
    <div style="margin-top:10px">${rows || '<div class="mn-empty">No records yet.</div>'}</div>
    ${recOffset < recTotal ? '<div class="mn-actions"><button class="mn-btn" id="mn-more">Load more records</button></div>' : ""}`;
  p.querySelectorAll("[data-act]").forEach((btn) => {
    btn.onclick = () => act(btn.dataset.act);
  });
  const more = p.querySelector("#mn-more");
  if (more) more.onclick = loadMore;
}

async function loadMore() {
  if (!current) return;
  try {
    const r = await api(`/api/mining_tasks/${current.id}/records?limit=${REC_PAGE}&offset=${recOffset}`);
    const items = r.items || [];
    if (r.total !== undefined) recTotal = Number(r.total);
    recOffset += items.length;
    if (!items.length) recTotal = recOffset;   // 防呆：空页即到头，按钮消失
    currentRecs = currentRecs.concat(items);
    renderPanel(currentRecs);
  } catch (e) { /* keep current view */ }
}

async function select(id) {
  try {
    if (id !== lastRecId) {                    // 切任务才重置分页（轮询保留已加载页，L-0215 修复 C-5）
      lastRecId = id; currentRecs = []; recOffset = 0; recTotal = 0;
    }
    current = await api("/api/mining_tasks/" + id);
    const want = Math.max(REC_PAGE, recOffset);
    const recs = await api(`/api/mining_tasks/${id}/records?limit=${want}&offset=0`);
    currentRecs = recs.items || [];
    recOffset = currentRecs.length;
    recTotal = Number(recs.total !== undefined ? recs.total : currentRecs.length);
    renderPanel(currentRecs);
    renderList();
    schedule();
  } catch (e) {
    $("#mn-panel").innerHTML = `<div class="mn-empty">Could not load task: ${esc(e.message)}</div>`;
  }
}

function schedule() {
  clearTimeout(pollTimer);
  if (current && ["queued", "running"].includes(current.status)) {
    pollTimer = setTimeout(() => select(current.id), 5000);
  }
}

async function act(action) {
  if (!current) return;
  try {
    await api(`/api/mining_tasks/${current.id}/${action}`, { method: "POST" });
  } catch (e) {
    alert("Action failed: " + e.message);
  }
  await select(current.id);
  await loadList();
}

/* ---- Topics management (L-0250) ---- */
async function loadTopics() {
  const el = $("#mn-topics");
  if (!el) return;
  try {
    const r = await api("/api/themes?include_hidden=1");
    renderTopics(r.items || []);
  } catch (e) { /* keep silent */ }
}

function renderTopics(items) {
  const el = $("#mn-topics");
  const rows = items.map((t) => `
    <div class="mn-topic" data-slug="${esc(t.slug)}">
      <span class="n">${esc(t.name)}</span>
      <span class="k">${t.kind === "topic" ? "topic" : "disease"} · ${t.records_count || 0} records${t.hidden ? ' · <span class="badge hidden">hidden</span>' : ""}</span>
      <span class="b">
        <button class="mn-btn" data-x="rename">Rename</button>
        ${t.hidden
          ? `<button class="mn-btn" data-x="unhide">Unhide</button>`
          : `<button class="mn-btn" data-x="hide" title="Reversible — unhide anytime">Hide</button>`}
      </span>
    </div>`).join("");
  el.innerHTML = `
    <h2>Topics</h2>
    <p class="mn-sub" style="margin:0 0 6px">Rename a topic, or hide it from the catalog lists
      (hiding is reversible; a hidden topic comes back automatically when you mine it again).</p>
    ${rows || '<div class="mn-empty">No topics yet.</div>'}`;
  el.querySelectorAll(".mn-topic").forEach((row) => {
    const slug = row.dataset.slug;
    const t = items.find((x) => x.slug === slug) || { slug };
    row.querySelector('[data-x="rename"]').onclick = () => startRename(row, t);
    const hide = row.querySelector('[data-x="hide"]');
    const show = row.querySelector('[data-x="unhide"]');
    if (hide) hide.onclick = async () => {
      try { await api(`/api/themes/${slug}/hide`, { method: "POST" }); }
      catch (e) { alert("Failed: " + e.message); }
      await loadTopics();
    };
    if (show) show.onclick = async () => {
      try { await api(`/api/themes/${slug}/unhide`, { method: "POST" }); }
      catch (e) { alert("Failed: " + e.message); }
      await loadTopics();
    };
  });
}

function startRename(row, t) {
  row.innerHTML = `
    <input class="mn-rename" value="${esc(t.name || "")}"/>
    <span class="b">
      <button class="mn-btn primary" data-x="save">Save</button>
      <button class="mn-btn" data-x="cancel">Cancel</button>
    </span>`;
  const inp = row.querySelector("input");
  inp.focus();
  inp.select();
  row.querySelector('[data-x="save"]').onclick = async () => {
    const name = inp.value.trim();
    if (!name) return;
    try {
      await api(`/api/themes/${t.slug}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
    } catch (e) { alert("Rename failed: " + e.message); }
    await loadTopics();
    await loadList();
  };
  row.querySelector('[data-x="cancel"]').onclick = () => loadTopics();
}

/* ---- pane switch (L-0253): Tasks | Topics — same look as Research|Ming ---- */
const mnTasksPane = $("#mn-tasks-pane"), mnTopicsPane = $("#mn-topics-pane");
const mnModeBtns = [$("#mn-mode-tasks"), $("#mn-mode-topics")];
function mnSetMode(m) {
  localStorage.setItem("mnMode", m);
  mnTasksPane.classList.toggle("wb-hidden", m !== "tasks");
  mnTopicsPane.classList.toggle("wb-hidden", m !== "topics");
  mnModeBtns.forEach((b) => b.classList.toggle("active", b.dataset.mode === m));
}
mnModeBtns.forEach((b) => { b.onclick = () => mnSetMode(b.dataset.mode); });
mnSetMode(localStorage.getItem("mnMode") === "topics" ? "topics" : "tasks");

window.addEventListener("DOMContentLoaded", async () => {
  $("#mn-hint").textContent = mySession()
    ? ""
    : "Tip: tasks are tied to your browser session — create one from the agent chat on the home page.";
  await loadTopics();
  await loadList();
  setInterval(loadList, 15000);
});
