"""试点报告生成器：对每病试点结果输出 6 项指标 + 明细表。

用法: python scripts/pilot_report.py [--theme alzheimers-disease|als|parkinsons-disease|all]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import db, init_db

THEMES = ["alzheimers-disease", "als", "parkinsons-disease"]


def report_one(theme: str) -> dict:
    out = {"theme": theme}
    with db() as conn:
        # 完成率
        out["processed"] = {
            row["mined_status"]: row["n"]
            for row in conn.execute(
                "SELECT mined_status, COUNT(*) n FROM papers WHERE theme=? "
                "AND mined_status != 'pending' AND mined_status NOT LIKE 'task_%' "
                "GROUP BY mined_status", (theme,)).fetchall()
        }
        out["pending"] = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE theme=? AND mined_status IN ('pending','fetched','claimed')",
            (theme,)).fetchone()[0]
        # 并发重单：同一论文被审计 >1 次
        out["dup_audits"] = conn.execute(
            """SELECT COUNT(*) FROM (SELECT paper_id FROM paper_audits a JOIN papers p
               ON p.id=a.paper_id WHERE p.theme=? AND a.task_id='' GROUP BY paper_id
               HAVING COUNT(*)>1)""",
            (theme,)).fetchone()[0]
        # 记录产出
        out["records"] = {
            row["status"]: row["n"]
            for row in conn.execute(
                """SELECT d.status, COUNT(DISTINCT d.id) n FROM datasets d
                   JOIN dataset_papers dp ON dp.dataset_id=d.id
                   JOIN papers p ON p.id=dp.paper_id WHERE p.theme=?
                   GROUP BY d.status""", (theme,)).fetchall()
        }
        out["corrections"] = conn.execute(
            """SELECT COALESCE(SUM(json_extract(records, '$.corrections')), 0)
               FROM paper_audits WHERE theme=? AND verdict='mined' AND task_id=''""",
            (theme,)).fetchone()[0]
        # 审计闭环
        out["audits"] = conn.execute(
            "SELECT COUNT(*) FROM paper_audits WHERE theme=? AND verdict='pending' "
            "AND task_id=''", (theme,)).fetchone()[0]
        # 链接健康（T2）
        out["link_health"] = {
            row["reachable"]: row["n"] for row in conn.execute(
                """SELECT l.reachable, COUNT(*) n FROM dataset_links l
                   JOIN datasets d ON d.id=l.dataset_id
                   JOIN dataset_papers dp ON dp.dataset_id=d.id
                   JOIN papers p ON p.id=dp.paper_id WHERE p.theme=?
                   GROUP BY l.reachable""", (theme,)).fetchall()
        }
        # 矿机稳定性：进程重启次数（按主题计数；任务矿机不计，L-0221）
        out["process_starts"] = conn.execute(
            "SELECT COUNT(*) FROM miner_processes WHERE theme=? AND task_id=''",
            (theme,)).fetchone()[0]
        # 抽样记录明细（供人工复核）
        rows = conn.execute(
            """SELECT DISTINCT d.id, d.title, d.status, d.confidence, l.repo, l.access,
                      l.ourl, l.reachable, e.zone, substr(e.quote,1,140) quote, p.pmid
               FROM datasets d
               JOIN (SELECT dataset_id, repository repo, accession access, url ourl, reachable
                     FROM dataset_links WHERE is_primary=1) l ON l.dataset_id=d.id
               JOIN dataset_papers dp ON dp.dataset_id=d.id
               JOIN papers p ON p.id=dp.paper_id
               LEFT JOIN evidence e ON e.dataset_id=d.id
               WHERE p.theme=?
               ORDER BY d.updated_at DESC LIMIT 30""", (theme,)).fetchall()
        out["sample_records"] = [dict(r) for r in rows]
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", default="all",
                        choices=THEMES + ["all"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    init_db()
    themes = THEMES if args.theme == "all" else [args.theme]
    report = {}
    for th in themes:
        report[th] = report_one(th)
        r = report[th]
        if not args.json:
            print("=" * 60)
            print(f"[{th}]")
            print("  处理状态:", r["processed"], "| 池中待挖:", r["pending"])
            print("  重复审计(并发重单):", r["dup_audits"])
            print("  记录:", dict(r["records"]), "| 对抗修正次数:", r["corrections"])
            print("  审计待决:", r["audits"], "| 链接健康:", r["link_health"])
            print("  进程启动次数(重启检测):", r["process_starts"])
            print("  --- 最近记录抽样（人工复核用）---")
            for s in r["sample_records"][:10]:
                print(f"   [{s['status']}] {s['access']}@{s['repo']} conf={s['confidence']} "
                      f"link_ok={s['reachable']} zone={s['zone']} pmid={s['pmid']}")
                print(f"      {s['title'][:80]}")
                print(f"      quote: {(s['quote'] or '')[:120]}")
                print(f"      url: {s['ourl'][:100]}")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()