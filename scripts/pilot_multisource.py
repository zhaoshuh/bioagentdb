"""多源试点运行器（S2/S3）：5 源 × 1,000 篇 → 去重 → 指标 → 报告。

用法:
  python scripts/pilot_multisource.py --sources europepmc,openalex,core,pubmed,preprints
  python scripts/pilot_multisource.py --only-metrics          # 仅重算指标/报告

产物（隔离于线上库）:
  data/pilot/multisource-pilot.sqlite3
  data/pilot/http_cache/<source>/*.json
  docs/expansion/pilot-report.md / pilot-report.json / qc-sample.csv
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import cfg  # noqa: E402
from app.harvest.dedup import check_new_to_db, merge_records  # noqa: E402
from app.harvest.extract import find_identifiers  # noqa: E402
from app.sources.base import CACHE_DIR  # noqa: E402
from app.sources.core_api import CoreAdapter  # noqa: E402
from app.sources.europepmc import EuropePmcAdapter, PreprintsAdapter  # noqa: E402
from app.sources.openalex import OpenAlexAdapter  # noqa: E402
from app.sources.pubmed import PubMedAdapter  # noqa: E402

PILOT_DB = ROOT / "data" / "pilot" / "multisource-pilot.sqlite3"
OUT_DIR = ROOT / "docs" / "expansion"

THEMES = [
    "alzheimers-disease", "als", "parkinsons-disease", "frontotemporal-dementia",
    "huntingtons-disease", "prion-disease", "progressive-supranuclear-palsy",
    "dementia-with-lewy-bodies", "multiple-system-atrophy", "spinocerebellar-ataxia",
]
REDISTRIBUTE = ["alzheimers-disease", "als", "parkinsons-disease"]

THEME_QUERIES: dict[str, list[str]] = {
    "alzheimers-disease": ["Alzheimer disease", "Alzheimer's disease", "dementia"],
    "als": ["amyotrophic lateral sclerosis", "motor neuron disease"],
    "parkinsons-disease": ["Parkinson disease", "Parkinson's disease"],
    "frontotemporal-dementia": ["frontotemporal dementia", "frontotemporal lobar degeneration"],
    "huntingtons-disease": ["Huntington disease", "Huntington's disease"],
    "prion-disease": ["Creutzfeldt-Jakob disease", "prion disease"],
    "progressive-supranuclear-palsy": ["progressive supranuclear palsy"],
    "dementia-with-lewy-bodies": ["Lewy body dementia", "dementia with Lewy bodies"],
    "multiple-system-atrophy": ["multiple system atrophy"],
    "spinocerebellar-ataxia": ["spinocerebellar ataxia"],
}

SOURCES = {
    "europepmc": EuropePmcAdapter,
    "openalex": OpenAlexAdapter,
    "core": CoreAdapter,
    "pubmed": PubMedAdapter,
    "preprints": PreprintsAdapter,
}

DA_RE = re.compile(
    r"data (?:are|is) available|available (?:at|from|in|upon request)|deposited (?:in|at|with)|"
    r"accession (?:number|no)|data availability", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_records(
  source TEXT NOT NULL, source_id TEXT NOT NULL, theme TEXT NOT NULL,
  doi TEXT DEFAULT '', pmid TEXT DEFAULT '', pmcid TEXT DEFAULT '',
  title TEXT DEFAULT '', abstract TEXT DEFAULT '', year INTEGER,
  journal TEXT DEFAULT '', authors TEXT DEFAULT '[]',
  is_preprint INTEGER DEFAULT 0, is_oa INTEGER DEFAULT 0,
  oa_url TEXT DEFAULT '', landing_url TEXT DEFAULT '', license TEXT DEFAULT '',
  fetched_at TEXT DEFAULT '',
  PRIMARY KEY (source, source_id, theme));
CREATE TABLE IF NOT EXISTS merged_papers(
  gid TEXT PRIMARY KEY, doi TEXT, pmid TEXT, pmcid TEXT, title TEXT, abstract TEXT,
  year INTEGER, journal TEXT, sources TEXT, n_sources INTEGER,
  is_preprint INTEGER, is_oa INTEGER, themes TEXT,
  signal INTEGER, da_phrase INTEGER, new_to_db INTEGER);
CREATE TABLE IF NOT EXISTS merge_log(
  gid TEXT PRIMARY KEY, method TEXT, n INTEGER, members TEXT);
CREATE TABLE IF NOT EXISTS run_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, theme TEXT,
  fetched INTEGER, created_at TEXT);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_db() -> sqlite3.Connection:
    PILOT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(PILOT_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def count_source_theme(conn, source: str, theme: str) -> int:
    return conn.execute(
        "SELECT COUNT(DISTINCT source_id) FROM raw_records WHERE source=? AND theme=?",
        (source, theme)).fetchone()[0]


def count_source(conn, source: str) -> int:
    return conn.execute(
        "SELECT COUNT(DISTINCT source_id) FROM raw_records WHERE source=?",
        (source,)).fetchone()[0]


def load_seen(conn, source: str) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT source_id FROM raw_records WHERE source=?", (source,))}


def insert_papers(conn, theme: str, papers) -> int:
    n = 0
    for p in papers:
        row = p.to_row()
        cur = conn.execute(
            """INSERT OR IGNORE INTO raw_records(
                 source, source_id, theme, doi, pmid, pmcid, title, abstract, year,
                 journal, authors, is_preprint, is_oa, oa_url, landing_url, license,
                 fetched_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["source"], row["source_id"], theme, row["doi"], row["pmid"],
             row["pmcid"], row["title"], row["abstract"], row["year"], row["journal"],
             row["authors"], int(row["is_preprint"]), int(row["is_oa"]),
             row["oa_url"], row["landing_url"], row["license"], utcnow()))
        n += cur.rowcount
    conn.commit()
    return n


def run_source(conn, name: str, per_theme: int, per_source: int) -> None:
    cls = SOURCES[name]
    adapter = cls()
    seen = load_seen(conn, name)
    print(f"[{name}] start (already seen: {len(seen)})")
    for theme in THEMES:
        have = count_source_theme(conn, name, theme)
        want = per_theme - have
        if want <= 0:
            continue
        papers = adapter.search(theme, THEME_QUERIES[theme], want, seen)
        added = insert_papers(conn, theme, papers)
        conn.execute("INSERT INTO run_log(source, theme, fetched, created_at) "
                     "VALUES(?,?,?,?)", (name, theme, added, utcnow()))
        conn.commit()
        print(f"  [{name}] {theme}: +{added} (have {count_source_theme(conn, name, theme)}/{per_theme})")
    total = count_source(conn, name)
    for theme in REDISTRIBUTE:
        deficit = per_source - total
        if deficit <= 0:
            break
        papers = adapter.search(theme, THEME_QUERIES[theme], deficit, seen)
        added = insert_papers(conn, theme, papers)
        total = count_source(conn, name)
        print(f"  [{name}] redistribute {theme}: +{added} (total {total}/{per_source})")
    print(f"[{name}] done: {count_source(conn, name)} unique papers")


def recompute(conn) -> dict:
    rows = [dict(r) for r in conn.execute(
        "SELECT source, source_id, theme, doi, pmid, pmcid, title, abstract, year, "
        "journal, authors, is_preprint, is_oa FROM raw_records")]
    agg: dict[tuple, dict] = {}
    for r in rows:
        key = (r["source"], r["source_id"])
        rec = agg.get(key)
        if rec is None:
            rec = dict(r)
            rec["themes"] = []
            agg[key] = rec
        rec["themes"].append(r["theme"])
    records = list(agg.values())
    result = merge_records(records)
    papers = result["papers"]
    main_db = cfg.get("db_path")
    new_count = check_new_to_db(papers, main_db)
    for p in papers:
        text = f"{p.get('title') or ''} {p.get('abstract') or ''}"
        p["signal"] = 1 if find_identifiers(text) else 0
        p["da_phrase"] = 1 if DA_RE.search(text) else 0
    conn.execute("DELETE FROM merged_papers")
    conn.execute("DELETE FROM merge_log")
    conn.executemany(
        """INSERT INTO merged_papers(gid, doi, pmid, pmcid, title, abstract, year,
             journal, sources, n_sources, is_preprint, is_oa, themes, signal,
             da_phrase, new_to_db)
           VALUES(:gid,:doi,:pmid,:pmcid,:title,:abstract,:year,:journal,:sources,
             :n_sources,:is_preprint,:is_oa,:themes,:signal,:da_phrase,:new_to_db)""",
        papers)
    conn.executemany(
        "INSERT INTO merge_log(gid, method, n, members) VALUES(:gid,:method,:n,:members)",
        result["merge_log"])
    conn.commit()
    result["new_to_db"] = new_count
    return result


def metrics(conn) -> dict:
    per_source = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(DISTINCT source_id) n FROM raw_records GROUP BY source")}
    per_theme = {(r["source"], r["theme"]): r["n"] for r in conn.execute(
        "SELECT source, theme, COUNT(DISTINCT source_id) n FROM raw_records "
        "GROUP BY source, theme")}
    merged = [dict(r) for r in conn.execute("SELECT * FROM merged_papers")]
    logs = [dict(r) for r in conn.execute("SELECT * FROM merge_log")]
    pairs: dict[str, int] = {}
    unique: dict[str, int] = {}
    for p in merged:
        srcs = json.loads(p["sources"])
        if len(srcs) == 1:
            unique[srcs[0]] = unique.get(srcs[0], 0) + 1
        for i in range(len(srcs)):
            for j in range(i + 1, len(srcs)):
                key = "+".join(sorted((srcs[i], srcs[j])))
                pairs[key] = pairs.get(key, 0) + 1
    new_papers = [p for p in merged if p["new_to_db"]]
    sig_new = [p for p in new_papers if p["signal"]]
    da_new = [p for p in new_papers if p["da_phrase"]]
    methods: dict[str, int] = {}
    for lg in logs:
        methods[lg["method"]] = methods.get(lg["method"], 0) + 1
    # 元数据完整率
    completeness: dict[str, dict] = {}
    for r in conn.execute(
            "SELECT source, COUNT(DISTINCT source_id) n, "
            "SUM(CASE WHEN abstract != '' THEN 1 ELSE 0 END) has_abs, "
            "SUM(CASE WHEN doi != '' THEN 1 ELSE 0 END) has_doi, "
            "SUM(CASE WHEN is_oa = 1 THEN 1 ELSE 0 END) has_oa "
            "FROM raw_records GROUP BY source"):
        n = max(1, r["n"])
        completeness[r["source"]] = {
            "abstract_pct": round(100 * r["has_abs"] / n, 1),
            "doi_pct": round(100 * r["has_doi"] / n, 1),
            "oa_pct": round(100 * r["has_oa"] / n, 1),
        }
    openalex_requests = len(list((CACHE_DIR / "openalex").glob("*.json"))) \
        if (CACHE_DIR / "openalex").exists() else 0
    return {
        "per_source": per_source,
        "per_theme": {f"{s}|{t}": n for (s, t), n in per_theme.items()},
        "merged_total": len(merged),
        "multi_source": sum(1 for p in merged if p["n_sources"] >= 2),
        "unique_per_source": unique,
        "pair_overlap": pairs,
        "new_to_db": len(new_papers),
        "new_with_signal": len(sig_new),
        "new_with_da_phrase": len(da_new),
        "signal_total": sum(1 for p in merged if p["signal"]),
        "merge_methods": methods,
        "completeness": completeness,
        "openalex_requests": openalex_requests,
        "openalex_cost_usd": round(openalex_requests * 0.001, 3),
    }


def write_qc_sample(conn) -> int:
    logs = [dict(r) for r in conn.execute(
        "SELECT * FROM merge_log WHERE n >= 2 ORDER BY method, gid")]
    title_logs = [lg for lg in logs if lg["method"] == "title"][:50]
    hard_logs = [lg for lg in logs if lg["method"] != "title"][:30]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "qc-sample.csv"
    import csv
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["gid", "method", "n", "member_source", "member_id",
                    "member_title", "member_year", "member_doi"])
        for lg in title_logs + hard_logs:
            for m in json.loads(lg["members"]):
                w.writerow([lg["gid"], lg["method"], lg["n"], m.get("source"),
                            m.get("source_id"), (m.get("title") or "")[:100],
                            m.get("year"), m.get("doi")])
    return len(title_logs) + len(hard_logs)


def write_report(conn, m: dict, qc_groups: int) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    srcs = sorted(m["per_source"])
    lines = [
        "# Multi-source pilot report",
        "",
        f"- date: {utcnow()} · sources: {', '.join(srcs)}",
        f"- raw unique papers: {sum(m['per_source'].values())} · merged: "
        f"{m['merged_total']} · multi-source: {m['multi_source']}",
        f"- new to BioAgentDB (v1.2): **{m['new_to_db']}** · of which deterministic "
        f"data signal: **{m['new_with_signal']}** · DA-phrase: {m['new_with_da_phrase']}",
        f"- OpenAlex requests: {m['openalex_requests']} (≈ ${m['openalex_cost_usd']})",
        "",
        "## Per source",
        "",
        "| source | unique papers | abstract % | DOI % | OA % |",
        "|---|---|---|---|---|",
    ]
    for s in srcs:
        c = m["completeness"].get(s, {})
        lines.append(f"| {s} | {m['per_source'].get(s, 0)} | {c.get('abstract_pct', '')} "
                     f"| {c.get('doi_pct', '')} | {c.get('oa_pct', '')} |")
    lines += ["", "## Pairwise overlap (merged papers)", "",
              "| pair | papers |", "|---|---|"]
    for k in sorted(m["pair_overlap"]):
        lines.append(f"| {k} | {m['pair_overlap'][k]} |")
    lines += ["", "## Unique to one source", "", "| source | papers |", "|---|---|"]
    for s in srcs:
        lines.append(f"| {s} | {m['unique_per_source'].get(s, 0)} |")
    lines += ["", "## Merge methods", "", "| method | groups |", "|---|---|"]
    for k in sorted(m["merge_methods"]):
        lines.append(f"| {k} | {m['merge_methods'][k]} |")
    lines += ["", f"- QC sample: `docs/expansion/qc-sample.csv` ({qc_groups} merge groups; "
              "人工抽检待做：假合并率 <2% 为硬线)", ""]
    (OUT_DIR / "pilot-report.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT_DIR / "pilot-report.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="europepmc,openalex,core,pubmed,preprints")
    ap.add_argument("--per-theme", type=int, default=100)
    ap.add_argument("--per-source", type=int, default=1000)
    ap.add_argument("--only-metrics", action="store_true")
    args = ap.parse_args()

    conn = open_db()
    if not args.only_metrics:
        for name in [s.strip() for s in args.sources.split(",") if s.strip()]:
            if name not in SOURCES:
                print(f"unknown source: {name}")
                continue
            try:
                run_source(conn, name, args.per_theme, args.per_source)
            except Exception as exc:  # noqa: BLE001
                print(f"[{name}] FAILED: {type(exc).__name__}: {exc}")

    result = recompute(conn)
    m = metrics(conn)
    qc_groups = write_qc_sample(conn)
    write_report(conn, m, qc_groups)
    print("\n=== metrics ===")
    print(json.dumps(m, ensure_ascii=False, indent=2)[:2000])
    conn.close()


if __name__ == "__main__":
    main()
