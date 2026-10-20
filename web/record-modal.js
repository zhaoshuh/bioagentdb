/* Shared record / dataset detail modal.
   Used by the ledger (app.js) and Ask the agent (workbench.js).
   Exposes: window.showPaperDetail(record), window.showDetail(datasetId), window.closeModal(). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const MOD_LABEL = {
    transcriptomics: "transcriptomics", proteomics: "proteomics", epigenomics: "epigenomics",
    genomics: "genomics", scrna: "scRNA-seq", snrna: "snRNA-seq", scatac: "scATAC-seq",
    spatial_transcriptomics: "spatial transcriptomics",
    spatial_proteomics: "spatial proteomics", multi_omics: "multi-omics",
    microbiome: "microbiome", metagenomics: "metagenomics", metabolomics: "metabolomics",
    lipidomics: "lipidomics", phosphoproteomics: "phosphoproteomics",
  };

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function cleanQuote(text, max) {
    let s = String(text || "").replace(/[\s]+$/, "");
    s = s.replace(/[.\u2026\s]+$/, "");
    s = s.replace(/\s+([,.;:!?])/g, "$1");
    if (s.length > max) {
      s = s.slice(0, max).replace(/\s+\S*$/, "");
      s = s.replace(/\s+(and|or|of|in|the|on|for|with|a|an|to|by|at|from|its|their)$/i, "");
    }
    return s;
  }
  async function api(path, body) {
    const opt = { headers: { "Content-Type": "application/json" } };
    if (body !== undefined) { opt.method = "POST"; opt.body = JSON.stringify(body); }
    const r = await fetch(path, opt);
    if (!r.ok) throw new Error((await r.text()).slice(0, 300));
    return r.json();
  }

  function showPaperDetail(it) {
    const ds = (it.datasets || []).map((x) => {
      const l = (x.links || []).find((z) => z.is_primary) || (x.links || [])[0];
      const dl = (x.links || []).find((z) => z.link_type === "download");
      return `<div class="card static">
        <b>${esc(x.title || "dataset")}</b>
        <span class="chip ${x.status === "verified" ? "ok" : x.status === "paper_verified" ? "warn" : "bad"}">${esc(x.status)}</span>
        <div class="muted" style="font-size:13px;margin:4px 0">${esc(l ? l.repository : "")} ${esc(l ? l.accession : "")} · ${x.sample_count ?? "n/a"} samples · conf ${(x.confidence ?? 0).toFixed(2)}</div>
        ${x.description ? `<div style="font-size:13.5px;color:var(--ink-soft)">${esc(x.description)}</div>` : ""}
        ${l ? `<div class="muted" style="font-size:12.5px"><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.url)}</a></div>` : ""}
        ${dl ? `<div class="muted" style="font-size:12.5px"><a href="${esc(dl.url)}" target="_blank" rel="noopener">download: ${esc(dl.url)}</a></div>` : ""}
      </div>`;
    }).join("");
    const evs = (it.evidence || []).map((e) =>
      `<div class="card static"><span class="chip">${esc(e.zone)}</span><p style="font-family:var(--serif);font-style:italic">\u201C${esc(e.quote)}\u201D</p></div>`).join("");
    $("modal-title").textContent = it.title;
    $("modal-body").innerHTML = `
      <div class="muted" style="font-size:13px">${esc(it.journal || "n/a")} · ${it.year || ""}${it.is_preprint ? " · preprint" : ""}
        ${it.pmid ? ` · PMID <a href="https://pubmed.ncbi.nlm.nih.gov/${esc(it.pmid)}" target="_blank" rel="noopener">${esc(it.pmid)}</a>` : ""}
        ${it.doi ? ` · <a href="https://doi.org/${esc(it.doi)}" target="_blank" rel="noopener">${esc(it.doi)}</a>` : ""}</div>
      ${it.abstract ? `<p style="font-size:13.5px;color:var(--ink-soft)">${esc(it.abstract.slice(0, 420))}${it.abstract.length > 420 ? "&hellip;" : ""}</p>` : ""}
      <h3>Datasets announced in this paper (${(it.datasets || []).length})</h3>
      ${ds || "<p class='muted'>(none)</p>"}
      <h3>Evidence in the paper</h3>
      ${evs || "<p class='muted'>(none)</p>"}`;
    $("modal").classList.remove("hidden");
  }

  async function showDetail(datasetId) {
    try {
      const it = await api(`/api/datasets/${datasetId}`);
      if (!it) return;
      const nd = it.normalized || {};
      const primary = (it.links || []).find((l) => l.is_primary) || (it.links || [])[0] || {};
      const acc = primary.accession || "";
      const paper = (it.papers || [])[0] || {};
      const normRows = (field, label) => {
        const arr = nd[field] || [];
        if (!arr.length) {
          return `<div class="muted" style="font-size:13px">${label}: <i>not stated / not normalized</i></div>`;
        }
        return arr.map((x) => `<div style="margin:4px 0">
            <b>${label}:</b> <span class="chip dz">${esc(x.canonical)}</span>
            ${it[field] && it[field] !== x.canonical
              ? `<span class="muted" style="font-size:12.5px">raw: \u201C${esc(it[field])}\u201D</span>` : ""}
            ${x.quote ? `<div class="muted" style="font-size:12.5px;font-family:var(--serif);font-style:italic;margin-top:2px">\u201C${esc(x.quote)}\u201D</div>` : ""}
          </div>`).join("");
      };
      const mods = Array.isArray(it.modalities) && it.modalities.length
        ? it.modalities : (it.modality ? [it.modality] : []);
      $("modal-title").textContent =
        `${acc || "dataset"} \u00b7 ${cleanQuote(it.title || "", 60)}`;
      $("modal-body").innerHTML = `
        <div class="muted" style="font-size:13px">${esc(primary.repository || "")} \u00b7 ${esc(it.status || "")} \u00b7 conf ${(it.confidence ?? 0).toFixed(2)}
          ${primary.url ? ` \u00b7 <a href="${esc(primary.url)}" target="_blank" rel="noopener">open in repository</a>` : ""}</div>
        <h3>Normalized fields (paper-anchored)</h3>
        ${normRows("disease", "Disease")}
        ${normRows("organism", "Organism")}
        <div style="margin:4px 0"><b>Modalities:</b> ${mods.map((m) => `<span class="chip">${esc(MOD_LABEL[m] || m)}</span>`).join(" ") || "<i class='muted'>not stated</i>"}</div>
        <h3>Evidence in the paper</h3>
        ${(it.evidence || []).map((e) => `<div class="card static"><span class="chip">${esc(e.zone)}</span><p style="font-family:var(--serif);font-style:italic">\u201C${esc(e.quote)}\u201D</p></div>`).join("") || "<p class='muted'>(none)</p>"}
        ${paper.title ? `<h3>Source paper</h3><div style="font-size:13.5px">${esc(paper.title)} <span class="muted">\u00b7 ${paper.year || ""}</span></div>` : ""}`;
      $("modal").classList.remove("hidden");
    } catch (e) { /* keep silent */ }
  }

  function closeModal() { const m = $("modal"); if (m) m.classList.add("hidden"); }

  window.showPaperDetail = showPaperDetail;
  window.showDetail = showDetail;
  window.closeModal = closeModal;
  const modal = $("modal");
  if (modal) {
    modal.addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  }
})();
