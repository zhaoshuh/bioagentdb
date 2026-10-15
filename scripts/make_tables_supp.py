"""生成 Supplementary Tables S3–S11 并就地替换 `docs/paper/final/tables.md` 的对应区段。

数据源（全部为权威源，不解析论文、不写死数字）:
  S3 error taxonomy     data/gold/error-analysis.json
  S4 release history    docs/versions/<v>-stats/resource-stats.json → totals
  S5 filter ablation    data/gold/ablation-report.json → policies
  S6 normalization      data/releases/_v15/bioagentdb.sqlite3 → record_normalization
  S7 record status      data/releases/_v15/bioagentdb.sqlite3 → datasets.status
  S8 repositories       docs/versions/v1.5-stats/resource-stats.json → repositories
  S9 multi-source       发布说明口径的冻结字面量（源码即真源；见 docs/paper/final/TABLES-NOTES.md）

用法: python scripts/make_tables_supp.py [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "paper" / "final" / "tables.md"
GOLD = ROOT / "data" / "gold"
VERSIONS = ROOT / "docs" / "versions"
import os  # noqa: E402

DB = Path(os.environ.get("BIOAGENTDB_EVAL_DB")
          or ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3")
RELEASES = ("v0.9", "v1.0", "v1.1", "v1.2", "v1.3", "v1.4", "v1.5")
START = "## Supplementary Table S3 "


def _json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def table(head: list[str], rows: list[list[object]]) -> list[str]:
    def _fmt(c: object) -> str:
        return f"{c:,}" if isinstance(c, int) else str(c)   # L-0261：千分位（与正文口径一致）
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(_fmt(c) for c in r) + " |" for r in rows]
    return out


def s3() -> list[str]:
    e = _json(GOLD / "error-analysis.json")
    fp, fn = e.get("fp_categories", {}), e.get("fn_categories", {})
    ref_gap = fp.get("in_text_but_reference_missed", 0) + fp.get("not_in_sample_text", 0)
    missed = fn.get("accession_missed", 0)
    no_data = fn.get("paper_predicted_no_data", 0)
    return ["## Supplementary Table S3 · Error taxonomy (record level, n = 250)", ""] + table(
        ["category", "count"],
        [["false positives (identifier in the text, reference gap)", ref_gap],
         ["false negatives — accession missed", missed],
         ["false negatives — predicted no data", no_data],
         ["false negatives (total)", missed + no_data]]) + [
        "", "*Raw-extraction view — all records the pipeline produced, before reviewer refusals "
            "and gate rejections (the released catalog is scored in §6.2–§6.3).*"]


def s4() -> list[str]:
    rows = []
    for i, v in enumerate(RELEASES, 1):
        t = _json(VERSIONS / f"{v}-stats" / "resource-stats.json").get("totals", {})
        rows.append([f"Stage {i}", t.get("papers"), t.get("papers_mined"), t.get("papers_with_data"),
                     t.get("datasets"), t.get("datasets_verified")])
    return (["## Supplementary Table S4 · Catalog growth", ""] +
            table(["stage", "papers", "mined", "with data", "datasets", "verified"], rows) +
            ["", "*Stages 3 and 4 share the same totals (rescoped themes).*",
             "", "*Stage 7 = v1.5 (rejected 12→85; verified 2,277→2,204 = 2,076 fully verified "
                 "+ 128 paper-verified).*"])


def s5() -> list[str]:
    pol = _json(GOLD / "ablation-report.json").get("policies", {})
    rows = []
    for k in ("all", "verified", "verified_grounded"):
        if k in pol:
            p = pol[k]
            rows.append([k.replace("_", " "),
                         f"{p['precision'] * 100:.1f}%", f"{p['recall'] * 100:.1f}%",
                         f"{p['f1'] * 100:.1f}%"])
    vg, vf = pol.get("verified_grounded"), pol.get("verified")
    if vg and vf:
        note = ("*`verified grounded` adds the T1 sub-filter on top of `verified` (recall "
                f"{vf['recall'] * 100:.1f}% vs {vg['recall'] * 100:.1f}%; precision effectively "
                f"unchanged: {vf['precision'] * 100:.1f}% vs {vg['precision'] * 100:.1f}%).*")
    else:
        note = "*`verified grounded` adds the T1 sub-filter on top of `verified`.*"
    return (["## Supplementary Table S5 · Post-hoc filter ablation (regex-reference)", ""] +
            table(["policy", "precision", "recall", "F1"], rows) + ["", note])


def _norm_stats() -> tuple[int, int, int, int]:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        total = c.execute("SELECT COUNT(*) FROM record_normalization").fetchone()[0]
        canon = c.execute("SELECT COUNT(*) FROM record_normalization "
                          "WHERE canonical IS NOT NULL AND canonical != ''").fetchone()[0]
        quote = c.execute("SELECT COUNT(*) FROM record_normalization "
                          "WHERE quote IS NOT NULL AND quote != ''").fetchone()[0]
        multi = c.execute("SELECT COUNT(*) FROM (SELECT record_id, field FROM record_normalization "
                          "GROUP BY record_id, field HAVING COUNT(*) > 1)").fetchone()[0]
    finally:
        c.close()
    return total, canon, quote, multi


def s6() -> list[str]:
    total, canon, quote, multi = _norm_stats()
    pct = f"{100 * quote / total:.1f}%" if total else "n/a"
    return (["## Supplementary Table S6 · Normalization quality (norm_v2)", ""] + table(
        ["metric", "value"],
        [["mapped rows", total], ["rows with a canonical value", canon],
         ["unmapped rows", total - canon], ["multi-value (record, field) pairs", multi],
         ["rows with a verbatim quote", f"{quote:,} ({pct})"]]) +
        ["", f"*“rows with a canonical value” equals “mapped rows” ({total:,}) by construction: every "
             "mapped row carries a canonical value in norm_v2.*"])


def s7() -> list[str]:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = c.execute("SELECT status, COUNT(*) n FROM datasets GROUP BY status "
                         "ORDER BY n DESC, status").fetchall()
        total = c.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
    finally:
        c.close()
    return ["## Supplementary Table S7 · Record status breakdown", ""] + table(
        ["status", "datasets"], [[s, n] for s, n in rows] + [["total", total]]) + [
        "", "*2,204 verified = 2,076 fully verified (T1 + T2) + 128 paper-verified "
            "(T1-grounded; T2 recorded rather than blocking, 61 of the 128 without a "
            "reachable link; §3.3, §4.3).*"]


def s8() -> list[str]:
    repos = _json(VERSIONS / f"{RELEASES[-1]}-stats" / "resource-stats.json").get("repositories", {})
    rows = [[k, v] for k, v in sorted(repos.items(), key=lambda kv: (-kv[1], kv[0]))]
    total = sum(repos.values())
    return (["## Supplementary Table S8 · Repository distribution "
             f"(all {len(repos)})", ""] +
            table(["repository", "dataset records"], rows + [["total (dataset records)", total]]))


def s9() -> list[str]:
    """§3.5 过滤式多源扩展 —— 数字为发布说明口径的冻结字面量（源码即真源）。"""
    return (["## Supplementary Table S9 · Multi-source expansion", ""] +
            table(["metric", "value"],
                  [["candidate papers screened (relevance-filtered)", 482],
                   ["imported into the catalog (pending, relevance-guarded)", 474],
                   ["yielded public data", "15 of 474 (3.2%)"],
                   ["dataset records added", "+21"],
                   ["sources", "primary: Europe PMC; expansion: OpenAlex, CORE, PubMed, bioRxiv/medRxiv preprints"]]) +
            ["", "*Most newly imported candidates have no PMC full text; only the abstract zone is "
                 "available for those records (recorded per record). (The v1.3 initial cycle; no "
                 "new refill in v1.4 or v1.5.)*"])


def s10() -> list[str]:
    d = _json(ROOT / "data" / "eval" / "vs-resources.json")
    e = d.get("europepmc_accessions") or {}
    o = d.get("omicsdi") or {}
    ev = _json(GOLD / "eval-report.json").get("record_level", {})
    evc = _json(GOLD / "catalog-view" / "eval-report.json").get("record_level", {})
    rows = [
        ["BioAgentDB (this work; released catalog)",
         f"{evc.get('precision', 0):.3f}", f"{evc.get('recall', 0):.3f}", f"{evc.get('f1', 0):.3f}"],
        ["BioAgentDB (raw extraction)",
         f"{ev.get('precision', 0):.3f}", f"{ev.get('recall', 0):.3f}", f"{ev.get('f1', 0):.3f}"],
        ["Europe PMC (text-mined accessions)",
         f"{e.get('precision', 0):.3f}", f"{e.get('recall', 0):.3f}", f"{e.get('f1', 0):.3f}"],
        ["Europe PMC (curated database links)", "—", "0 of 250 papers", "—"],
        ["OmicsDI (dataset coverage)", "—", f"{o.get('coverage', 0):.3f} "
                                          f"({o.get('covered')}/{o.get('checked')})", "—"],
    ]
    return (["## Supplementary Table S10 · Same-sample comparison with existing resources "
             "(250 papers; 291 gold accessions)", ""] +
            table(["system", "precision", "recall", "F1"], rows) +
            ["", "*All rows use the same human gold and the same accession normalization "
                 "(fixed case-fold, version suffixes, range endpoints). Europe PMC text-mined "
                 "accessions are filtered to strings matching the databank vocabulary; OmicsDI "
                 "is measured as accession coverage (precision not comparable).*"])


def s11() -> list[str]:
    a = _json(ROOT / "data" / "eval" / "ablation-components.json")
    var = a.get("variants") or {}
    rows = [[k, f"{v.get('precision', 0):.3f}", f"{v.get('recall', 0):.3f}",
             f"{v.get('f1', 0):.3f}", f"{v.get('t1_rate', 0):.3f}"]
            for k, v in var.items()]
    out = (["## Supplementary Table S11 · Reviewer complementarity and component ablations "
            "(re-runs)", ""] +
           table(["variant", "precision", "recall", "F1", "T1"], rows))
    r = _json(ROOT / "data" / "eval" / "reviewer-agreement.json")
    comp = r.get("flagged_targets_by_adversary_count") or {}
    if comp:
        comp_txt = ", ".join(f"{k}: {v}" for k, v in sorted(comp.items()))
        out += ["", "*Reviewer complementarity (60 papers, re-run): flagged (paper, target) units "
                    "by the number of reviewers that flagged them — " + comp_txt + ".*"]
    rev = a.get("prompt_revision") or r.get("prompt_revision") or ""
    gen = a.get("generated_at") or r.get("generated_at") or ""
    if rev or gen:
        out += ["", "*Re-run anchor: prompt revision " + (rev or "n/a") +
                (f"; generated {gen}" if gen else "") + ".*"]
    return out


def build() -> str:
    blocks = [s3(), s4(), s5(), s6(), s7(), s8(), s9(), s10(), s11()]
    return "\n\n".join("\n".join(b) for b in blocks) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    out = Path(args.out)
    text = out.read_text(encoding="utf-8")
    idx = text.find(START)
    if idx < 0:
        raise SystemExit(f"{START!r} not found in {out}")
    head = text[:idx]
    out.write_text(head + build(), encoding="utf-8")
    print(f"wrote {out} (S3–S11)")


if __name__ == "__main__":
    main()
