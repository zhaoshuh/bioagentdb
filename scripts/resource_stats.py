"""资源统计（论文 A 的核心表）：对任意 sqlite 目录库产出统计报告。

用法:
  python scripts/resource_stats.py [--db PATH] [--out DIR]
  # 默认分析 cfg.db_path；也可指向快照里的 bioagentdb.sqlite3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def analyze(db_path: Path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    q = lambda sql, a=(): [dict(r) for r in conn.execute(sql, a).fetchall()]  # noqa: E731
    one = lambda sql, a=(): conn.execute(sql, a).fetchone()[0]  # noqa: E731
    try:
        totals = {
            "papers": one("SELECT COUNT(*) FROM papers"),
            "papers_mined": one("SELECT COUNT(*) FROM papers WHERE mined_status IN "
                                "('mined','no_accessions','off_topic')"),
            "papers_with_data": one("SELECT COUNT(DISTINCT paper_id) FROM dataset_papers"),
            "datasets": one("SELECT COUNT(*) FROM datasets"),
            "datasets_verified": one("SELECT COUNT(*) FROM datasets WHERE status IN "
                                     "('verified','paper_verified')"),
            "datasets_rejected": one("SELECT COUNT(*) FROM datasets WHERE status='rejected'"),
            "evidence": one("SELECT COUNT(*) FROM evidence"),
            "themes": one("SELECT COUNT(*) FROM themes"),
        }
        per_theme = q("""
            SELECT t.slug, t.name, t.priority,
                   (SELECT COUNT(DISTINCT pt.paper_id) FROM paper_themes pt
                      JOIN dataset_papers dp ON dp.paper_id=pt.paper_id
                     WHERE pt.theme=t.slug) AS papers_with_data,
                   (SELECT COUNT(DISTINCT dp.dataset_id) FROM paper_themes pt
                      JOIN dataset_papers dp ON dp.paper_id=pt.paper_id
                     WHERE pt.theme=t.slug) AS datasets
            FROM themes t ORDER BY t.priority""")
        multi = {
            "papers_in_multi_theme": one("SELECT COUNT(*) FROM (SELECT paper_id FROM paper_themes "
                                         "GROUP BY paper_id HAVING COUNT(*)>1)"),
            "papers_with_data_in_multi_theme": one(
                "SELECT COUNT(*) FROM (SELECT pt.paper_id FROM paper_themes pt "
                "JOIN dataset_papers dp ON dp.paper_id=pt.paper_id GROUP BY pt.paper_id "
                "HAVING COUNT(DISTINCT pt.theme)>1)"),
        }
        mods = Counter()
        for r in conn.execute("SELECT modalities, modality FROM datasets"):
            try:
                arr = json.loads(r["modalities"]) if r["modalities"] else []
            except Exception:  # noqa: BLE001
                arr = []
            if not arr and r["modality"]:
                arr = [r["modality"]]
            for m in arr:
                mods[m] += 1
        repos = {r["repository"]: r["n"] for r in conn.execute(
            "SELECT repository, COUNT(DISTINCT dataset_id) n FROM dataset_links "
            "GROUP BY repository ORDER BY n DESC")}
        years = {str(r["year"]): r["n"] for r in conn.execute(
            "SELECT p.year, COUNT(DISTINCT dp.paper_id) n FROM dataset_papers dp "
            "JOIN papers p ON p.id=dp.paper_id WHERE p.year IS NOT NULL "
            "GROUP BY p.year ORDER BY p.year DESC")}
        # T1 引文锚定：主登记号是否出现在该数据集的任一证据引文中
        rows = conn.execute("""
            SELECT d.id, l.accession,
              (SELECT COUNT(*) FROM evidence e WHERE e.dataset_id=d.id
                 AND UPPER(e.quote) LIKE '%'||UPPER(l.accession)||'%') n
            FROM datasets d JOIN dataset_links l ON l.dataset_id=d.id AND l.is_primary=1
            WHERE l.accession != ''""").fetchall()
        grounded = sum(1 for r in rows if r["n"] > 0)
        # T2 可达性
        reach = {r["k"]: r["n"] for r in conn.execute(
            "SELECT CASE WHEN reachable=1 THEN 'reachable' WHEN reachable=0 THEN 'unreachable' "
            "ELSE 'unknown' END k, COUNT(*) n FROM dataset_links "
            "WHERE link_type='portal' GROUP BY k")}
        zones = {r["zone"]: r["n"] for r in conn.execute(
            "SELECT zone, COUNT(*) n FROM evidence GROUP BY zone")}
        return {
            "db": str(db_path),
            "totals": totals,
            "per_theme": per_theme,
            "multi_theme": multi,
            "modalities": dict(mods.most_common()),
            "repositories": repos,
            "years": years,
            "grounding": {"datasets_with_primary": len(rows), "grounded": grounded,
                          "t1_rate": round(grounded / max(1, len(rows)), 4)},
            "reachability": reach,
            "evidence_zones": zones,
        }
    finally:
        conn.close()


def to_markdown(s: dict) -> str:
    t = s["totals"]
    lines = ["# BioAgentDB resource statistics", "",
             f"- db: `{s['db']}`", "",
             "## Totals", "",
             f"- papers: {t['papers']} · mined: {t['papers_mined']} · "
             f"with data: {t['papers_with_data']}",
             f"- datasets: {t['datasets']} · verified: {t['datasets_verified']} · "
             f"rejected: {t['datasets_rejected']}",
             f"- evidence: {t['evidence']} · themes: {t['themes']}",
             f"- T1 grounding rate: {s['grounding']['t1_rate']:.3f} "
             f"({s['grounding']['grounded']}/{s['grounding']['datasets_with_primary']})",
             f"- multi-theme papers: {s['multi_theme']['papers_in_multi_theme']} "
             f"(with data: {s['multi_theme']['papers_with_data_in_multi_theme']})",
             "", "## Per theme", "",
             "| priority | slug | name | papers w/ data | datasets |",
             "|---|---|---|---|---|"]
    for r in s["per_theme"]:
        lines.append(f"| {r['priority']} | {r['slug']} | {r['name']} | "
                     f"{r['papers_with_data']} | {r['datasets']} |")
    lines += ["", "## Modalities", ""]
    lines += [f"- {k}: {v}" for k, v in s["modalities"].items()]
    lines += ["", "## Repositories", ""]
    lines += [f"- {k or 'n/a'}: {v}" for k, v in s["repositories"].items()]
    lines += ["", "## Reachability (portal links)", ""]
    lines += [f"- {k}: {v}" for k, v in s["reachability"].items()]
    lines += ["", "## Evidence zones", ""]
    lines += [f"- {k}: {v}" for k, v in s["evidence_zones"].items()]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.db:
        db_path = Path(args.db)
    else:
        from app.core.config import cfg
        db_path = Path(cfg.db_path)
    s = analyze(db_path)
    out = Path(args.out) if args.out else db_path.parent / "resource-stats"
    out.mkdir(parents=True, exist_ok=True)
    (out / "resource-stats.json").write_text(
        json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "resource-stats.md").write_text(to_markdown(s), encoding="utf-8")
    print(f"saved {out / 'resource-stats.md'}")
    print(to_markdown(s)[:1200])


if __name__ == "__main__":
    main()
