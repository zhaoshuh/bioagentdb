"""下游效用实验：目录（catalog）在"疾病数据集检索"上是否带来提升。

对每个问题：
  A) 无目录：直接问 LLM（凭记忆列登记号）
  B) 有目录：把目录里匹配的记录喂给 LLM，要求列出登记号
金标准 = 目录中匹配该问题的登记号集合。
指标：登记号级 P/R/F1；幻觉率（预测不在全局目录里的比例）。

用法: python scripts/eval_downstream.py [--n 30] [--db PATH] [--out data/eval]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.harvest.llm import chat  # noqa: E402
from app.harvest.themes import get_theme  # noqa: E402

ACC_RE = re.compile(
    r"\b(GSE\d{3,7}|PXD\d{5,7}|PRJNA\d{4,9}|PRJEB\d{4,9}|SRP\d{5,8}|MTBLS\d{3,6}|"
    r"phs\d{6}(?:\.v\d+\.p\d+)?|EGA[SDN]\d{8,14}|E-[A-Z]{4}-\d{3,5}|CRA\d{6}|JPST\d{6})\b",
    re.I)


def canon(a: str) -> str:
    a = (a or "").strip().lower()
    return re.sub(r"\.v\d+\.p\d+$", "", a)


def build_questions(conn, n: int) -> list[dict]:
    """三种问法（modality / organism / tissue）× 主题；nds≤8 保持题目规模可控。"""
    qs = []
    for facet in ("modality", "organism", "tissue"):
        rows = conn.execute(f"""
            SELECT pt.theme AS theme, d.{facet} AS value,
                   group_concat(DISTINCT l.accession) AS accs,
                   count(DISTINCT d.id) AS nds
            FROM paper_themes pt
            JOIN dataset_papers dp ON dp.paper_id = pt.paper_id
            JOIN datasets d ON d.id = dp.dataset_id
            JOIN dataset_links l ON l.dataset_id = d.id AND l.is_primary = 1
            WHERE d.status IN ('verified','paper_verified') AND l.accession != ''
              AND d.{facet} != ''
            GROUP BY pt.theme, d.{facet}
            HAVING nds BETWEEN 1 AND 8
            ORDER BY nds DESC""").fetchall()
        for r in rows:
            accs = sorted({canon(a) for a in (r["accs"] or "").split(",") if a})
            if not accs:
                continue
            th = get_theme(r["theme"]) or {}
            qs.append({"theme": r["theme"], "theme_name": th.get("name", r["theme"]),
                       "facet": facet, "value": r["value"], "gold": accs})
            if len(qs) >= n:
                return qs
    return qs


def ask(question: str, context: str) -> str:
    sys_msg = ("You are a biomedical data assistant. List public dataset accession numbers. "
               "Output ONLY a comma-separated list of accessions. If unsure, output nothing.")
    user = question if not context else f"{question}\n\nCatalog records:\n{context}\n\nList their accessions:"
    try:
        out = chat([{"role": "system", "content": sys_msg},
                    {"role": "user", "content": user}], temperature=0.0, timeout=90)
        return out.get("content") or ""
    except Exception as exc:  # noqa: BLE001
        return f"__ERR__ {exc}"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", default=str(root / "data" / "eval"))
    args = ap.parse_args()
    if args.db:
        db_path = Path(args.db)
    else:
        from app.core.config import cfg
        db_path = Path(cfg.db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        global_accs = {canon(r[0]) for r in conn.execute(
            "SELECT DISTINCT accession FROM dataset_links WHERE accession != ''")}
        questions = build_questions(conn, args.n)

        def context_for(q):
            rows = conn.execute(f"""
                SELECT DISTINCT d.title AS title, l.accession AS acc
                FROM paper_themes pt JOIN dataset_papers dp ON dp.paper_id=pt.paper_id
                JOIN datasets d ON d.id=dp.dataset_id
                JOIN dataset_links l ON l.dataset_id=d.id AND l.is_primary=1
                WHERE pt.theme=? AND d.{q['facet']}=? AND l.accession!='' LIMIT 20""",
                (q["theme"], q["value"])).fetchall()
            return "\n".join(f"- {r['title'][:90]} [{r['acc']}]" for r in rows)

        results = []
        agg = {"no_ctx": {"tp": 0, "fp": 0, "fn": 0, "hall": 0},
               "ctx": {"tp": 0, "fp": 0, "fn": 0, "hall": 0}}
        for q in questions:
            qtext = (f"In BioAgentDB, which dataset accessions exist for {q['theme_name']} "
                     f"with {q['facet']} {q['value']}?")
            gold = set(q["gold"])
            qres = {"question": qtext, "theme": q["theme"], "facet": q["facet"],
                    "value": q["value"], "gold": q["gold"], "conditions": {}}
            for cond, ctx in (("no_ctx", ""), ("ctx", context_for(q))):
                ans = ask(qtext, ctx)
                pred = {canon(a) for a in ACC_RE.findall(ans)}
                tp = len(pred & gold)
                fp = len(pred - gold)
                fn = len(gold - pred)
                hall = len([a for a in pred if a not in global_accs])
                agg[cond]["tp"] += tp
                agg[cond]["fp"] += fp
                agg[cond]["fn"] += fn
                agg[cond]["hall"] += hall
                qres["conditions"][cond] = {"pred": sorted(pred), "tp": tp, "fp": fp,
                                            "fn": fn, "hall": hall}
            results.append(qres)

        def prf(a):
            p = a["tp"] / max(1, a["tp"] + a["fp"])
            r = a["tp"] / max(1, a["tp"] + a["fn"])
            f = 2 * p * r / max(1e-9, p + r)
            return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4),
                    **a}

        import random as _random

        def boot_f1_ci(cond: str) -> list[float]:
            rows = [r["conditions"][cond] for r in results if cond in r.get("conditions", {})]
            if not rows:
                return []
            rng = _random.Random(20261013)
            outs = []
            for _ in range(10000):
                tp = fp = fn = 0
                for _ in range(len(rows)):
                    s = rng.choice(rows)
                    tp += s["tp"]
                    fp += s["fp"]
                    fn += s["fn"]
                p = tp / max(1, tp + fp)
                r = tp / max(1, tp + fn)
                outs.append(2 * p * r / max(1e-9, p + r))
            outs.sort()
            return [round(outs[int(0.025 * (len(outs) - 1))], 4),
                    round(outs[int(0.975 * (len(outs) - 1))], 4)]

        report = {"provisional": True, "n_questions": len(questions),
                  "conditions": {"no_catalog": prf(agg["no_ctx"]), "with_catalog": prf(agg["ctx"])},
                  "f1_ci95": {"no_catalog": boot_f1_ci("no_ctx"),
                              "with_catalog": boot_f1_ci("ctx")},
                  "per_question": results,
                  "global_catalog_accessions": len(global_accs)}
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "downstream-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        md = ["# Downstream utility (provisional)", "",
              f"- questions: {len(questions)}",
              f"- **no catalog**  : P/R/F1 = {prf(agg['no_ctx'])['precision']} / "
              f"{prf(agg['no_ctx'])['recall']} / {prf(agg['no_ctx'])['f1']} "
              f"(hallucinated accs={agg['no_ctx']['hall']})",
              f"- **with catalog**: P/R/F1 = {prf(agg['ctx'])['precision']} / "
              f"{prf(agg['ctx'])['recall']} / {prf(agg['ctx'])['f1']} "
              f"(hallucinated accs={agg['ctx']['hall']})"]
        (out / "downstream-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        print("\n".join(md))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
