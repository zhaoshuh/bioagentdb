/* Chat workbench (v1.5) — sessions, SSE streaming, three result classes, exports. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const log = $("wb-log"), input = $("wb-input"), sendBtn = $("wb-send"),
        stopBtn = $("wb-stop"), listEl = $("wb-session-list"),
        resultsEl = $("wb-results"), expJson = $("wb-export-json"),
        expCsv = $("wb-export-csv"),
        mnlLog = $("mnl-log"), mnlInput = $("mnl-input"),
        mnlSend = $("mnl-send"), mnlStop = $("mnl-stop");

  let sid = null, busy = false, abortCtrl = null, resultSets = [];
  let mnlSid = null, mnlBusy = false, mnlAbort = null, mnlGoal = "";

  const STEP_LABEL = {
    clarify: "Clarifying the request", catalog: "Searching the catalog",
    research: "Researching the topic online",
    sources: "Searching live sources", dedup: "Deduplicating",
    papers: "Reading papers for announced datasets", verify: "Verifying links",
    report: "Building the report",
  };
  const SOURCE_LABEL = { europepmc: "Europe PMC", openalex: "OpenAlex", core: "CORE",
    pubmed: "PubMed", preprints: "bioRxiv/medRxiv" };
  const FT_LABEL = { europepmc_fulltext: "EPMC full text", pmc: "PMC full text",
    ncbi_efetch: "PMC (NCBI)", pmc_oa: "PMC OA", preprint: "preprint XML",
    core: "CORE full text", core_pdf: "CORE PDF", openalex_pdf: "OpenAlex PDF",
    none: "abstract only" };
  const EXAMPLES = [
    "spatial transcriptomics of mouse brain development",
    "single-nucleus RNA-seq of human brain in Alzheimer's disease, at least 20 samples",
    "proteomics of Parkinson's disease CSF",
  ];

  /* ---------------- helpers ---------------- */
  function el(tag, cls, text) {
    const d = document.createElement(tag);
    if (cls) d.className = cls;
    if (text !== undefined) d.textContent = text;
    return d;
  }
  const scrollLog = () => { log.scrollTop = log.scrollHeight; };

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return r.json();
  }

  function stamp(node, ts) {
    node.appendChild(el("div", "wb-ts", fmtTs(ts || new Date().toISOString())));
  }
  function bubble(role, text, ts) {
    const d = el("div", `wb-msg ${role}`);
    d.appendChild(el("span", "wb-body", text));
    stamp(d, ts);
    log.appendChild(d); scrollLog(); return d;
  }

  /* ---------------- safe markdown subset (final answers) ---------------- */
  function escHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function inlineMd(s) {
    let t = escHtml(s);
    t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
    t = t.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    return t;
  }
  function renderMarkdown(src) {
    const lines = String(src || "").split("\n");
    const out = [];
    let inUl = false;
    const closeUl = () => { if (inUl) { out.push("</ul>"); inUl = false; } };
    for (const line of lines) {
      if (!line.trim()) { closeUl(); continue; }
      const h = line.match(/^###\s+(.*)$/);
      if (h) { closeUl(); out.push(`<h4>${inlineMd(h[1])}</h4>`); continue; }
      if (/^---+\s*$/.test(line)) { closeUl(); out.push("<hr/>"); continue; }
      const li = line.match(/^-\s+(.*)$/);
      if (li) {
        if (!inUl) { out.push("<ul>"); inUl = true; }
        out.push(`<li>${inlineMd(li[1])}</li>`);
        continue;
      }
      closeUl();
      out.push(`<p>${inlineMd(line)}</p>`);
    }
    closeUl();
    return out.join("");
  }
  function collapseLists(root) {
    root.querySelectorAll("ul").forEach((ul) => {
      const lis = [...ul.children];
      if (lis.length <= 3) return;
      lis.forEach((li, i) => li.classList.toggle("md-hidden", i >= 3));
      let btn = ul.nextElementSibling;
      if (!btn || !btn.classList.contains("md-toggle")) {
        btn = el("button", "md-toggle");
        ul.after(btn);
      }
      btn.textContent = `Show all (${lis.length})`;
      btn.onclick = () => {
        const hidden = [...ul.children].some(
          (li, i) => i >= 3 && li.classList.contains("md-hidden"));
        [...ul.children].forEach((li, i) => {
          if (i >= 3) li.classList.toggle("md-hidden", !hidden);
        });
        btn.textContent = hidden ? "Show less" : `Show all (${lis.length})`;
      };
    });
  }
  function mdBubble(text, ts) {
    const d = el("div", "wb-msg assistant md");
    d.innerHTML = renderMarkdown(text);
    collapseLists(d);
    stamp(d, ts);
    log.appendChild(d); scrollLog(); return d;
  }
  function tokenBubble(turn, text) {
    let b = turn.querySelector(".wb-msg.assistant.md");
    if (!b) { b = el("div", "wb-msg assistant md"); b._raw = ""; turn.appendChild(b); }
    b._raw += text;
    b.innerHTML = renderMarkdown(b._raw);
    collapseLists(b);
    scrollLog();
  }
  function chips(options, onPick) {
    if (!options || !options.length) return;
    const wrap = el("div", "wb-profile");
    options.forEach((o) => {
      const c = el("span", "wb-chip", o);
      c.onclick = () => onPick(o);
      wrap.appendChild(c);
    });
    log.appendChild(wrap); scrollLog();
  }

  /* ---------------- web research card (v1.6) ---------------- */
  function renderResearch(r, parent) {
    if (!r) return null;
    const host = parent || log;
    const card = el("div", "wb-research");
    const head = el("div", "wb-research-head");
    head.appendChild(el("span", "wb-research-title", "Web research"));
    if (r.cached) head.appendChild(el("span", "wb-badge", "cached"));
    const ts = r.checked_at || r.fetched_at;
    if (ts) head.appendChild(el("span", "wb-research-ts", fmtTs(ts)));
    card.appendChild(head);
    if (r.summary) card.appendChild(el("div", "wb-research-sum", r.summary));
    const terms = r.search_terms || [];
    if (terms.length) {
      const row = el("div", "wb-research-row");
      row.appendChild(el("span", "wb-research-k", "Search terms added:"));
      terms.forEach((t) => row.appendChild(el("span", "wb-chip static", t)));
      card.appendChild(row);
    }
    const excl = r.exclusions || [];
    if (excl.length) {
      const row = el("div", "wb-research-row");
      row.appendChild(el("span", "wb-research-k", "De-emphasized:"));
      excl.forEach((t) => row.appendChild(el("span", "wb-chip static warm", t)));
      card.appendChild(row);
    }
    const srcs = r.sources || [];
    if (srcs.length) {
      const list = el("div", "wb-research-src");
      list.appendChild(el("div", "wb-research-k", "Sources:"));
      srcs.forEach((s) => {
        const u = String((s && s.url) || "");
        if (!/^https?:\/\//i.test(u)) return;  // 纵深防御：仅渲染 http(s)
        const a = el("a", "", `${s.title || u} — ${u.length > 64 ? u.slice(0, 64) + "…" : u}`);
        a.href = u; a.target = "_blank"; a.rel = "noopener";
        list.appendChild(a);
      });
      card.appendChild(list);
    }
    host.appendChild(card); scrollLog();
    return card;
  }
  function stepLine(turn, name, status, detail, counts, ms) {
    let line = turn.querySelector(`[data-step="${name}"]`);
    if (!line) {
      line = el("div", "wb-step");
      line.dataset.step = name;
      line.appendChild(el("span", "dot"));
      line.appendChild(el("span", "label", STEP_LABEL[name] || name));
      line.appendChild(el("span", "detail"));
      turn.appendChild(line);
    }
    line.classList.toggle("running", status === "start");
    line.classList.toggle("done", status === "done");
    if (status === "done") turn._doneCount = (turn._doneCount || 0) + 1;
    const det = line.querySelector(".detail");
    if (counts && typeof counts === "object" && !Array.isArray(counts)) {
      det.textContent = Object.keys(counts).map((k) =>
        `${SOURCE_LABEL[k] || k}: ${counts[k] === -1 ? "skipped" : counts[k]}`).join(" · ");
    } else if (Array.isArray(detail)) {
      det.textContent = detail.map((s) => {
        if (s && typeof s === "object" && "hits" in s) {
          return `${SOURCE_LABEL[s.source] || s.source}: ${s.hits} (${s.ms}ms)`;
        }
        return `${SOURCE_LABEL[s.source] || s.source}: ${s.n}`;
      }).join(" · ");
    } else if (detail && typeof detail === "object") {
      det.textContent = Object.keys(detail).map((k) => {
        const v = detail[k];
        return `${SOURCE_LABEL[k] || k}: ${v.hits}${v.ok ? "" : " ✕"}`;
      }).join(" · ");
    } else if (detail !== undefined && detail !== null) {
      det.textContent = String(detail);
    }
    if (status === "done" && ms) det.textContent += ` · ${(ms / 1000).toFixed(1)}s`;
    scrollLog();
  }

  function progressLine(turn, ev) {
    let hist = turn.querySelector(".wb-plines");
    if (!hist) { hist = el("div", "wb-plines"); turn.appendChild(hist); }
    const sec = ((Date.now() - (turn._t0 || Date.now())) / 1000).toFixed(1);
    if (ev.heartbeat) {
      let hb = turn.querySelector(".wb-pline.hb");
      if (!hb) {
        hb = el("div", "wb-pline hb");
        hb.appendChild(el("span", "t"));
        hb.appendChild(el("span", "x"));
        hist.appendChild(hb);
      }
      hb.querySelector(".t").textContent = "+" + sec + "s";
      hb.querySelector(".x").textContent = ev.text;
    } else {
      const hb = turn.querySelector(".wb-pline.hb");
      if (hb) hb.remove();
      const line = el("div", "wb-pline");
      line.appendChild(el("span", "t", "+" + sec + "s"));
      line.appendChild(el("span", "x", ev.text));
      hist.appendChild(line);
    }
    hist.querySelectorAll(".wb-pline").forEach((n) => n.classList.remove("cur"));
    const lines = hist.querySelectorAll(".wb-pline");
    if (lines.length) lines[lines.length - 1].classList.add("cur");
    scrollLog();
  }
  function renderProfile(profile, ready, missing, defaults) {
    const wrap = el("div", "wb-profile");
    const add = (t, cls) => wrap.appendChild(el("span", `wb-chip static ${cls || ""}`, t));
    const defs = new Set(defaults || profile.defaults || []);
    const topic = profile.topic || profile.disease;
    if (topic) add(topic, "strong");
    if (profile.disease && profile.disease !== topic) add(profile.disease);
    if (profile.organism) add(profile.organism + (defs.has("organism") ? " (default)" : ""));
    if (profile.tissue) add(profile.tissue);
    (profile.modalities || []).forEach((m) => add(String(m).replace(/_/g, " ")));
    if (profile.sample_size_min) add(`≥ ${profile.sample_size_min} samples`);
    (missing || []).forEach((m) => add(`missing: ${m}`, "warn"));
    if (ready) add("ready", "strong");
    log.appendChild(wrap); scrollLog();
  }

  /* ---------------- sessions (kind-aware: research | ming, L-0234) ---------------- */
  function currentKind() {
    return document.getElementById("wb-ming-pane").classList.contains("wb-hidden")
      ? "research" : "ming";
  }
  async function loadSessions() {
    const kind = currentKind();
    try {
      const data = await api(`/api/chat/sessions?kind=${kind}`);
      listEl.innerHTML = "";
      (data.sessions || []).forEach((s) => {
        const active = kind === "ming" ? s.id === mnlSid : s.id === sid;
        const d = el("div", "wb-session" + (active ? " active" : ""));
        d.appendChild(el("div", "t", s.title || "(untitled)"));
        d.appendChild(el("div", "d", (s.updated_at || "").replace("T", " ").slice(0, 16)));
        d.onclick = () => (kind === "ming" ? openMingSession(s.id) : openSession(s.id));
        listEl.appendChild(d);
      });
      if (!(data.sessions || []).length) {
        listEl.appendChild(el("p", "wb-empty", kind === "ming"
          ? "No Ming sessions yet — describe what to mine to start one."
          : "No sessions yet — ask a question to start one."));
      }
    } catch (e) { /* keep silent */ }
  }

  function newSession() {
    if (currentKind() === "ming") {
      mnlSid = null; mnlGoal = "";
      mnlLog.innerHTML = ""; renderMingIntro();
      loadSessions(); mnlInput.focus();
      return;
    }
    sid = null; resultSets = [];
    log.innerHTML = ""; renderIntro();
    renderResultSets();
    loadSessions();
  }

  function renderHistorySteps(steps) {
    const done = steps.filter((s) => s.event === "step" && s.status === "done");
    const ms = done.reduce((a, s) => a + (s.ms || 0), 0);
    const d = el("details", "wb-hist");
    d.appendChild(el("summary", "", `Agent steps · ${done.length} steps`
      + (ms ? ` · ${(ms / 1000).toFixed(1)}s` : "")));
    const body = el("div", "wb-hist-body");
    steps.forEach((ev) => {
      if (ev.event === "step" && ev.status === "done") {
        const line = el("div", "wb-step done");
        line.appendChild(el("span", "dot"));
        line.appendChild(el("span", "label", STEP_LABEL[ev.name] || ev.name));
        const det = el("span", "detail");
        const c = ev.counts, dl = ev.detail;
        if (c && typeof c === "object" && !Array.isArray(c)) {
          det.textContent = Object.keys(c).map((k) =>
            `${SOURCE_LABEL[k] || k}: ${c[k] === -1 ? "skipped" : c[k]}`).join(" · ");
        } else if (Array.isArray(dl)) {
          det.textContent = dl.map((s) => (s && typeof s === "object" && "hits" in s)
            ? `${SOURCE_LABEL[s.source] || s.source}: ${s.hits} (${s.ms}ms)`
            : `${SOURCE_LABEL[s.source] || s.source}: ${s.n}`).join(" · ");
        } else if (dl && typeof dl === "object") {
          det.textContent = Object.keys(dl).map((k) =>
            `${SOURCE_LABEL[k] || k}: ${dl[k].hits}${dl[k].ok ? "" : " ✕"}`).join(" · ");
        } else if (dl !== undefined && dl !== null) {
          det.textContent = String(dl);
        }
        if (ev.ms) det.textContent += ` · ${(ev.ms / 1000).toFixed(1)}s`;
        line.appendChild(det);
        body.appendChild(line);
      } else if (ev.event === "progress" && ev.text) {
        body.appendChild(el("div", "wb-pline", ev.text));
      }
    });
    d.appendChild(body);
    log.appendChild(d); scrollLog();
  }

  async function openSession(id) {
    try {
      const data = await api(`/api/chat/sessions/${id}`);
      sid = id; resultSets = [];
      log.innerHTML = "";
      (data.messages || []).forEach((m) => {
        if (m.role === "user") { bubble("user", m.content, m.created_at); return; }
        if (m.role !== "assistant") return;
        const meta = m.meta || {};
        if (meta.kind === "clarify") {
          bubble("assistant", m.content, m.created_at);
          chips(meta.options || [], (o) => send(o));
        } else if (meta.kind === "report" && meta.report) {
          if (meta.steps && meta.steps.length) renderHistorySteps(meta.steps);
          if (meta.report.research) renderResearch(meta.report.research);
          mdBubble(m.content, meta.report.generated_at || m.created_at);
          const cat = (meta.report.items || []).filter((i) => i.type === "catalog");
          const cand = (meta.report.items || []).filter((i) => i.type === "candidate");
          resultSets.push({ ts: meta.report.generated_at || m.created_at,
                            report: meta.report, catalog: cat, candidates: cand });
        } else {
          bubble("assistant", m.content, m.created_at);
        }
      });
      resultSets.reverse();
      renderResultSets();
      loadSessions();
    } catch (e) { bubble("error", "Could not load session: " + e.message); }
  }

  function renderIntro() {
    const d = el("div", "wb-intro");
    d.appendChild(el("h2", "", "Ask about biomedical data in papers"));
    d.appendChild(el("p", "", "Describe what you need — any research topic: a disease, a "
      + "biological process or a technique, with organ/organism details if any. The agent "
      + "clarifies your intent, searches the "
      + "BioAgentDB catalog and five live sources (Europe PMC, OpenAlex, CORE, PubMed, "
      + "preprints), reads papers for announced dataset accessions, and returns results "
      + "with evidence. The catalog spans any topic (diseases are one kind); external "
      + "findings are unverified."));
    const wrap = el("div", "wb-profile");
    EXAMPLES.forEach((ex) => {
      const c = el("span", "wb-chip", ex);
      c.onclick = () => send(ex);
      wrap.appendChild(c);
    });
    d.appendChild(wrap);
    log.appendChild(d);
  }

  /* ---------------- results / report ---------------- */
  function fmtTs(iso) {
    if (!iso) return "—";
    return String(iso).replace("T", " ").slice(0, 16);
  }

  function reportSummaryBox(report) {
    const box = el("div", "wb-rep");
    const row = (k, v) => { const r = el("div", "row"); r.textContent = `${k}: ${v}`; return r; };
    const c = report.counts || {};
    box.appendChild(row("Catalog records", c.catalog ?? 0));
    box.appendChild(row("External candidates", c.candidates ?? 0));
    box.appendChild(row("Paper-announced datasets", c.datasets ?? 0));
    if (report.research) {
      box.appendChild(row("Web research",
        (report.research.search_terms || []).length + " term(s) · "
        + (report.research.sources || []).length + " source(s)"
        + (report.research.cached ? " · cached" : "")));
    }
    const per = report.per_source || {};
    box.appendChild(row("Per source", Object.keys(per).map((k) => {
      const v = per[k] || {};
      const hits = v.hits === -1 ? "skipped" : v.hits;
      return `${SOURCE_LABEL[k] || k} ${hits}${v.ok === false ? " ✕" : ""}`;
    }).join(" · ")));
    const ft = (report.fulltext_stats || {}).kinds || {};
    const ftKeys = Object.keys(ft);
    if (ftKeys.length) {
      box.appendChild(row("Full text", ftKeys.map((k) =>
        `${FT_LABEL[k] || k} ${ft[k]}`).join(" · ")));
    }
    box.appendChild(row("Quality gate", report.rubric_ok
      ? "passed" : `issues: ${(report.rubric_issues || []).join("; ")}`));
    box.appendChild(row("Generated", fmtTs(report.generated_at)));
    return box;
  }

  function datasetBlock(datasets) {
    const wrap = el("div", "wb-datasets");
    wrap.appendChild(el("div", "wb-dshead", "Datasets announced in this paper (unverified)"));
    (datasets || []).forEach((d) => {
      const row = el("div", "wb-dsrow");
      row.appendChild(el("span", "wb-badge mono", d.accession || "?"));
      if (d.repository) row.appendChild(el("span", "wb-badge", d.repository));
      if (d.da_evidence) row.appendChild(el("span", "wb-badge ok", "DA section"));
      if (d.t2) row.appendChild(el("span", "wb-badge ok", "link ok"));
      wrap.appendChild(row);
      if (d.quote) wrap.appendChild(el("div", "quote", d.quote));
      if (d.url) {
        const a = el("a", "", d.url.length > 72 ? d.url.slice(0, 72) + "…" : d.url);
        a.href = d.url; a.target = "_blank"; a.rel = "noopener";
        wrap.appendChild(a);
      }
    });
    return wrap;
  }

  function catalogSection(catalog) {
    const catSec = el("div", "wb-sec");
    catSec.appendChild(el("h4", "", `Catalog records — verified (${(catalog || []).length})`));
    catSec.appendChild(el("p", "wb-secinfo",
      "Curated in BioAgentDB — each accession appears in the paper's evidence quote; links checked."));
    (catalog || []).forEach((it) => {
      const card = el("div", "wb-card");
      card.appendChild(el("div", "t", `${it.title} ${it.year ? `(${it.year})` : ""}`));
      const meta = el("div", "meta");
      (it.datasets || []).forEach((ds) => {
        const b = el("span", "wb-badge mono", ds.accession || "?");
        b.title = `${ds.repository || ""} · ${ds.status || ""}`;
        meta.appendChild(b);
      });
      const vb = el("span", "wb-badge ok", "verified");
      vb.title = "Curated record — accession matches the paper's evidence quote.";
      meta.appendChild(vb);
      card.appendChild(meta);
      if (it.evidence) card.appendChild(el("div", "quote", it.evidence));
      card.appendChild(recordLink(it));
      catSec.appendChild(card);
    });
    if (!(catalog || []).length) catSec.appendChild(el("p", "wb-empty", "No catalog records matched."));
    return catSec;
  }

  function candidateSection(candidates) {
    const candSec = el("div", "wb-sec");
    candSec.appendChild(el("h4", "", `Papers to inspect (${(candidates || []).length})`));
    candSec.appendChild(el("p", "wb-secinfo",
      "Found by live sources — not catalogued yet. Use “Verify now” to read a paper's full text and check its links on the spot."));
    (candidates || []).forEach((it) => {
      const card = el("div", "wb-card");
      card.appendChild(el("div", "t", `${it.title} ${it.year ? `(${it.year})` : ""}`));
      const meta = el("div", "meta");
      (it.sources || []).forEach((s) => meta.appendChild(el("span", "wb-badge", SOURCE_LABEL[s] || s)));
      const vb = el("span", "wb-badge warn", "not yet verified");
      vb.title = "Not catalogued yet — found by live sources; use Verify now to check it.";
      vb.dataset.role = "verifybadge";
      meta.appendChild(vb);
      if (it.fulltext_kind && it.fulltext_kind !== "none") {
        meta.appendChild(el("span", "wb-badge ok", FT_LABEL[it.fulltext_kind] || it.fulltext_kind));
      }
      if (it.t2) meta.appendChild(el("span", "wb-badge ok", "link reachable"));
      card.appendChild(meta);
      if ((it.datasets || []).length) card.appendChild(datasetBlock(it.datasets));
      const url = it.landing_url || it.oa_url || (it.doi ? `https://doi.org/${it.doi}` : "");
      if (url) {
        const a = el("a", "", url.length > 72 ? url.slice(0, 72) + "…" : url);
        a.href = url; a.target = "_blank"; a.rel = "noopener";
        card.appendChild(a);
      }
      card.appendChild(verifyBlock(it));
      candSec.appendChild(card);
    });
    if (!(candidates || []).length) candSec.appendChild(el("p", "wb-empty", "No papers to inspect."));
    return candSec;
  }

  function datasetRows(rs) {
    const rows = [];
    (rs.catalog || []).forEach((it) => (it.datasets || []).forEach((d) =>
      rows.push({ d, it, verified: true })));
    (rs.candidates || []).forEach((it) => (it.datasets || []).forEach((d) =>
      rows.push({ d, it, verified: false })));
    return rows;
  }

  function datasetSection(rs) {
    const rows = datasetRows(rs);
    const sec = el("div", "wb-sec");
    sec.appendChild(el("h4", "", `Datasets (${rows.length})`));
    sec.appendChild(el("p", "wb-secinfo",
      "One row per accession — switch back to “By paper” for titles and evidence quotes."));
    if (!rows.length) {
      sec.appendChild(el("p", "wb-empty", "No accessions in this result."));
      return sec;
    }
    [["Verified — in the catalog", rows.filter((r) => r.verified)],
     ["Not yet verified — from live sources", rows.filter((r) => !r.verified)]
    ].forEach(([label, list]) => {
      if (!list.length) return;
      sec.appendChild(el("h5", "wb-dsh", `${label} (${list.length})`));
      list.forEach(({ d, it, verified }) => {
        const card = el("div", "wb-card wb-dscard");
        const meta = el("div", "meta");
        const ab = el("span", "wb-badge mono", d.accession || "?");
        ab.title = [d.repository, d.status].filter(Boolean).join(" · ") || "accession";
        meta.appendChild(ab);
        if (d.repository) meta.appendChild(el("span", "wb-badge", d.repository));
        if (d.status && d.status.toLowerCase() !== "verified")
          meta.appendChild(el("span", "wb-badge", d.status));
        if (!verified && d.da_evidence) meta.appendChild(el("span", "wb-badge ok", "Data Availability"));
        const vb = el("span", "wb-badge " + (verified ? "ok" : "warn"),
          verified ? "verified" : "not yet verified");
        vb.title = verified
          ? "Curated record — accession matches the paper's evidence quote."
          : "Not catalogued yet — found by live sources; inspect before use.";
        meta.appendChild(vb);
        card.appendChild(meta);
        card.appendChild(el("div", "t",
          `${it.title || ""}${it.year ? ` (${it.year})` : ""}`));
        if (verified) {
          card.appendChild(recordLink(it));
        } else {
          const url = it.landing_url || it.oa_url || (it.doi ? `https://doi.org/${it.doi}` : "");
          if (url) {
            const a = el("a", "", url.length > 64 ? url.slice(0, 64) + "…" : url);
            a.href = url; a.target = "_blank"; a.rel = "noopener";
            card.appendChild(a);
          }
        }
        sec.appendChild(card);
      });
    });
    return sec;
  }

  function renderResultSets() {
    resultsEl.innerHTML = "";
    if (!resultSets.length) {
      resultsEl.appendChild(el("p", "wb-empty",
        "Ask a question — catalog records, paper-announced datasets and a report will appear here."));
      enableExports(false);
      return;
    }
    resultSets.forEach((rs, idx) => {
      if (rs.view === undefined) rs.view = "paper";
      if (rs.checked === undefined) rs.checked = true;
      const card = el("details", "wb-rset" + (idx === 0 ? " latest" : ""));
      card.open = rs.open !== undefined ? rs.open : idx === 0;
      card.addEventListener("toggle", () => { rs.open = card.open; });
      const sum = el("summary", "wb-rshead");
      sum.appendChild(el("span", "wb-rchev", "▸"));
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.className = "wb-rcheck"; cb.checked = rs.checked;
      cb.title = "include this result in exports";
      cb.onclick = (e) => e.stopPropagation();
      cb.onchange = () => { rs.checked = cb.checked; updateExportState(); };
      sum.appendChild(cb);
      sum.appendChild(el("span", "wb-rts", fmtTs(rs.ts)));
      const nDs = datasetRows(rs).length;
      sum.appendChild(el("span", "wb-rsum",
        `${rs.catalog.length} verified · ${rs.candidates.length} to inspect`
        + (nDs ? ` · ${nDs} datasets` : "")
        + (rs.report.rubric_ok === false ? " · quality issues" : "")));
      card.appendChild(sum);
      const body = el("div", "wb-rsbody");
      body.appendChild(reportSummaryBox(rs.report));
      if (rs.report.relax_suggestion) {
        body.appendChild(el("div", "wb-relax", rs.report.relax_suggestion));
      }
      const bar = el("div", "wb-viewbar");
      const mk = (key, label) => {
        const b = el("button", "wb-vbtn" + (rs.view === key ? " on" : ""), label);
        b.type = "button";
        b.onclick = (e) => { e.preventDefault(); e.stopPropagation(); rs.view = key; renderResultSets(); };
        return b;
      };
      bar.appendChild(mk("paper", `By paper (${rs.catalog.length + rs.candidates.length})`));
      bar.appendChild(mk("dataset", `By dataset (${nDs})`));
      body.appendChild(bar);
      if (rs.view === "dataset") {
        body.appendChild(datasetSection(rs));
      } else {
        body.appendChild(catalogSection(rs.catalog));
        body.appendChild(candidateSection(rs.candidates));
      }
      card.appendChild(body);
      resultsEl.appendChild(card);
    });
    updateExportState();
  }

  function updateExportState() {
    const any = Array.from(resultsEl.querySelectorAll(".wb-rcheck")).some((c) => c.checked);
    enableExports(any);
  }

  function selectedSets() {
    const cards = Array.from(resultsEl.querySelectorAll(".wb-rset"));
    const out = [];
    cards.forEach((card, i) => {
      const cb = card.querySelector(".wb-rcheck");
      if (cb && cb.checked && resultSets[i]) out.push(resultSets[i]);
    });
    return out;
  }

  function enableExports(on) {
    expJson.disabled = !on; expCsv.disabled = !on;
  }
  function download(name, text, mime) {
    const blob = new Blob([text], { type: mime });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 400);
  }
  function exportJSONL() {
    const sets = selectedSets();
    if (!sets.length) return;
    const lines = [];
    sets.forEach((rs) => {
      if (rs.report.research) {
        lines.push(JSON.stringify({ result_time: rs.ts, type: "research",
          summary: rs.report.research.summary || "",
          search_terms: rs.report.research.search_terms || [],
          exclusions: rs.report.research.exclusions || [],
          sources: rs.report.research.sources || [] }));
      }
      (rs.report.items || []).forEach((i) =>
        lines.push(JSON.stringify({ result_time: rs.ts, ...i })));
    });
    download("bioagentdb-workbench.jsonl", lines.join("\n") + "\n", "application/jsonl");
  }
  function exportCSV() {
    const sets = selectedSets();
    if (!sets.length) return;
    const rows = [["result_time", "type", "title", "year", "sources", "doi", "pmid",
      "catalog_accessions", "announced_accessions", "url"]];
    sets.forEach((rs) => (rs.report.items || []).forEach((i) => {
      rows.push([rs.ts, i.type, i.title, i.year || "",
        (i.sources || []).join("|"), i.doi || "", i.pmid || "",
        (i.datasets || []).map((d) => d.accession).join("|"),
        (i.datasets || []).map((d) => d.accession).join("|"),
        i.landing_url || ""]);
    }));
    const csv = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
    download("bioagentdb-workbench.csv", csv + "\n", "text/csv");
  }

  /* ---------------- record detail (shared modal) + live verify ---------------- */
  async function wbOpenRecord(paperId) {
    if (!paperId || !window.showPaperDetail) return;
    try {
      const it = await api(`/api/records/${paperId}`);
      window.showPaperDetail(it);
    } catch (e) { /* keep silent */ }
  }

  function recordLink(it) {
    const a = el("a", "", "view record →");
    a.href = "#";
    a.title = "open the full record — datasets, links and evidence";
    a.onclick = (e) => { e.preventDefault(); wbOpenRecord(it.paper_id); };
    return a;
  }

  function verifyBlock(it) {
    const wrap = el("div", "wb-verify");
    const out = el("div", "wb-vres");
    const badge = () => wrap.closest(".wb-card")?.querySelector("[data-role=verifybadge]");
    const renderResult = (r) => {
      out.innerHTML = "";
      out.appendChild(el("div", "hd", `Checked just now · ${fmtTs(r.checked_at)}`));
      if (r.link) {
        const row = el("div", "row");
        row.appendChild(el("span", "wb-badge " + (r.link.ok ? "ok" : "warn"),
          r.link.ok ? "paper link reachable" : "paper link not reachable"));
        const a = el("a", "", r.link.url.length > 60 ? r.link.url.slice(0, 60) + "…" : r.link.url);
        a.href = r.link.url; a.target = "_blank"; a.rel = "noopener";
        row.appendChild(a);
        out.appendChild(row);
      }
      (r.datasets || []).forEach((d) => {
        const row = el("div", "row");
        row.appendChild(el("span", "wb-badge mono", d.accession || "?"));
        if (d.repository) row.appendChild(el("span", "wb-badge", d.repository));
        row.appendChild(el("span", "wb-badge " + (d.t2 ? "ok" : "warn"),
          d.t2 ? "link checked" : "link not reachable"));
        if (d.da_evidence) row.appendChild(el("span", "wb-badge ok", "quoted in Data Availability"));
        out.appendChild(row);
        if (d.quote) out.appendChild(el("div", "quote", d.quote));
      });
      out.appendChild(el("div", "note", r.note || ""));
      const vb = badge();
      if (vb) {
        const passed = (r.datasets || []).some((d) => d.t2 && d.da_evidence);
        if (passed) {
          vb.className = "wb-badge ok";
          vb.textContent = "verified just now — not catalogued yet";
        } else if (r.fulltext_kind && r.fulltext_kind !== "none") {
          vb.textContent = "checked live — no accession found";
        } else {
          vb.textContent = "checked live — no open full text";
        }
      }
    };
    const btn = el("button", "wb-btn small", it._verified_live ? "Verify again" : "Verify now");
    btn.type = "button";
    btn.title = "read the paper's full text (if open) and check its links now";
    btn.onclick = async () => {
      btn.disabled = true; btn.textContent = "Checking…";
      out.innerHTML = "";
      out.appendChild(el("div", "hd",
        "Reading the paper's full text and checking links — this can take a few seconds…"));
      try {
        const r = await api("/api/chat/verify-candidate", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: it.title || "", doi: it.doi || "", pmid: it.pmid || "",
                                 landing_url: it.landing_url || "", oa_url: it.oa_url || "",
                                 is_preprint: !!it.is_preprint }),
        });
        it._verified_live = r;
        renderResult(r);
      } catch (e) {
        out.innerHTML = "";
        out.appendChild(el("div", "hd", "Check failed: " + e.message));
      }
      btn.disabled = false; btn.textContent = "Verify again";
    };
    wrap.appendChild(btn);
    if (it._verified_live) renderResult(it._verified_live);
    wrap.appendChild(out);
    return wrap;
  }

  /* ---------------- streaming ---------------- */
  function setBusy(b) {
    sendBtn.disabled = b; stopBtn.disabled = !b;
  }

  function handleEvent(ev, turn) {
    switch (ev.event) {
      case "step":
        stepLine(turn, ev.name, ev.status, ev.detail, ev.counts, ev.ms);
        break;
      case "progress":
        progressLine(turn, ev);
        break;
      case "token":
        tokenBubble(turn, ev.text);
        break;
      case "profile":
        renderProfile(ev.profile, ev.ready, ev.missing, ev.defaults);
        break;
      case "clarify":
        bubble("assistant", ev.text);
        chips(ev.options || [], (o) => send(o));
        break;
      case "refusal":
        bubble("assistant", ev.text);
        break;
      case "results":
        if (ev.note) { const n = el("div", "wb-note", ev.note); log.appendChild(n); }
        break;
      case "research":
        renderResearch(ev.research, turn);
        break;
      case "report": {
        const rep = ev.report || {};
        const cat = (rep.items || []).filter((i) => i.type === "catalog");
        const cand = (rep.items || []).filter((i) => i.type === "candidate");
        resultSets.unshift({ ts: rep.generated_at || new Date().toISOString(),
                             report: rep, catalog: cat, candidates: cand });
        renderResultSets();
        break;
      }
      case "error": bubble("error", ev.message); break;
      case "done": {
        const sec = ((Date.now() - (turn._t0 || Date.now())) / 1000).toFixed(0);
        const sum = el("div", "wb-done-line",
          `Completed · ${turn._doneCount || 0} steps · ${sec}s`);
        turn.appendChild(sum);
        const tb = turn.querySelector(".wb-msg.assistant.md");
        if (tb && !tb.querySelector(".wb-ts")) stamp(tb);
        turn.querySelectorAll(".wb-pline.cur").forEach((n) => n.classList.remove("cur"));
        scrollLog();
        break;
      }
      default: break;
    }
  }

  async function send(text) {
    if (busy || !text || !text.trim()) return;
    busy = true; setBusy(true);
    input.value = "";
    bubble("user", text);
    try {
      if (!sid) {
        const s = await api("/api/chat/sessions", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: text.slice(0, 70) }),
        });
        sid = s.id;
      }
      const turn = el("div", "wb-steps");
      turn._t0 = Date.now();
      turn._doneCount = 0;
      log.appendChild(turn);
      abortCtrl = new AbortController();
      const resp = await fetch(`/api/chat/sessions/${sid}/message`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }), signal: abortCtrl.signal,
      });
      if (!resp.ok || !resp.body) throw new Error(`stream failed (${resp.status})`);
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
          for (const line of chunk.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            let ev; try { ev = JSON.parse(line.slice(6)); } catch { continue; }
            handleEvent(ev, turn);
          }
        }
      }
    } catch (e) {
      if (e.name === "AbortError") bubble("assistant", "Stopped.");
      else bubble("error", "Error: " + e.message);
    } finally {
      busy = false; setBusy(false);
      loadSessions();
    }
  }

  /* ---------------- wiring ---------------- */
  sendBtn.onclick = () => send(input.value);
  stopBtn.onclick = () => { if (abortCtrl) abortCtrl.abort(); };
  $("wb-new").onclick = newSession;
  expJson.onclick = exportJSONL;
  expCsv.onclick = exportCSV;
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input.value); }
  });
  window.wbSend = send;
  window.wbNew = newSession;

  /* ---------------- modes + Ming panel (L-0223) ---------------- */
  const researchPane = $("wb-research-pane"), mingPane = $("wb-ming-pane");
  const modeBtns = [$("wb-mode-research"), $("wb-mode-ming")];
  function setMode(m) {
    localStorage.setItem("wbMode", m);
    researchPane.classList.toggle("wb-hidden", m !== "research");
    mingPane.classList.toggle("wb-hidden", m !== "ming");
    modeBtns.forEach((b) => b.classList.toggle("active", b.dataset.mode === m));
    if (m === "ming" && !mnlLog.childElementCount) renderMingIntro();
    loadSessions();
  }
  modeBtns.forEach((b) => { b.onclick = () => setMode(b.dataset.mode); });
  setMode(localStorage.getItem("wbMode") === "ming" ? "ming" : "research");

  function mingSession() {
    let s = localStorage.getItem("chatSession");
    if (!s) {
      s = "ming-" + Math.random().toString(36).slice(2, 10);
      localStorage.setItem("chatSession", s);
    }
    return s;
  }
  function splitLines(v) {
    return String(v || "").split("\n").map((s) => s.trim()).filter(Boolean);
  }
  /* ---- Ming chat (L-0234/L-0235): a conversation, same as Research ---- */
  const mnlScroll = () => { mnlLog.scrollTop = mnlLog.scrollHeight; };
  function setMingBusy(b) {
    mnlBusy = b;
    mnlSend.disabled = b;
    mnlStop.disabled = !b;
    mnlInput.disabled = b;
  }
  function mnlBubble(role, text, ts) {
    const d = el("div", `wb-msg ${role}`);
    d.appendChild(el("span", "wb-body", text));
    stamp(d, ts);
    mnlLog.appendChild(d); mnlScroll(); return d;
  }
  function mnlToken(turn, text) {
    let b = turn.querySelector(".wb-msg.assistant.md");
    if (!b) { b = el("div", "wb-msg assistant md"); b._raw = ""; turn.appendChild(b); }
    b._raw += text;
    b.innerHTML = renderMarkdown(b._raw);
    mnlScroll();
  }
  function renderMingIntro() {
    const d = el("div", "wb-note");
    d.innerHTML = "Describe what to mine — the agent chats the goal through with you " +
      '(answer freely; say "any" to skip), then proposes the task. Mining runs in the ' +
      "background: first until the literature is exhausted, then a patrol. Verified records " +
      'are published to the public catalog and tracked in <a href="/mining.html">My mining</a>.';
    mnlLog.appendChild(d);
  }
  function renderMingProposal(turn, r) {
    const wrap = el("div", "wb-ming-result");
    const card = el("div", "card");
    const head = el("div", "row");
    head.appendChild(el("strong", null, r.theme_label || r.theme));
    head.appendChild(el("span", "badge",
      (r.kind === "topic" ? "Ming topic" : "disease theme")
      + (r.existing ? " · existing" : " · new")));
    card.appendChild(head);
    card.appendChild(el("div", "wb-note", "theme: " + r.theme));
    card.appendChild(el("label", null, "search queries (one per line, editable)"));
    const q = el("textarea");
    q.rows = Math.min(8, Math.max(3, (r.queries || []).length));
    q.value = (r.queries || []).join("\n");
    card.appendChild(q);
    const row2 = el("div", "row");
    row2.appendChild(el("label", null, "papers/round"));
    const pr = el("input"); pr.type = "number"; pr.min = "1";
    pr.value = String(r.papers_per_round || 200);
    row2.appendChild(pr);
    row2.appendChild(el("label", null, "patrol every (h)"));
    const cad = el("input"); cad.type = "number"; cad.min = "1";
    cad.value = String(r.cadence_hours || 24);
    row2.appendChild(cad);
    const btn = el("button", "wb-btn primary", "Start Ming");
    btn.type = "button";
    btn.onclick = async () => {
      btn.disabled = true; btn.textContent = "Starting…";
      try {
        const out = await api("/api/mining_tasks", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            theme: r.theme,
            name: r.theme_label || r.theme,
            queries: splitLines(q.value),
            papers_per_round: Number(pr.value) || undefined,
            cadence_hours: Number(cad.value) || undefined,
            session_id: mingSession(),
            theme_kind: r.kind === "topic" ? "topic" : "",
            note: (mnlGoal || "").slice(0, 300),
          }),
        });
        btn.remove();
        const ok = el("div", "wb-note");
        ok.append(`Task started (${out.task_id}) — it runs in the background; open `);
        const a = document.createElement("a");
        a.href = "/mining.html"; a.textContent = "My mining";
        ok.appendChild(a); ok.append(" to watch it.");
        card.appendChild(ok);
      } catch (e) {
        btn.disabled = false; btn.textContent = "Start Ming";
        card.appendChild(el("div", "wb-note", "Failed: " + e.message));
      }
    };
    row2.appendChild(btn);
    card.appendChild(row2);
    wrap.appendChild(card);
    (turn || mnlLog).appendChild(wrap);
    mnlScroll();
  }
  function handleMingEvent(ev, turn) {
    switch (ev.event) {
      case "token": mnlToken(turn, ev.text); break;
      case "proposal": renderMingProposal(turn, ev.proposal || {}); break;
      case "error": mnlBubble("error", ev.message || "agent error"); break;
      case "done": {
        const tb = turn.querySelector(".wb-msg.assistant.md");
        if (tb && !tb.querySelector(".wb-ts")) stamp(tb);
        mnlScroll();
        break;
      }
      default: break;
    }
  }
  async function mingSend(text) {
    if (mnlBusy || !text || !text.trim()) return;
    setMingBusy(true);
    mnlInput.value = "";
    const msg = text.trim();
    if (!mnlGoal) mnlGoal = msg.slice(0, 300);
    mnlBubble("user", msg);
    try {
      mnlAbort = new AbortController();
      if (!mnlSid) {
        const s = await api("/api/chat/sessions", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: msg.slice(0, 70), kind: "ming" }),
          signal: mnlAbort.signal,
        });
        mnlSid = s.id;
      }
      const turn = el("div", "wb-steps");
      turn._t0 = Date.now();
      mnlLog.appendChild(turn);
      const resp = await fetch(`/api/chat/sessions/${mnlSid}/message`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg }), signal: mnlAbort.signal,
      });
      if (!resp.ok || !resp.body) throw new Error(`stream failed (${resp.status})`);
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
          for (const line of chunk.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            let ev; try { ev = JSON.parse(line.slice(6)); } catch { continue; }
            handleMingEvent(ev, turn);
          }
        }
      }
    } catch (e) {
      if (e.name === "AbortError") mnlBubble("assistant", "Stopped.");
      else mnlBubble("error", "Error: " + e.message);
    } finally {
      setMingBusy(false);
      loadSessions();
    }
  }
  mnlSend.onclick = () => mingSend(mnlInput.value);
  mnlStop.onclick = () => { if (mnlAbort) mnlAbort.abort(); };
  mnlInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); mingSend(mnlInput.value); }
  });

  async function openMingSession(id) {
    try {
      const data = await api(`/api/chat/sessions/${id}`);
      mnlSid = id; mnlGoal = "";
      mnlLog.innerHTML = "";
      (data.messages || []).forEach((m) => {
        if (m.role === "user") {
          if (!mnlGoal) mnlGoal = String(m.content || "").slice(0, 300);
          mnlBubble("user", m.content, m.created_at);
          return;
        }
        if (m.role !== "assistant") return;
        const meta = m.meta || {};
        mnlBubble("assistant", m.content, m.created_at);
        if (meta.kind === "proposal" && meta.proposal) {
          renderMingProposal(mnlLog, meta.proposal);
        }
      });
      if (!(data.messages || []).length) renderMingIntro();
      loadSessions();
    } catch (e) { /* silent */ }
  }

  newSession();
  (currentKind() === "ming" ? mnlInput : input).focus();
})();
